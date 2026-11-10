"""Shared test setup.

Real reel encoding is slow (an article reel is ~30s of 1080x1920 frames) and is covered
by test_reels.py. Everywhere else, DEROT_SEO_FAST_REELS makes social_reels.render_reel
write placeholder files instead. It is an environment variable rather than a monkeypatch
so the CLI subprocesses that test_pipeline and test_social start inherit it too. Without
it the self-test that gates publishing took 27 minutes in CI.
"""
import pytest


@pytest.fixture(autouse=True)
def _fast_reels(request, monkeypatch):
    if request.node.module.__name__.endswith("test_reels"):
        monkeypatch.delenv("DEROT_SEO_FAST_REELS", raising=False)
        return
    monkeypatch.setenv("DEROT_SEO_FAST_REELS", "1")
