"""Carousel slides for Instagram and Threads, in the Amber look.

1080x1350 (4:5) JPEGs: the tallest ratio the Instagram API accepts for feed
images, and it reads well on Threads. Rendered with Pillow so CI needs no
browser. Colors and fonts mirror amber.css and the blog OG images.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .util import ASSETS_DIR, SITE_DIR

W, H = 1080, 1350
PAD = 96

BASE = (12, 9, 7)
TEXT = (245, 237, 227)
TEXT_SOFT = (233, 217, 198)
TEXT_2 = (185, 172, 158)
AMBER = (242, 163, 65)
LABEL = (242, 196, 138)
HAIRLINE = (52, 42, 34)

FONTS = ASSETS_DIR / "fonts"


def _serif(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / "DMSerifDisplay-Regular.ttf"), size)


def _sans(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / "DMSans-Variable.ttf"), size)
    try:
        f.set_variation_by_axes([min(40, max(9, size // 2)), weight])
    except (OSError, ValueError):
        pass
    return f


def _background(strength: float) -> Image.Image:
    """Ember glow rising from the top edge, like the site and app."""
    img = Image.new("RGB", (W, H), BASE)
    glow = Image.new("RGB", (W, H), BASE)
    g = ImageDraw.Draw(glow)
    cx = W // 2
    for r, col in [(900, (58, 23, 10)), (680, (110, 44, 15)), (470, (180, 80, 28)), (270, (232, 118, 46))]:
        g.ellipse([cx - r, -int(r * 1.15), cx + r, int(r * 0.55)], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(150))
    return Image.blend(img, glow, strength)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            trial = (cur + " " + w).strip()
            if d.textlength(trial, font=font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _fit(d, text: str, make_font, sizes, width: int, max_height: int, leading: float):
    """Largest size whose wrapped text fits the box. Returns (font, lines, size)."""
    for size in sizes:
        font = make_font(size)
        lines = _wrap(d, text, font, width)
        if len(lines) * size * leading <= max_height and all(d.textlength(l, font=font) <= width for l in lines):
            return font, lines, size
    size = sizes[-1]
    font = make_font(size)
    return font, _wrap(d, text, font, width), size


def _draw_lines(d, lines, font, size, x, y, fill, leading) -> int:
    for ln in lines:
        d.text((x, y), ln, font=font, fill=fill)
        y += int(size * leading)
    return y


def _horizon(d, x0: int, x1: int, y: int) -> None:
    """The amber horizon line from the icon, fading at both ends."""
    span = x1 - x0
    fade = max(1, span // 4)
    for x in range(x0, x1):
        a = min(1.0, (x - x0) / fade, (x1 - x) / fade)
        col = tuple(int(BASE[i] + (AMBER[i] - BASE[i]) * a) for i in range(3))
        d.line([(x, y), (x, y + 2)], fill=col)


def _footer(d, page: str | None) -> None:
    _horizon(d, PAD, W - PAD, H - 150)
    d.text((PAD, H - 118), "DeRot", font=_serif(46), fill=TEXT)
    url_font = _sans(28, 500)
    label = "derot.org"
    d.text((W - PAD - d.textlength(label, font=url_font), H - 106), label, font=url_font, fill=TEXT_2)
    if page:
        pf = _sans(26, 600)
        d.text((PAD, PAD - 6), page, font=pf, fill=LABEL)


def render_cover(kicker: str, headline: str, out: Path,
                 hint: str = "Swipe for the 60-second version  →") -> Path:
    img = _background(0.95)
    d = ImageDraw.Draw(img)
    kf = _sans(30, 600)
    d.text((PAD, 250), kicker.upper(), font=kf, fill=LABEL, spacing=4)
    font, lines, size = _fit(d, headline, _serif, list(range(160, 60, -4)), W - 2 * PAD, 620, 1.12)
    y = _draw_lines(d, lines, font, size, PAD, 320, TEXT, 1.12)
    sf = _sans(32, 500)
    d.text((PAD, max(y + 60, 1020)), hint, font=sf, fill=TEXT_2)
    _footer(d, None)
    return _save(img, out)


def render_point(page: str, headline: str, body: str, out: Path) -> Path:
    img = _background(0.55)
    d = ImageDraw.Draw(img)
    hf, hlines, hsize = _fit(d, headline, _serif, list(range(92, 52, -4)), W - 2 * PAD, 420, 1.12)
    bf, blines, bsize = _fit(d, body, lambda s: _sans(s, 400), list(range(46, 30, -2)), W - 2 * PAD, 520, 1.42)
    block = len(hlines) * hsize * 1.12 + 56 + len(blines) * bsize * 1.42
    top = max(230, int((H - 170 - block) / 2) + 20)
    y = _draw_lines(d, hlines, hf, hsize, PAD, top, TEXT, 1.12)
    y += 28
    d.line([(PAD, y), (PAD + 90, y)], fill=AMBER, width=4)
    y += 28
    _draw_lines(d, blines, bf, bsize, PAD, y, TEXT_SOFT, 1.42)
    _footer(d, page)
    return _save(img, out)


def render_closing(page: str, tool_label: str, out: Path) -> Path:
    img = _background(0.95)
    d = ImageDraw.Draw(img)
    icon_path = SITE_DIR / "img" / "derot-icon-384.png"
    y = 270
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGB").resize((168, 168), Image.LANCZOS)
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, 167, 167], radius=38, fill=255)
        img.paste(icon, (PAD, y), mask)
        y += 168 + 70
    hf = _serif(96)
    y = _draw_lines(d, ["Read the full", "guide, free."], hf, 96, PAD, y, TEXT, 1.1)
    y += 36
    bf = _sans(40, 400)
    tool_line = f"Try it now, free: {tool_label} at derot.org/tools."
    for ln in ["Link in bio, or derot.org/blog.", tool_line]:
        for wl in _wrap(d, ln, bf, W - 2 * PAD):
            d.text((PAD, y), wl, font=bf, fill=TEXT_SOFT)
            y += 58
        y += 14
    y += 30
    sf = _sans(32, 600)
    d.text((PAD, y), "Save this for your next scroll.", font=sf, fill=AMBER)
    _footer(d, page)
    return _save(img, out)


def _save(img: Image.Image, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out


def render_set(slides: list[dict], kicker: str, tool: dict, out_dir: Path) -> list[Path]:
    """slides[0] is the cover (headline), the rest are points (headline, body).
    A closing slide is always added. Returns the image paths in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide-*.jpg"):
        old.unlink()
    total = len(slides) + 1
    paths = [render_cover(kicker, slides[0]["headline"], out_dir / "slide-01.jpg")]
    for i, s in enumerate(slides[1:], start=2):
        paths.append(render_point(f"{i:02d} / {total:02d}", s["headline"], s["body"], out_dir / f"slide-{i:02d}.jpg"))
    paths.append(render_closing(f"{total:02d} / {total:02d}", tool["label"],
                                out_dir / f"slide-{total:02d}.jpg"))
    return paths


