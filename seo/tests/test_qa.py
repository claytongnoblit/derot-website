from derot_seo import qa
from derot_seo.util import load_config

CFG = load_config()
PLAN = {"primary_keyword": "box breathing", "content_type": "how-to"}
BRIEF = {"sources": [
    {"id": "S1", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "title": "A", "publisher": "P", "year": "2023"},
    {"id": "S2", "url": "https://www.frontiersin.org/articles/x", "title": "B", "publisher": "F", "year": "2018"},
    {"id": "S3", "url": "https://www.health.harvard.edu/y", "title": "C", "publisher": "H", "year": "2020"},
]}
INV = {"/": {"title": "Home", "type": "home"}, "/tools/box-breathing-timer/": {"title": "Box", "type": "tool"},
       "/about/": {"title": "About", "type": "about"}}


def article(body_extra="", **over):
    sections = "\n\n".join(
        f"## Box breathing step {i}\n\n" + ("Slow breathing helps you settle when the feed has wound you up. " * 40)
        for i in range(5))
    body = ("Box breathing is a simple four-count breathing pattern. "
            "It helps you settle in about a minute. [Research](https://pubmed.ncbi.nlm.nih.gov/1/) and "
            "[a review](https://www.frontiersin.org/articles/x) support slow breathing. Try the "
            "[box breathing timer](/tools/box-breathing-timer/) or read [about us](/about/).\n\n"
            + sections + body_extra)
    a = {"title": "Box breathing: how to do it", "meta_title": "Box Breathing: How to Do It | DeRot",
         "meta_description": "Box breathing explained: how to do the 4-4-4-4 pattern, why it helps you feel calmer, and when to use it during a stressful scroll.",
         "slug": "box-breathing", "dek": "d", "key_takeaways": ["a", "b", "c"], "body_markdown": body,
         "faqs": [{"question": f"Q{i}?", "answer": "An answer that is long enough to stand on its own as a reply."}
                  for i in range(3)],
         "sources_used": ["S1", "S2", "S3"]}
    a.update(over)
    return a


def test_clean_article_passes():
    r = qa.check(article(), PLAN, BRIEF, INV, CFG, check_links=False)
    assert r.ok, r.errors


def test_dashes_are_autofixed():
    a = article(title="Box breathing — how to do it")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert "—" not in a["title"] and r.ok, r.errors
    assert any("dash" in f for f in r.fixes)


def test_number_range_dash_becomes_hyphen():
    assert qa.fix_dashes("4–6 breaths")[0] == "4-6 breaths"


def test_invented_external_link_blocked():
    a = article(body_extra="\n\nSee [this](https://made-up.example.com/study).")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("not from research sources" in e for e in r.errors)


def test_unknown_internal_link_blocked():
    a = article(body_extra="\n\nRead [our guide](/blog/does-not-exist/).")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("unknown page" in e for e in r.errors)


