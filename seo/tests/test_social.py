"""Social posting: copy gate, package preparation, and the poster against a fake
Meta API. No network and no real tokens."""
import copy as copylib
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from derot_seo import meta_api, social
from derot_seo.util import load_config

SITE = Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc

POST = {
    "slug": "box-breathing", "title": "Box breathing: a calm guide", "dek": "Four counts, four sides.",
    "date_published": "2026-10-12", "cluster": "breathing-techniques", "primary_keyword": "box breathing",
    "body_markdown": "Breathe in for 4, hold for 4, out for 4, hold for 4. Five minutes a day helped in a 2023 study.",
    "key_takeaways": ["Box breathing uses four equal counts."], "howto_steps": ["Inhale for 4."], "faqs": [],
}


def good_copy():
    return {
        "cover_headline": "Box breathing in 60 seconds",
        "points": [{"headline": "Four equal counts", "body": "Breathe in for 4, hold for 4, out for 4, hold for 4."},
                   {"headline": "Use it mid-scroll", "body": "When your shoulders creep up, run one round."},
                   {"headline": "Go gently", "body": "Stop if you feel lightheaded."}],
        "instagram_caption": "Four counts, four sides.\n\nOne round takes about 16 seconds.",
        "instagram_hashtags": ["#breathwork", "box breathing", "breathwork", "dopaminedetox"],
        "threads_text": "Box breathing is four equal counts. Try one round next time the feed winds you up.",
        "facebook_message": "Four counts, four sides. Here is the whole technique. {url}",
    }


# ------------------------------------------------------------ copy gate
def test_check_copy_fixes_and_passes():
    cfg = load_config()
    c = good_copy()
    c["cover_headline"] = "Box breathing — in 60 seconds"
    c["instagram_caption"] = c["instagram_caption"].replace("16", "4")
    errors, fixes = social.check_copy(c, POST, cfg)
    assert errors == []
    assert "—" not in c["cover_headline"]
    assert c["instagram_caption"].endswith("Full guide: link in bio.")
    assert c["threads_text"].endswith("{url}")
    assert "{url}" not in c["facebook_message"]
    assert c["instagram_hashtags"] == ["breathwork", "boxbreathing"]  # deduped, cleaned, detox dropped
    assert any("dash" in f for f in fixes)


def test_check_copy_blocks_brand_and_invented_facts():
    cfg = load_config()
    c = good_copy()
    c["points"][1]["body"] = "DeRot locks your apps so you can beat phone addiction."
    c["threads_text"] = "A 47% drop in stress, says https://example.com/study {url}"
    errors, _ = social.check_copy(c, POST, cfg)
    text = "\n".join(errors)
    assert "phone addiction" in text
    assert "lock" in text
    assert "47" in text                     # number not in the article
    assert "example.com" in text            # outside link


def test_check_copy_threads_byte_limit():
    cfg = load_config()
    c = good_copy()
    c["threads_text"] = ("Calm " * 95) + "{url}"
    errors, _ = social.check_copy(c, POST, cfg)
    assert any("bytes" in e for e in errors)


# ------------------------------------------------------------ fixtures
@pytest.fixture()
def site(tmp_path):
    dst = tmp_path / "site"
    shutil.copytree(SITE, dst, ignore=shutil.ignore_patterns(".git", "marketing", "__pycache__", ".pytest_cache"))
    for d in ("seo/content/posts", "seo/content/drafts", "seo/content/social", "img/social"):
        shutil.rmtree(dst / d, ignore_errors=True)
        (dst / d).mkdir(parents=True)
    for f in ("seo/data/runs.jsonl", "seo/data/generated_files.json"):
        (dst / f).unlink(missing_ok=True)
    return dst


def cli(site, *args, today="2026-10-12"):
    env = {**os.environ, "DEROT_SEO_TODAY": today, "PYTHONWARNINGS": "ignore"}
    for k in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "META_PAGE_ACCESS_TOKEN", "THREADS_ACCESS_TOKEN"):
        env.pop(k, None)
    return subprocess.run([sys.executable, "-m", "derot_seo", "--mock", *args], cwd=site / "seo",
                          env=env, capture_output=True, text=True, timeout=300)


