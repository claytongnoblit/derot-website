"""Promo calendar: the plan passes the copy rules, builds into the social queue,
and the poster handles promo packages. No network."""
import copy as copylib
import datetime as dt
import json

import pytest
from PIL import Image

from derot_seo import promo, social
from derot_seo.util import load_config
from test_social import FakeMeta, clients  # noqa: F401  (shared fake Meta API)

UTC = dt.timezone.utc
BEFORE = dt.datetime(2026, 10, 9, 5, tzinfo=UTC)


def test_plan_passes_copy_rules():
    assert promo.check_plan(promo.load_plan()) == []


def test_copy_rules_catch_promo_mistakes():
    plan = copylib.deepcopy(promo.load_plan())
    t = next(p for p in plan["prelaunch"] if p["platform"] == "threads")
    t["text"] = "DeRot is available now — it calms your vagus nerve. https://example.com"
    ig = next(p for p in plan["prelaunch"] if p["platform"] == "instagram")
    ig["caption"] = "No bio line here."
    errors = "\n".join(promo.check_plan(plan))
    for needle in ("pre-launch", "dash", "vagus", "example.com", "link in bio"):
        assert needle in errors, needle


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "seo/content/social").mkdir(parents=True)
    (site / "seo/content/posts").mkdir(parents=True)
    (site / "img").mkdir()
    monkeypatch.setattr(social, "SOCIAL_DIR", site / "seo/content/social")
    monkeypatch.setattr(social, "POSTS_DIR", site / "seo/content/posts")
    monkeypatch.setattr(social, "IMG_DIR", site / "img/social")
    monkeypatch.setattr(promo, "SITE_DIR", site)
    return site


def pkgs(site):
    return {f.stem: json.loads(f.read_text()) for f in (site / "seo/content/social").glob("*.json")}


def test_build_queues_prelaunch_only(queue):
    cfg = load_config()
    counts = promo.build(cfg, now=BEFORE)
    plan = promo.load_plan()
    assert counts["queued"] == len(plan["prelaunch"])
    q = pkgs(queue)
    assert all(p["kind"] == "promo" and p["phase"] == "prelaunch" for p in q.values())
    first = q["promo-w1-mon-feeling"]["platforms"]["threads"]
    assert first["scheduled_at"] == "2026-10-12T21:00:00+00:00" and first["status"] == "queued"
    meet = q["promo-w1-tue-meet"]
    assert meet["platforms"]["instagram"]["scheduled_at"] == "2026-10-13T23:30:00+00:00"
    assert len(meet["images"]) == 6 and all(i["alt"] for i in meet["images"])
    with Image.open(queue / meet["images"][0]["path"]) as im:
        assert im.size == (1080, 1350)
    how = q["promo-w1-thu-how"]
    assert [i["path"] for i in how["threads_images"]] == [meet["images"][i]["path"] for i in (2, 3, 4)]
    assert q["promo-w1-mon-feeling"]["threads_images"] == []
    assert q["promo-w2-tue-box"]["url"] == "https://derot.org/tools/box-breathing-timer/"
    assert q["promo-w4-fri-almost"]["url"] == "https://derot.org/#newsletter"
    # launch images are rendered for review but nothing launch-phase is queued
    assert (queue / "img/social/promo-launch-d0-carousel/slide-01.jpg").exists()
    assert not any(k.startswith("promo-launch") for k in q)


def test_launch_needs_app_store_url_then_queues(queue):
    cfg = load_config()
    cfg["site"]["app_store_url"] = ""
    with pytest.raises(ValueError):
        promo.build(cfg, launch_date=dt.date(2026, 11, 16), now=BEFORE, phases=("launch",))
    cfg["site"]["app_store_url"] = "https://apps.apple.com/app/derot/id1234567890"
    counts = promo.build(cfg, launch_date=dt.date(2026, 11, 16), now=BEFORE, phases=("launch",))
    assert counts["queued"] == len(promo.load_plan()["launch"])
    q = pkgs(queue)
    d0 = q["promo-launch-d0-threads"]
    assert d0["url"] == cfg["site"]["app_store_url"]
    assert d0["platforms"]["threads"]["scheduled_at"] == "2026-11-16T21:00:00+00:00"
    assert len(d0["threads_images"]) == 4
    assert q["promo-launch-d5-setup"]["platforms"]["instagram"]["scheduled_at"] == "2026-11-21T23:30:00+00:00"


def test_poster_handles_promo(queue):
    cfg = load_config()
    promo.build(cfg, now=BEFORE)
    fake = FakeMeta()
    now = dt.datetime(2026, 10, 13, 23, 40, tzinfo=UTC)   # after Mon/Tue slots of week 1
    res = social.post_due(cfg, now=now, clients=clients(fake, cfg), check_images=lambda ims: None, log=lambda s: None)
    assert sorted(res["posted"])[0].startswith("promo-w1-mon-feeling") and len(res["posted"]) == 3
    th = fake.posts_to("/TH1/threads")
    assert {c[2]["media_type"] for c in th} == {"TEXT"}            # text-only Threads posts
    assert all("{url}" not in c[2]["text"] for c in th)
    ig_parent = [c for c in fake.posts_to("/IG1/media") if c[2].get("media_type") == "CAROUSEL"]
    assert len(ig_parent) == 1 and "#digitalwellbeing" in ig_parent[0][2]["caption"]
    assert fake.posts_to("/PAGE1/feed") == []                       # promo is Threads + Instagram only

    # Thursday's Threads post carries three borrowed slides as a carousel
    fake2 = FakeMeta()
    res = social.post_due(cfg, now=dt.datetime(2026, 10, 15, 21, 5, tzinfo=UTC), clients=clients(fake2, cfg),
                          check_images=lambda ims: None, log=lambda s: None)
    assert any(r.startswith("promo-w1-thu-how") for r in res["posted"])
    assert len([c for c in fake2.posts_to("/TH1/threads") if c[2].get("is_carousel_item") == "true"]) == 3

    # a rebuild never rewrites what already went out
    before = pkgs(queue)["promo-w1-mon-feeling"]
    counts = promo.build(cfg, now=now)
    assert counts["kept"] >= 4
    assert pkgs(queue)["promo-w1-mon-feeling"] == before


def test_moving_start_and_past_dates(queue):
    cfg = load_config()
    plan = promo.load_plan()
    plan["start"] = dt.date(2026, 10, 5)                      # a week earlier than now allows
    counts = promo.build(cfg, plan, now=dt.datetime(2026, 10, 12, 12, tzinfo=UTC))
    q = pkgs(queue)
    assert q["promo-w1-mon-feeling"]["platforms"]["threads"]["status"] == "skipped"
    assert counts["past"] > 0
    assert q["promo-w1-sat-statement"]["platforms"]["instagram"]["status"] == "skipped"
    # a slot coming up sooner than the lead time is nudged back, not dropped
    nxt = q["promo-w2-mon-signs"]["platforms"]["threads"]
    assert nxt["status"] == "queued" and nxt["scheduled_at"] == "2026-10-12T21:00:00+00:00"
    plan["start"] = dt.date(2026, 10, 5)
    promo.build(cfg, plan, now=dt.datetime(2026, 10, 12, 20, 30, tzinfo=UTC))
    nxt = pkgs(queue)["promo-w2-mon-signs"]["platforms"]["threads"]
    assert nxt["status"] == "queued" and nxt["scheduled_at"] == "2026-10-12T21:30:00+00:00"
