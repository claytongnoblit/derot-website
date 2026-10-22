"""Reel rendering end to end: real frames, real ffmpeg. Skipped if no ffmpeg is available."""
import subprocess

import pytest
from PIL import Image

from derot_seo import social_reels as reels

try:
    FFMPEG = reels.ffmpeg_exe()
except RuntimeError:
    FFMPEG = None


def test_durations_and_checks():
    beats = [{"beat": "hook", "text": "Your jaw is probably clenched right now."},
             {"beat": "breathe", "pattern": "calm", "rounds": 2},
             {"beat": "end", "closing": "prelaunch"}]
    assert reels.duration(beats) == pytest.approx(2.4 + 20 + 3)
    assert reels.check_beats(beats, reels.simg.SCREENS) == []
    assert "open on a hook" in " ".join(reels.check_beats(beats[1:], reels.simg.SCREENS))
    assert reels.spec_hash(beats) != reels.spec_hash(beats[:2])


@pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg")
def test_render_reel(tmp_path):
    beats = [{"beat": "hook", "text": "Your jaw is probably clenched right now.", "secs": 1},
             {"beat": "breathe", "phases": [["Breathe in", 1, 1.0], ["Breathe out", 1, 0.0]], "rounds": 1,
              "title": "Breathe with this", "note": "Longer out than in."},
             {"beat": "phone", "screen": "shield", "headline": "Mid-scroll", "secs": 1},
             {"beat": "end", "closing": "prelaunch", "secs": 1}]
    mp4, cover = tmp_path / "reel.mp4", tmp_path / "cover.jpg"
    info = reels.render_reel(beats, mp4, cover)
    assert info["duration"] == 5.0
    with Image.open(cover) as im:
        assert im.size == (1080, 1920)
    out = subprocess.run([FFMPEG, "-i", str(mp4)], capture_output=True, text=True).stderr
    assert "h264" in out and "1080x1920" in out and "aac" in out
    assert "Duration: 00:00:05" in out


def test_reels_stay_short():
    talk = [{"beat": "hook", "text": "Short hook."}] + \
           [{"beat": "text", "headline": "A line", "secs": 5} for _ in range(5)] + [{"beat": "end"}]
    assert "no breathe-along" in " ".join(reels.check_beats(talk, reels.simg.SCREENS))
    long_breathe = [{"beat": "hook", "text": "Short hook."}, {"beat": "breathe", "pattern": "478", "rounds": 2},
                    {"beat": "end"}]
    assert "under 40s" in " ".join(reels.check_beats(long_breathe, reels.simg.SCREENS))
    # reading time is capped, however long the text
    assert reels.beat_secs({"beat": "text", "headline": "word " * 40}) == 5.0


def test_hook_moves_from_the_first_frame():
    for b in ({"beat": "hook", "text": "Your doomscroll has a tell."},
              {"beat": "hook", "text": "Your doomscroll has a tell.", "circle": False}):
        secs, frame = reels._hook(b)
        assert frame(0).tobytes() != frame(secs / 2).tobytes()