@pytest.fixture()
def published(site, monkeypatch):
    r = cli(site, "publish", "--no-link-check")
    assert r.returncode == 0, r.stdout + r.stderr
    monkeypatch.setattr(social, "SOCIAL_DIR", site / "seo/content/social")
    monkeypatch.setattr(social, "POSTS_DIR", site / "seo/content/posts")
    monkeypatch.setattr(social, "IMG_DIR", site / "img/social")
    monkeypatch.setattr(social, "SITE_DIR", site)
    monkeypatch.setattr(social, "OUT_DIR", site / "seo/out")
    pkg_file = next((site / "seo/content/social").glob("*.json"))
    return site, json.loads(pkg_file.read_text())


def test_publish_queues_a_social_package(published):
    site, pkg = published
    assert pkg["approved"] is True and pkg["qa_errors"] == []
    assert set(pkg["platforms"]) == {"facebook", "threads", "instagram"}
    assert all(p["status"] == "queued" for p in pkg["platforms"].values())
    assert len(pkg["images"]) == len(pkg["copy"]["points"]) + 2   # cover + points + closing
    for im in pkg["images"]:
        assert im["url"] == "https://derot.org/" + im["path"]
        assert im["alt"]
        with Image.open(site / im["path"]) as img:
            assert img.size == (1080, 1350) and img.format == "JPEG"
    assert (site / f"seo/out/social/{pkg['slug']}-preview.html").exists()
    # the existing per-article social kit is still written
    assert list((site / "seo/out/social").glob("*.md"))


def test_prepare_is_idempotent_and_missing_finds_nothing(published):
    site, pkg = published
    r = cli(site, "social", "prepare", "--missing")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Nothing to prepare" in r.stdout


# ------------------------------------------------------------ fake Meta API
class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = headers or {}

    def json(self):
        return self._body


class FakeMeta:
    """Answers the Graph and Threads endpoints the clients use, and records calls."""

    def __init__(self):
        self.calls = []
        self.n = 0
        self.fail_next = []   # list of (path_substring, status, error_dict)

    def _id(self):
        self.n += 1
        return str(1000 + self.n)

    def _maybe_fail(self, url):
        for i, (frag, status, err) in enumerate(self.fail_next):
            if frag in url:
                self.fail_next.pop(i)
                return FakeResp(status, {"error": err})
        return None

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, dict(data or {})))
        return self._maybe_fail(url) or FakeResp(200, {"id": self._id()})

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {})))
        fail = self._maybe_fail(url)
        if fail:
            return fail
        f = (params or {}).get("fields", "")
        if "status_code" in f:
            return FakeResp(200, {"status_code": "FINISHED"})
        if f.startswith("status"):
            return FakeResp(200, {"status": "FINISHED"})
        if "permalink" in f:
            key = "permalink_url" if "permalink_url" in f else "permalink"
            return FakeResp(200, {key: "https://example.test/p/" + url.rsplit("/", 1)[-1]})
        return FakeResp(200, {})

    def posts_to(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]


def clients(fake, cfg):
    kw = dict(session=fake, sleep=lambda s: None)
    return {
        "facebook": meta_api.FacebookPageClient("PAGETOKEN", "PAGE1", "v26.0", **kw),
        "instagram": meta_api.InstagramClient("PAGETOKEN", "IG1", "v26.0", **kw),
        "threads": meta_api.ThreadsClient("THTOKEN", "TH1", **kw),
    }


AFTER_ALL = dt.datetime(2026, 12, 1, tzinfo=UTC)


def run_post(cfg, fake, now, **kw):
    return social.post_due(cfg, now=now, clients=clients(fake, cfg), check_images=lambda ims: None,
                           log=lambda s: None, **kw)


