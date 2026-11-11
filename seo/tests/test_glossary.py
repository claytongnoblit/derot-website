import json
import re

from derot_seo import glossary, qa, site
from derot_seo.util import SITE_DIR, load_posts


def _g():
    g = glossary.load_glossary()
    assert g, "seo/content/glossary.yaml missing"
    return g


def test_glossary_schema():
    g = _g()
    groups = {grp["id"] for grp in g["groups"]}
    ids = [t["id"] for t in g["terms"]]
    assert len(ids) == len(set(ids)), "duplicate glossary ids"
    for t in g["terms"]:
        assert re.fullmatch(r"[a-z0-9-]+", t["id"]), t["id"]
        assert t["group"] in groups, t["id"]
        assert t["definition"].strip() and t["term"].strip()
        assert len(t["definition"]) <= 260, f"{t['id']}: keep the definition to one quotable sentence"


def test_glossary_follows_copy_rules():
    g = _g()
    for t in g["terms"]:
        text = f"{t['term']} {t['definition']} {t.get('more', '')}"
        assert not re.search("[—–]", text), f"{t['id']}: dash"
        for pat, label in qa.BANNED:
            assert not re.search(pat, text, re.I), f"{t['id']}: {label}"
        assert not qa.lock_violations(text), t["id"]


def test_glossary_internal_links_resolve():
    g = _g()
    ids = {t["id"] for t in g["terms"]}
    for url in glossary.glossary_links(g):
        if url.startswith("#"):
            assert url[1:] in ids, url
        elif url.startswith("/"):
            path = url.split("#")[0]
            assert (SITE_DIR / path.lstrip("/") / "index.html").exists(), url
        else:
            assert url.startswith("https://"), url


def test_glossary_jsonld_and_inventory():
    g = _g()
    blocks = [json.loads(b.replace("<\\/", "</")) for b in glossary.glossary_jsonld(g, "https://derot.org")]
    assert blocks[0]["@type"] == "DefinedTermSet"
    assert len(blocks[0]["hasDefinedTerm"]) == len(g["terms"])
    inv = site.inventory(load_posts(), {})
    assert inv["/glossary/"]["type"] == "glossary"
    assert f"/glossary/#{g['terms'][0]['id']}" in inv


def test_glossary_in_sitemap_and_llms():
    g = _g()
    cfg = {"site": {"base_url": "https://derot.org"}}
    assert "https://derot.org/glossary/" in site.build_sitemap(cfg, [], {}, [], g)
    llms = site.build_llms(cfg, [], [], {}, g)
    assert "## Glossary" in llms and f"/glossary/#{g['terms'][0]['id']}" in llms