# ------------------------------------------------------------ promo templates
SCREENS = ASSETS_DIR / "screens"
CLOSINGS = {
    "prelaunch": (["Coming soon", "to iPhone."],
                  ["Join the launch list: link in bio.", "We'll tell you the day it's live."]),
    "launch": (["Now on", "iPhone."], ["Download DeRot: link in bio.", "No account needed. Runs on your phone."]),
}


def _icon(img: Image.Image, x: int, y: int, size: int = 168) -> None:
    icon_path = SITE_DIR / "img" / "derot-icon-384.png"
    if not icon_path.exists():
        return
    icon = Image.open(icon_path).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.226), fill=255)
    img.paste(icon, (x, y), mask)


def _top_bar(d, page: str | None) -> None:
    if page:
        d.text((PAD, PAD - 6), page, font=_sans(26, 600), fill=LABEL)
    wm = _serif(40)
    d.text((W - PAD - d.textlength("DeRot", font=wm), PAD - 14), "DeRot", font=wm, fill=TEXT)


def render_phone(page: str | None, headline: str, body: str, screen: str, out: Path, crop: float = 0.0) -> Path:
    """Headline and body over an app screenshot in a phone outline that runs off the bottom edge.
    crop: fraction of the screen's height to skip at the top, to bring the interesting part up."""
    img = _background(0.7)
    d = ImageDraw.Draw(img)
    _top_bar(d, page)
    hf, hl, hs = _fit(d, headline, _serif, list(range(84, 52, -4)), W - 2 * PAD, 200, 1.1)
    y = _draw_lines(d, hl, hf, hs, PAD, 190, TEXT, 1.1)
    if body:
        bf, bl, bs = _fit(d, body, lambda s: _sans(s, 400), list(range(38, 28, -2)), W - 2 * PAD, 150, 1.38)
        y = _draw_lines(d, bl, bf, bs, PAD, y + 30, TEXT_SOFT, 1.38)
    top = max(y + 56, 520)
    pw = 600
    shot = Image.open(SCREENS / f"{screen}.jpg").convert("RGB")
    ph = int(shot.height * pw / shot.width)
    shot = shot.resize((pw, ph), Image.LANCZOS)
    skip = int(ph * crop)
    shot = shot.crop((0, skip, pw, min(ph, skip + (H - top))))
    x = (W - pw) // 2
    # soft amber glow behind the phone
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).rounded_rectangle([x - 30, top - 30, x + pw + 30, H + 60], radius=90, fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    img.paste(Image.new("RGB", (W, H), (120, 52, 18)), (0, 0), glow)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height + 80], radius=64, fill=255)
    img.paste(shot, (x, top), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x - 2, top - 2, x + pw + 1, H + 80], radius=66, outline=(92, 70, 52), width=3)
    return _save(img, out)