def due_now(pkg):
    """A time just after the latest slot (always within max_late_hours)."""
    return max(dt.datetime.fromisoformat(p["scheduled_at"]) for p in pkg["platforms"].values()) + dt.timedelta(minutes=5)


def test_posts_everything_due_once(published):
    site, pkg = published
    cfg = load_config()
    fake = FakeMeta()
    res = run_post(cfg, fake, due_now(pkg))
    assert len(res["posted"]) == 3 and not res["failed"], res
    n_img = len(pkg["images"])

    ig_media = fake.posts_to("/IG1/media")
    children = [c for c in ig_media if c[2].get("is_carousel_item") == "true"]
    parent = [c for c in ig_media if c[2].get("media_type") == "CAROUSEL"]
    assert len(children) == n_img and len(parent) == 1
    assert all(c[2]["alt_text"] for c in children)
    assert parent[0][2]["caption"].rstrip().endswith(("#" + pkg["copy"]["instagram_hashtags"][-1]))
    assert "link in bio" in parent[0][2]["caption"].lower()
    assert len(fake.posts_to("/IG1/media_publish")) == 1

    th = fake.posts_to("/TH1/threads")
    assert len(th) == 1 and th[0][2]["media_type"] == "IMAGE"            # cover mode
    assert th[0][2]["image_url"] == pkg["images"][0]["url"]
    assert pkg["url"] in th[0][2]["text"] and "{url}" not in th[0][2]["text"]
    assert len(fake.posts_to("/TH1/threads_publish")) == 1

    fb = fake.posts_to("/PAGE1/feed")
    assert len(fb) == 1 and fb[0][2]["link"].startswith(pkg["url"] + "?utm_source=facebook")
    # tokens go in the body, never the URL
    assert all("TOKEN" not in c[1] for c in fake.calls)

    saved = json.loads((site / f"seo/content/social/{pkg['slug']}.json").read_text())
    for name, st in saved["platforms"].items():
        assert st["status"] == "posted" and st["id"] and st["permalink"].startswith("https://example.test/")

    fake2 = FakeMeta()
    res2 = run_post(cfg, fake2, due_now(pkg) + dt.timedelta(hours=1))
    assert res2["posted"] == [] and fake2.calls == []


def test_nothing_before_the_slot(published):
    site, pkg = published
    fake = FakeMeta()
    first = min(dt.datetime.fromisoformat(p["scheduled_at"]) for p in pkg["platforms"].values())
    res = run_post(load_config(), fake, first - dt.timedelta(minutes=1))
    assert res["posted"] == [] and fake.calls == []


def test_transient_error_retries_then_fails(published):
    site, pkg = published
    cfg = load_config()
    now = due_now(pkg)
    fake = FakeMeta()
    for _ in range(3):
        fake.fail_next.append(("/TH1/threads", 500, {"message": "Service temporarily unavailable THTOKEN",
                                                      "code": 2, "is_transient": True}))
    res = run_post(cfg, fake, now, platform="threads")
    assert res["waiting"] and not res["failed"]
    st = json.loads((site / f"seo/content/social/{pkg['slug']}.json").read_text())["platforms"]["threads"]
    assert st["status"] == "queued" and st["attempts"] == 1 and st["retry_after"]
    assert "THTOKEN" not in st["error"]          # token scrubbed from stored errors
    # retry_after holds it back
    assert run_post(cfg, fake, now + dt.timedelta(minutes=10), platform="threads")["waiting"] == []
    run_post(cfg, fake, now + dt.timedelta(hours=2), platform="threads")
    res = run_post(cfg, fake, now + dt.timedelta(hours=5), platform="threads")
    assert res["failed"]
    st = json.loads((site / f"seo/content/social/{pkg['slug']}.json").read_text())["platforms"]["threads"]
    assert st["status"] == "failed" and st["attempts"] == 3


