import copy

from derot_seo import keywords as kw
from derot_seo.util import load_config

CFG = load_config()


def universe():
    return {
        "clusters": [{"id": "a", "name": "A", "pillar_keyword": "a pillar", "description": "", "priority": 5},
                     {"id": "b", "name": "B", "pillar_keyword": "b pillar", "description": "", "priority": 3}],
        "keywords": [
            {"keyword": "box breathing how to", "cluster": "a", "volume_tier": 3, "difficulty_tier": 2, "business_fit": 5, "content_type": "how-to", "status": "queued"},
            {"keyword": "a pillar", "cluster": "a", "volume_tier": 4, "difficulty_tier": 4, "business_fit": 5, "content_type": "pillar", "status": "queued"},
            {"keyword": "box breathing timer", "cluster": "a", "volume_tier": 4, "difficulty_tier": 2, "business_fit": 5, "content_type": "tool", "status": "tool"},
            {"keyword": "why scrolling stresses me", "cluster": "b", "volume_tier": 2, "difficulty_tier": 1, "business_fit": 5, "content_type": "explainer", "status": "queued"},
            {"keyword": "off brand", "cluster": "b", "volume_tier": 5, "difficulty_tier": 1, "business_fit": 1, "content_type": "explainer", "status": "queued"},
            {"keyword": "done already", "cluster": "b", "volume_tier": 5, "difficulty_tier": 1, "business_fit": 5, "content_type": "explainer", "status": "published"},
        ],
    }


def test_shortlist_excludes_tools_published_and_offbrand():
    sl = [k["keyword"] for k in kw.shortlist(universe(), [], CFG, 10)]
    assert "box breathing timer" not in sl
    assert "done already" not in sl
    assert "off brand" not in sl


def test_pillar_waits_for_support_posts():
    sl = kw.shortlist(universe(), [], CFG, 10)
    assert sl[-1]["keyword"] == "a pillar"


def test_cannibalization_marks_covered():
    data = universe()
    posts = [{"slug": "box-breathing", "primary_keyword": "how to box breathing", "secondary_keywords": [], "cluster": "a"}]
    sl = [k["keyword"] for k in kw.shortlist(data, posts, CFG, 10)]
    assert "box breathing how to" not in sl
    assert next(k for k in data["keywords"] if k["keyword"] == "box breathing how to")["status"] == "covered"


def test_recent_cluster_penalty_rotates_topics():
    data = universe()
    posts = [{"slug": "x", "primary_keyword": "zzz", "secondary_keywords": [], "cluster": "a"},
             {"slug": "y", "primary_keyword": "yyy", "secondary_keywords": [], "cluster": "a"}]
    assert kw.shortlist(data, posts, CFG, 1)[0]["cluster"] == "b"


def test_real_keyword_file_is_valid():
    data = kw.load_universe()
    ids = {c["id"] for c in data["clusters"]}
    assert len(data["keywords"]) > 50
    for k in data["keywords"]:
        assert k["cluster"] in ids, k
        assert 1 <= k.get("volume_tier", 1) <= 5
        assert 1 <= k.get("difficulty_tier", 1) <= 5
    assert kw.shortlist(copy.deepcopy(data), [], CFG, 5)


# ---------------------------------------------------------------- comparison track
def _cmp_cfg():
    cfg = copy.deepcopy(CFG)
    cfg["comparison_track"] = {"enabled": True, "clusters": ["b"], "max_posts_per_week": 2,
                               "min_hours_between_posts": 40, "social": False,
                               "content_type_bonus": {"list": 3.0}}
    return cfg


def test_tracks_split_the_keyword_universe():
    cfg = _cmp_cfg()
    main = {k["cluster"] for k in kw.shortlist(universe(), [], cfg, 10)}
    comp = {k["cluster"] for k in kw.shortlist(universe(), [], cfg, 10, track="comparison")}
    assert main == {"a"} and comp == {"b"}


def test_disabled_track_leaves_main_untouched():
    cfg = _cmp_cfg()
    cfg["comparison_track"]["enabled"] = False
    assert not kw.comparison_clusters(cfg)
    assert {k["cluster"] for k in kw.shortlist(universe(), [], cfg, 10)} == {"a", "b"}
    assert kw.shortlist(universe(), [], cfg, 10, track="comparison") == []


def test_list_bonus_favors_roundups():
    cfg = _cmp_cfg()
    data = universe()
    data["keywords"].append({"keyword": "best calm apps", "cluster": "b", "volume_tier": 2, "difficulty_tier": 1,
                             "business_fit": 5, "content_type": "list", "status": "queued"})
    assert kw.shortlist(data, [], cfg, 1, track="comparison")[0]["keyword"] == "best calm apps"


def test_cadence_is_per_track(monkeypatch):
    from derot_seo import pipeline
    from derot_seo.util import today
    cfg = _cmp_cfg()
    d = today().isoformat()
    main_posts = [{"slug": f"m{i}", "cluster": "a", "date_published": d} for i in range(3)]
    assert "weekly cap" in pipeline.cadence_block(cfg, main_posts, "main")
    assert pipeline.cadence_block(cfg, main_posts, "comparison") is None
    comp_posts = [{"slug": f"c{i}", "cluster": "b", "date_published": d} for i in range(2)]
    assert "weekly cap" in pipeline.cadence_block(cfg, comp_posts, "comparison")
    assert pipeline.cadence_block(cfg, comp_posts, "main") is None


def test_comparison_posts_skip_social():
    from derot_seo import social
    cfg = _cmp_cfg()
    assert social.wants_social({"cluster": "a"}, cfg)
    assert not social.wants_social({"cluster": "b"}, cfg)
    cfg["comparison_track"]["social"] = True
    assert social.wants_social({"cluster": "b"}, cfg)