def render_statement(kicker: str, statement: str, out: Path, sub: str = "", page: str | None = None) -> Path:
    """One big line in the display serif, with an optional supporting line."""
    img = _background(0.9)
    d = ImageDraw.Draw(img)
    sf, sl, ss = _fit(d, statement, _serif, list(range(128, 60, -4)), W - 2 * PAD, 640, 1.1)
    block = len(sl) * ss * 1.1
    bf = bl = None
    bs = 40
    if sub:
        bf, bl, bs = _fit(d, sub, lambda s: _sans(s, 400), list(range(44, 30, -2)), W - 2 * PAD, 300, 1.4)
        block += 60 + len(bl) * bs * 1.4
    y = max(300, int((H - 160 - block) / 2) + 40)
    if kicker:
        d.text((PAD, y - 70), kicker.upper(), font=_sans(30, 600), fill=LABEL)
    y = _draw_lines(d, sl, sf, ss, PAD, y, TEXT, 1.1)
    if sub:
        y += 30
        d.line([(PAD, y), (PAD + 90, y)], fill=AMBER, width=4)
        _draw_lines(d, bl, bf, bs, PAD, y + 30, TEXT_SOFT, 1.4)
    _footer(d, page)
    return _save(img, out)


def render_promo_closing(page: str | None, phase: str, out: Path) -> Path:
    img = _background(0.95)
    d = ImageDraw.Draw(img)
    head, lines = CLOSINGS[phase]
    _icon(img, PAD, 290)
    y = _draw_lines(d, head, _serif(104), 104, PAD, 530, TEXT, 1.08)
    y += 40
    bf = _sans(40, 400)
    for ln in lines:
        for wl in _wrap(d, ln, bf, W - 2 * PAD):
            d.text((PAD, y), wl, font=bf, fill=TEXT_SOFT)
            y += 58
        y += 10
    d.text((PAD, y + 34), "Regulate, don't restrict.", font=_sans(32, 600), fill=AMBER)
    _footer(d, page)
    return _save(img, out)


def render_promo(slides: list[dict], kicker: str, out_dir: Path, closing: str | None) -> list[Path]:
    """Render a promo post. Each slide: {"template": cover|point|phone|statement, ...}.
    closing: "prelaunch", "launch", or None. One slide and no closing = a single image post."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide-*.jpg"):
        old.unlink()
    total = len(slides) + (1 if closing else 0)
    paths = []
    for i, s in enumerate(slides, start=1):
        page = f"{i:02d} / {total:02d}" if total > 1 and i > 1 else None
        out = out_dir / f"slide-{i:02d}.jpg"
        t = s.get("template", "point")
        if t == "cover":
            paths.append(render_cover(s.get("kicker", kicker), s["headline"], out,
                                      hint=s.get("hint", "Swipe  →")))
        elif t == "phone":
            paths.append(render_phone(page, s["headline"], s.get("body", ""), s["screen"], out, s.get("crop", 0.0)))
        elif t == "statement":
            paths.append(render_statement(s.get("kicker", kicker if i == 1 else ""), s["headline"], out,
                                          s.get("body", ""), page))
        else:
            paths.append(render_point(page or "", s["headline"], s.get("body", ""), out))
    if closing:
        paths.append(render_promo_closing(f"{total:02d} / {total:02d}", closing, out_dir / f"slide-{total:02d}.jpg"))
    return paths