def test_alt_text_rejection_falls_back(published):
    site, pkg = published
    fake = FakeMeta()
    fake.fail_next.append(("/TH1/threads", 400, {"message": "(#100) Invalid parameter alt_text", "code": 100}))
    res = run_post(load_config(), fake, due_now(pkg), platform="threads")
    assert len(res["posted"]) == 1
    th = fake.posts_to("/TH1/threads")
    assert "alt_text" in th[0][2] and "alt_text" not in th[1][2]


def test_held_unpublished_stale_and_waiting(published):
    site, pkg = published
    cfg = load_config()
    pkg_file = site / f"seo/content/social/{pkg['slug']}.json"
    fake = FakeMeta()

    held = copylib.deepcopy(pkg)
    held["approved"] = False
    pkg_file.write_text(json.dumps(held))
    assert run_post(cfg, fake, due_now(pkg))["posted"] == []

    pkg_file.write_text(json.dumps(pkg))
    post_file = site / f"seo/content/posts/{pkg['slug']}.json"
    saved_post = post_file.read_text()
    post_file.unlink()                      # article unpublished: never promote it
    assert run_post(cfg, fake, due_now(pkg))["posted"] == []
    post_file.write_text(saved_post)

    res = social.post_due(cfg, now=due_now(pkg), clients=clients(fake, cfg), log=lambda s: None,
                          check_images=lambda ims: "img: HTTP 404")
    assert len(res["waiting"]) == 3 and fake.calls == []
    st = json.loads(pkg_file.read_text())["platforms"]
    assert all(s["attempts"] == 0 for s in st.values())

    res = social.post_due(cfg, now=due_now(pkg), clients={}, log=lambda s: None, check_images=lambda ims: None)
    assert all("no credentials" in w for w in res["waiting"])

    res = run_post(cfg, fake, AFTER_ALL)
    assert len(res["skipped"]) == 3 and fake.calls == []


def test_dry_run_posts_nothing(published):
    site, pkg = published
    fake = FakeMeta()
    res = run_post(load_config(), fake, due_now(pkg), dry_run=True)
    assert len(res["posted"]) == 3 and fake.calls == []
    st = json.loads((site / f"seo/content/social/{pkg['slug']}.json").read_text())["platforms"]
    assert all(s["status"] == "queued" for s in st.values())


def test_schedule_respects_lead_time():
    cfg = load_config()
    late = dt.datetime(2026, 10, 12, 23, 0, tzinfo=UTC)
    sch = social.schedule_for(cfg, POST, late)
    assert dt.datetime.fromisoformat(sch["facebook"]["scheduled_at"]) == late + dt.timedelta(hours=1)
    assert sch["instagram"]["scheduled_at"] == "2026-10-13T00:00:00+00:00"
    early = dt.datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    sch = social.schedule_for(cfg, POST, early)
    assert sch["facebook"]["scheduled_at"] == "2026-10-12T16:30:00+00:00"
    assert sch["instagram"]["scheduled_at"] == "2026-10-12T23:30:00+00:00"


class BadCopyLLM:
    """Always returns copy that breaks the brand rules."""
    def __init__(self):
        from derot_seo.llm import Usage
        self.usage = Usage()
        self.stages = []

    def structured(self, stage, role, system, prompt, schema, max_tokens=0, mock_hint=None):
        self.stages.append(stage)
        c = good_copy()
        c["points"][0]["body"] = "Crush your screen time and take back control."
        return c


def test_failing_copy_is_rewritten_once_then_held(published):
    site, pkg = published
    cfg = load_config()
    post = json.loads((site / f"seo/content/posts/{pkg['slug']}.json").read_text())
    llm = BadCopyLLM()
    held = social.prepare(post, cfg, llm, now=dt.datetime(2026, 10, 12, 9, tzinfo=UTC), force=True)
    assert llm.stages == ["social", "social_revise"]
    assert held["approved"] is False and held["needs_review"] and held["qa_errors"]
    assert run_post(cfg, FakeMeta(), due_now(held))["posted"] == []
    assert len(social.unnotified_holds()) == 1
    assert social.unnotified_holds() == []          # only reported once