def test_lock_language_blocked():
    a = article(body_extra="\n\nDeRot locks your apps until you breathe.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("lock" in e for e in r.errors)


def test_brand_phrase_blocked():
    a = article(body_extra="\n\nThis will help your phone addiction.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("phone addiction" in e for e in r.errors)


def test_short_article_blocked():
    a = article(body_markdown="## A\n\nToo short.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert not r.ok


def test_h1_removed():
    a = article()
    a["body_markdown"] = "# Title\n\n" + a["body_markdown"]
    qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert not a["body_markdown"].startswith("# ")


def test_normalize_url():
    assert qa.normalize_url("http://www.Example.com/a/?utm_source=x#frag") == "https://example.com/a"


# ---- regressions from the independent review -------------------------------
def test_plural_and_verb_variants_match_keyword():
    plan = {"primary_keyword": "breathing exercises for anxiety", "content_type": "how-to"}
    a = article(title="A breathing exercise for anxiety you can do anywhere")
    a["body_markdown"] = "A breathing exercise for anxiety can help. " + a["body_markdown"]
    r = qa.check(a, plan, BRIEF, INV, CFG, check_links=False)
    assert not any("not reflected in the title" in e for e in r.errors), r.errors


def test_hyphen_and_space_variants_match():
    from derot_seo.util import tokens
    assert tokens("4-7-8 breathing") == tokens("4 7 8 breathing")


def test_treatment_caveat_is_not_blocking():
    a = article(body_extra="\n\nBreathing is not a treatment for anxiety disorders; CBT is the first-line treatment for insomnia.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert r.ok, r.errors
    assert any("treatment" in w for w in r.warnings)


def test_lock_negation_and_lock_screen_allowed():
    a = article(body_extra="\n\nDeRot never locks you out. Unlike blockers that lock you out, it pauses. "
                           "DeRot appears over your lock screen notifications.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert not any("locking" in e for e in r.errors), r.errors


def test_lock_claim_in_howto_steps_blocked():
    a = article(howto_steps=["Open the app.", "DeRot locks Instagram for ten minutes."])
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("locking" in e for e in r.errors)


def test_raw_html_blocked():
    a = article(body_extra='\n\n<script>alert(1)</script>')
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert any("raw HTML" in e for e in r.errors)


def test_less_than_in_prose_is_not_html():
    a = article(body_extra="\n\nAim for <6 breaths a minute and 5 < 6.")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert not any("raw HTML" in e for e in r.errors)


def test_url_with_parentheses_matches_source():
    brief = {"sources": BRIEF["sources"] + [{"id": "S4", "url": "https://www.thelancet.com/article/S0140-6736(20)30001-1/fulltext",
                                             "title": "L", "publisher": "Lancet", "year": "2020"}]}
    a = article(body_extra="\n\nSee [the Lancet paper](https://www.thelancet.com/article/S0140-6736(20)30001-1/fulltext).")
    r = qa.check(a, PLAN, brief, INV, CFG, check_links=False)
    assert r.ok, r.errors


def test_anchor_mailto_and_www_links_ok():
    a = article(body_extra="\n\nJump to [the steps](#box-breathing-step-1), email [us](mailto:support@derot.org), "
                           "or try the [timer](https://www.derot.org/tools/box-breathing-timer).")
    r = qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert r.ok, r.errors


def test_dashes_fixed_in_social_and_howto():
    a = article(howto_steps=["Breathe in — slowly"], social={"x_post": "Calm — fast {url}"})
    qa.check(a, PLAN, BRIEF, INV, CFG, check_links=False)
    assert "—" not in a["howto_steps"][0] and "—" not in a["social"]["x_post"]


def test_pmc_and_pubmed_aliases_are_the_same_source():
    assert qa.normalize_url("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11077410/") == \
        qa.normalize_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC11077410")
    assert qa.normalize_url("https://www.ncbi.nlm.nih.gov/pubmed/30245619") == \
        qa.normalize_url("https://pubmed.ncbi.nlm.nih.gov/30245619/")
    assert qa.normalize_url("https://dx.doi.org/10.1/x") == qa.normalize_url("https://doi.org/10.1/x")


def test_apply_edits():
    from derot_seo.generate import apply_edits
    a = {"body_markdown": "Dutch adults aged 16 to 93 took part.", "title": "T", "faqs":
         [{"question": "Q?", "answer": "lower life satisfaction"}], "key_takeaways": ["k"]}
    applied, remaining = apply_edits(a, [
        {"issue": "16 is not adult", "find": "Dutch adults aged", "replace": "Dutch people aged"},
        {"issue": "faq", "find": "lower life satisfaction", "replace": "lower wellbeing"},
        {"issue": "needs rewrite", "find": "", "replace": ""},
        {"issue": "stale find", "find": "not in the text", "replace": "x"}])
    assert a["body_markdown"].startswith("Dutch people aged") and a["faqs"][0]["answer"] == "lower wellbeing"
    assert len(applied) == 2 and [b["issue"] for b in remaining] == ["needs rewrite", "stale find"]


def test_registry_status_routes(monkeypatch):
    import derot_seo.qa as Q
    seen = []

    class R:
        def __init__(self, status=200, data=None):
            self.status_code, self._d = status, data or {}

        def json(self):
            return self._d

    def fake_get(url, params=None, timeout=None, **kw):
        seen.append((url, params))
        if "eutils" in url:
            ok = params["id"] == "10604906"
            return R(data={"result": {params["id"]: {"title": "T"} if ok else {"error": "cannot get document summary"}}})
        return R(200 if url.endswith("10.3389/x") else 404)
    monkeypatch.setattr(Q.requests, "get", fake_get)
    monkeypatch.setattr(Q.time, "sleep", lambda s: None)
    assert Q._registry_status("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10604906/") == 200
    assert Q._registry_status("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/") == 404
    assert Q._registry_status("https://dx.doi.org/10.3389/x") == 200
    assert Q._registry_status("https://doi.org/10.9/nope") == 404
    assert Q._registry_status("https://example.com/a") == "n/a"
    assert seen[0][1] == {"db": "pmc", "id": "10604906", "retmode": "json"}


def test_registry_rate_limit_is_unknown_not_dead(monkeypatch):
    import derot_seo.qa as Q

    class R:
        status_code = 200

        def json(self):
            return {"error": "API rate limit exceeded"}
    monkeypatch.setattr(Q.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(Q.time, "sleep", lambda s: None)
    assert Q._registry_status("https://pubmed.ncbi.nlm.nih.gov/28271575/") is None
