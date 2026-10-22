"""Instagram Reels in the Amber look.

1080x1920 (9:16) H.264 MP4s at 30 fps, drawn frame by frame with Pillow and
encoded with ffmpeg: the system ffmpeg if there is one, otherwise the binary
that ships with the imageio-ffmpeg package, so Clay's Mac and CI both work.

A reel is a list of beats, each a dict with a "beat" key:

  hook     {text, kicker?, sub?, secs?, circle?}  The first frame. On screen from frame 0
           with no fade in, because the first second decides whether anyone stays.
           The breathing circle starts moving under the text from frame 0 (a still
           first frame loses people); circle: false keeps just the pulsing glow.
  text     {headline, body?, secs?}  Text eases up into place over the glow.
  breathe  {pattern: calm|box|478|sigh, rounds?, title?, note?}  A breathe-along
           circle. The note appears during the last round.
  phone    {screen, headline, body?, crop?, secs?}  An app screen rising into frame.
  end      {closing: prelaunch|launch|article, tool?, secs?}  The closing card.

Instagram draws its own UI over roughly the bottom quarter (caption, audio,
buttons down the right side) and the top 10%, so all text stays inside
X0..X1, Y0..Y1. The first frame doubles as the cover image.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter

from . import social_images as simg

W, H, FPS = 1080, 1920, 30
X0, X1 = 96, 960            # text column; the right edge leaves room for Instagram's buttons
Y0, Y1 = 250, 1480          # below the top bar, above the caption overlay
XFADE = 0.2                 # seconds of crossfade between beats
RENDER_VERSION = 2          # bump to force every cached reel to re-render

BASE, TEXT, TEXT_SOFT, TEXT_2 = simg.BASE, simg.TEXT, simg.TEXT_SOFT, simg.TEXT_2
AMBER, LABEL = simg.AMBER, simg.LABEL

# (label, seconds, target fullness 0..1; None = hold)
PATTERNS = {
    "calm": [("Breathe in", 4, 1.0), ("Breathe out", 6, 0.0)],
    "box": [("Breathe in", 4, 1.0), ("Hold", 4, None), ("Breathe out", 4, 0.0), ("Hold", 4, None)],
    "478": [("Breathe in", 4, 1.0), ("Hold", 7, None), ("Breathe out", 8, 0.0)],
    "sigh": [("Breathe in", 2, 0.72), ("Top it up", 1, 1.0), ("Long sigh out", 6, 0.0)],
}
DEFAULT_ROUNDS = {"calm": 3, "box": 2, "478": 2, "sigh": 3}
CLOSINGS = {
    "prelaunch": (["Coming soon", "to iPhone."], ["Join the launch list: link in bio."]),
    "launch": (["Now on", "iPhone."], ["Download DeRot: link in bio.", "No account needed."]),
    "article": (["Full guide,", "free."], ["Link in bio, or derot.org/blog."]),
}
BEATS = ("hook", "text", "breathe", "phone", "end")
# Most viewers leave in the first few seconds, so reels stay short.
TALK_MAX = 22               # seconds, reels without a breathe-along
BREATHE_MAX = 40            # seconds, any reel


# ------------------------------------------------------------ timing
def _words(*texts: str) -> int:
    return sum(len((t or "").split()) for t in texts)


def beat_secs(b: dict) -> float:
    """How long a beat stays on screen. Text beats get reading time from word count."""
    kind = b.get("beat")
    if kind == "breathe":
        pattern = _pattern(b)
        return float(sum(p[1] for p in pattern) * int(b.get("rounds", DEFAULT_ROUNDS.get(b.get("pattern", "calm"), 3))))
    if b.get("secs"):
        return float(b["secs"])
    if kind == "hook":
        return 2.4
    if kind == "end":
        return 3.0
    n = _words(b.get("headline", ""), b.get("body", ""))
    return max(2.2, min(5.0, 1.2 + 0.27 * n))


def duration(beats: list[dict]) -> float:
    return sum(beat_secs(b) for b in beats)


def _pattern(b: dict) -> list:
    if b.get("phases"):
        return [tuple(p) for p in b["phases"]]
    return PATTERNS[b.get("pattern", "calm")]


def check_beats(beats: list[dict], screens_dir: Path) -> list[str]:
    errors = []
    if not beats:
        return ["reel has no beats"]
    if beats[0].get("beat") != "hook":
        errors.append("reel must open on a hook beat")
    for i, b in enumerate(beats):
        k = b.get("beat")
        if k not in BEATS:
            errors.append(f"reel[{i}]: unknown beat '{k}'")
            continue
        if k == "breathe" and not b.get("phases") and b.get("pattern", "calm") not in PATTERNS:
            errors.append(f"reel[{i}]: unknown breathing pattern '{b.get('pattern')}'")
        if k == "phone" and not (screens_dir / f"{b.get('screen')}.jpg").exists():
            errors.append(f"reel[{i}]: unknown screen '{b.get('screen')}'")
        if k == "end" and b.get("closing", "prelaunch") not in CLOSINGS:
            errors.append(f"reel[{i}]: unknown closing '{b.get('closing')}'")
        if k == "hook" and len(b.get("text", "")) > 70:
            errors.append(f"reel[{i}]: hook is {len(b['text'])} characters; keep it under 70 so it reads in a second")
    total = duration(beats) if not errors else 0
    if errors:
        return errors
    if total < 5:
        errors.append(f"reel is {total:.0f}s (Instagram needs at least 3, aim for 7+)")
    has_breathe = any(b.get("beat") == "breathe" for b in beats)
    if not has_breathe and total > TALK_MAX:
        errors.append(f"reel is {total:.0f}s with no breathe-along; keep it under {TALK_MAX}s")
    if total > BREATHE_MAX:
        errors.append(f"reel is {total:.0f}s; keep it under {BREATHE_MAX}s (cut a round or a text beat)")
    return errors


def beat_text(b: dict) -> list[tuple[str, str]]:
    """Every user-visible string in a beat, for the copy-rule checks."""
    return [(k, b[k]) for k in ("text", "kicker", "sub", "headline", "body", "title", "note") if b.get(k)]


def spec_hash(beats: list[dict], audio: str = "", volume: float = 1.0) -> str:
    raw = json.dumps({"v": RENDER_VERSION, "beats": beats, "audio": audio,
                      **({"vol": volume} if audio else {})}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------ drawing helpers
_BG: dict = {}


def _glow() -> Image.Image:
    """Ember glow rising from the top edge, drawn small and scaled up (blur is slow at full size)."""
    if "glow" not in _BG:
        s = 4
        w, h = W // s, H // s
        img = Image.new("RGB", (w, h), BASE)
        g = ImageDraw.Draw(img)
        cx = w // 2
        for r, col in [(900, (58, 23, 10)), (680, (110, 44, 15)), (470, (180, 80, 28)), (270, (232, 118, 46))]:
            r //= s
            g.ellipse([cx - r, -int(r * 1.15), cx + r, int(r * 0.55)], fill=col)
        img = img.filter(ImageFilter.GaussianBlur(150 // s)).resize((W, H), Image.BICUBIC)
        _BG["glow"] = img
        _BG["flat"] = Image.new("RGB", (W, H), BASE)
    return _BG["glow"]


def _bg(strength: float) -> Image.Image:
    key = round(strength, 2)
    if key not in _BG:
        _BG[key] = Image.blend(Image.new("RGB", (W, H), BASE), _glow(), key)
    return _BG[key].copy()


def _wordmark(d: ImageDraw.ImageDraw, y: int = Y1 - 40) -> None:
    f = simg._serif(44)
    d.text((X0, y), "DeRot", font=f, fill=TEXT)
    uf = simg._sans(28, 500)
    d.text((X1 - d.textlength("derot.org", font=uf), y + 12), "derot.org", font=uf, fill=TEXT_2)


def _text_block(d, headline: str, body: str, top: Optional[int], hsizes, bsizes,
                h_max: int = 560, b_max: int = 480) -> int:
    width = X1 - X0
    hf, hl, hs = simg._fit(d, headline, simg._serif, hsizes, width, h_max, 1.1)
    bl = []
    if body:
        bf, bl, bs = simg._fit(d, body, lambda s: simg._sans(s, 400), bsizes, width, b_max, 1.38)
    block = len(hl) * hs * 1.1 + ((56 + len(bl) * bs * 1.38) if bl else 0)
    y = top if top is not None else int(Y0 + (Y1 - Y0 - block) / 2)
    y = simg._draw_lines(d, hl, hf, hs, X0, y, TEXT, 1.1)
    if bl:
        y += 26
        d.line([(X0, y), (X0 + 90, y)], fill=AMBER, width=4)
        y += 30
        y = simg._draw_lines(d, bl, bf, bs, X0, y, TEXT_SOFT, 1.38)
    return y


# ------------------------------------------------------------ beats
# Each builder returns (seconds, frame_fn); frame_fn(t) -> RGB image, t in [0, seconds).
Frame = Callable[[float], Image.Image]


def _hook(b: dict) -> tuple[float, Frame]:
    secs = beat_secs(b)
    circle = b.get("circle", True)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    width = X1 - X0
    kf = simg._sans(32, 600)
    h_max = 560 if circle else 720
    hf, hl, hs = simg._fit(d, b["text"], simg._serif, list(range(150, 70, -4)), width, h_max, 1.08)
    sub_lines = []
    if b.get("sub"):
        sf, sub_lines, ss = simg._fit(d, b["sub"], lambda s: simg._sans(s, 400), list(range(50, 34, -2)), width,
                                      160 if circle else 260, 1.35)
    block = len(hl) * hs * 1.08 + (70 if b.get("kicker") else 0) + ((40 + len(sub_lines) * ss * 1.35) if sub_lines else 0)
    if circle:   # text up top, the circle breathing in below it
        y = int(Y0 + 30)
    else:
        y = int(Y0 + (Y1 - 120 - Y0 - block) / 2)   # sit a little high: the lower screen gets covered
    if b.get("kicker"):
        d.text((X0, y), b["kicker"].upper(), font=kf, fill=LABEL)
        y += 70
    y = simg._draw_lines(d, hl, hf, hs, X0, y, TEXT, 1.08)
    if sub_lines:
        y = simg._draw_lines(d, sub_lines, sf, ss, X0, y + 40, TEXT_SOFT, 1.35)
    lo, hi = _bg(0.8), _bg(1.0)
    cy = max(int(y) + 120 + HOOK_R[1], 1180)

    def frame(t: float) -> Image.Image:
        # one slow inhale across the hook: the glow swells and the circle opens
        p = _ease(t / max(secs, 0.1))
        img = Image.blend(lo, hi, p)
        if circle:
            _draw_circle(img, W // 2, cy, int(HOOK_R[0] + (HOOK_R[1] - HOOK_R[0]) * p), HOOK_R[1])
        img.paste(layer, (0, 0), layer)
        return img

    return secs, frame


HOOK_R = (120, 210)        # hook circle radius, start and end


def _text(b: dict) -> tuple[float, Frame]:
    secs = beat_secs(b)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _text_block(ImageDraw.Draw(layer), b["headline"], b.get("body", ""), None,
                list(range(116, 60, -4)), list(range(52, 34, -2)))
    lo, hi = _bg(0.55), _bg(0.7)
    rise, rise_t = 36, 0.45

    def frame(t: float) -> Image.Image:
        img = Image.blend(lo, hi, 0.5 + 0.5 * math.sin(math.pi * min(1.0, t / max(secs, 0.1))))
        img.paste(layer, (0, int(rise * (1 - _ease(t / rise_t)))), layer)
        return img

    return secs, frame


_CIRCLE: dict = {}


def _draw_circle(img: Image.Image, cx: int, cy: int, r: int, rmax: int) -> None:
    """The breathing circle with its soft glow, drawn onto img in place."""
    pad = 170
    key = rmax
    if key not in _CIRCLE:
        S = 2 * (rmax + pad)
        sprite = Image.new("L", (S, S), 0)
        ImageDraw.Draw(sprite).ellipse([pad, pad, S - pad, S - pad], fill=170)
        _CIRCLE[key] = (S, sprite.filter(ImageFilter.GaussianBlur(70)), Image.new("RGB", (S, S), (200, 96, 30)))
    S, sprite, glow_rgb = _CIRCLE[key]
    gs = max(8, int(S * (r + pad) / (rmax + pad)))
    img.paste(glow_rgb.resize((gs, gs)), (cx - gs // 2, cy - gs // 2), sprite.resize((gs, gs), Image.BILINEAR))
    ss = 2   # supersampled for a clean edge
    size = (2 * r + 16) * ss
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    o = 8 * ss
    ImageDraw.Draw(disc).ellipse([o, o, size - o, size - o], fill=(46, 22, 10, 215), outline=AMBER + (255,), width=6 * ss)
    disc = disc.resize((size // ss, size // ss), Image.LANCZOS)
    img.paste(disc, (cx - disc.width // 2, cy - disc.height // 2), disc)


def _ease(p: float) -> float:
    return (1 - math.cos(math.pi * max(0.0, min(1.0, p)))) / 2


def _breathe(b: dict) -> tuple[float, Frame]:
    pattern = _pattern(b)
    rounds = int(b.get("rounds", DEFAULT_ROUNDS.get(b.get("pattern", "calm"), 3)))
    seq = []           # (start, secs, label, from_level, to_level, round)
    t, level = 0.0, 0.0
    for r in range(rounds):
        for label, secs, target in pattern:
            to = level if target is None else float(target)
            seq.append((t, float(secs), label, level, to, r))
            t += secs
            level = to
    total = t
    cx, cy = W // 2, 900
    rmin, rmax = 150, 330

    def static(strength: float, note: bool) -> Image.Image:
        img = _bg(strength)
        d = ImageDraw.Draw(img)
        if b.get("title"):
            tf, tl, ts = simg._fit(d, b["title"], simg._serif, list(range(76, 48, -4)), X1 - X0, 200, 1.1)
            simg._draw_lines(d, tl, tf, ts, X0, Y0, TEXT, 1.1)
        if note and b.get("note"):
            nf, nl, ns = simg._fit(d, b["note"], lambda s: simg._sans(s, 500), list(range(46, 32, -2)), X1 - X0, 140, 1.35)
            y = cy + rmax + 120
            for ln in nl:
                d.text((W // 2, y), ln, font=nf, fill=TEXT_SOFT, anchor="mt")
                y += int(ns * 1.35)
        return img

    layers = {(s, n): static(s, n) for s in (0.45, 0.9) for n in (False, True)}
    label_f = simg._sans(42, 600)
    count_f = simg._serif(92)
    round_f = simg._sans(28, 600)

    def frame(tt: float) -> Image.Image:
        i = 0
        while i + 1 < len(seq) and seq[i + 1][0] <= tt:
            i += 1
        start, secs, label, a, z, rnd = seq[i]
        p = (tt - start) / secs
        lvl = a + (z - a) * _ease(p)
        last = rnd == rounds - 1
        lo, hi = layers[(0.45, last)], layers[(0.9, last)]
        img = Image.blend(lo, hi, lvl * 0.85)
        _draw_circle(img, cx, cy, int(rmin + (rmax - rmin) * lvl), rmax)
        d = ImageDraw.Draw(img)
        count = min(int(secs), int(p * secs) + 1)
        d.text((cx, cy - 28), str(count), font=count_f, fill=AMBER, anchor="mm")
        d.text((cx, cy + 50), label, font=label_f, fill=TEXT, anchor="mm")
        if rounds > 1:
            d.text((cx, cy + rmax + 60), f"Round {rnd + 1} of {rounds}", font=round_f, fill=TEXT_2, anchor="mm")
        return img

    return total, frame


def _phone(b: dict) -> tuple[float, Frame]:
    base = _bg(0.7)
    d = ImageDraw.Draw(base)
    y = _text_block(d, b["headline"], b.get("body", ""), Y0, list(range(92, 56, -4)), list(range(42, 30, -2)),
                    h_max=230, b_max=170)
    top = max(y + 60, 660)
    pw = 640
    shot = Image.open(simg.SCREENS / f"{b['screen']}.jpg").convert("RGB")
    ph = int(shot.height * pw / shot.width)
    shot = shot.resize((pw, ph), Image.LANCZOS)
    skip = int(ph * float(b.get("crop", 0.0)))
    shot = shot.crop((0, skip, pw, min(ph, skip + (H - top) + 160)))
    m = 60
    sprite = Image.new("RGBA", (pw + 2 * m, shot.height + m), (0, 0, 0, 0))
    gl = Image.new("L", sprite.size, 0)
    ImageDraw.Draw(gl).rounded_rectangle([m - 30, m - 30, m + pw + 30, sprite.height + 100], radius=90, fill=120)
    gl = gl.filter(ImageFilter.GaussianBlur(50))
    sprite.paste(Image.new("RGBA", sprite.size, (120, 52, 18, 255)), (0, 0), gl)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height + 80], radius=64, fill=255)
    sprite.paste(shot, (m, m), mask)
    ImageDraw.Draw(sprite).rounded_rectangle([m - 2, m - 2, m + pw + 1, sprite.height + 80], radius=66,
                                            outline=(92, 70, 52, 255), width=3)
    x = (W - sprite.width) // 2
    rise = 140

    def frame(t: float) -> Image.Image:
        img = base.copy()
        off = int(rise * (1 - _ease(min(1.0, t / 0.8))))
        img.paste(sprite, (x, top - m + off), sprite)
        return img

    return beat_secs(b), frame


def _end(b: dict) -> tuple[float, Frame]:
    img = _bg(0.95)
    d = ImageDraw.Draw(img)
    head, lines = CLOSINGS[b.get("closing", "prelaunch")]
    lines = list(lines)
    if b.get("tool"):
        lines.append(f"Try it now, free: {b['tool']} at derot.org/tools.")
    simg._icon(img, X0, 400, 168)
    y = simg._draw_lines(d, head, simg._serif(112), 112, X0, 640, TEXT, 1.06)
    y += 40
    bf = simg._sans(42, 400)
    for ln in lines:
        for wl in simg._wrap(d, ln, bf, X1 - X0):
            d.text((X0, y), wl, font=bf, fill=TEXT_SOFT)
            y += 60
        y += 8
    d.text((X0, y + 34), "Regulate, don't restrict.", font=simg._sans(34, 600), fill=AMBER)
    _wordmark(d)
    return beat_secs(b), lambda t: img


BUILDERS = {"hook": _hook, "text": _text, "breathe": _breathe, "phone": _phone, "end": _end}


# ------------------------------------------------------------ encoding
def ffmpeg_exe() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("Rendering reels needs ffmpeg: run `pip install --user imageio-ffmpeg` "
                           "(or `brew install ffmpeg`).") from None


def render_reel(beats: list[dict], out_mp4: Path, cover_jpg: Optional[Path] = None,
                audio: Optional[Path] = None, volume: float = 1.0) -> dict:
    """Render a reel to out_mp4 (and its first frame to cover_jpg). Returns {"duration"}."""
    segs = [BUILDERS[b["beat"]](b) for b in beats]
    starts, t = [], 0.0
    for secs, _ in segs:
        starts.append(t)
        t += secs
    total = t
    n = int(round(total * FPS))
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.with_suffix(".tmp.mp4")
    if audio and Path(audio).exists():
        a_in = ["-stream_loop", "-1", "-i", str(audio)]
        # no fade in: the sound should be there from frame 0, like the picture
        a_filter = ["-af", f"volume={volume:.2f},afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5"]
    else:  # a silent track: some players and Instagram's processing are happier with one
        a_in = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        a_filter = []
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           *a_in, "-map", "0:v", "-map", "1:a", *a_filter,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-g", str(FPS * 2),
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    seg_i, prev_last = 0, None
    try:
        for f in range(n):
            tt = f / FPS
            while seg_i + 1 < len(segs) and tt >= starts[seg_i + 1]:
                prev_last = segs[seg_i][1](max(0.0, segs[seg_i][0] - 1.0 / FPS))
                seg_i += 1
            local = tt - starts[seg_i]
            img = segs[seg_i][1](local)
            if seg_i > 0 and local < XFADE and prev_last is not None:
                img = Image.blend(prev_last, img, local / XFADE)
            if f == 0 and cover_jpg:
                cover_jpg.parent.mkdir(parents=True, exist_ok=True)
                img.save(cover_jpg, "JPEG", quality=90, optimize=True, progressive=True)
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")
    tmp.replace(out_mp4)
    return {"duration": round(total, 2)}
