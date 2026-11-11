"""Glossary page (/glossary/). Source of truth: seo/content/glossary.yaml (hand-edited).
Renders one page with a per-term anchor and DefinedTermSet structured data."""
from __future__ import annotations

import datetime as dt
import json
import re

import yaml

from .util import SITE_DIR

GLOSSARY_SRC = SITE_DIR / "seo" / "content" / "glossary.yaml"


def load_glossary() -> dict | None:
    if not GLOSSARY_SRC.exists():
        return None
    return yaml.safe_load(GLOSSARY_SRC.read_text(encoding="utf-8"))


def glossary_links(g: dict) -> list[str]:
    """Every link in the glossary (definitions and see-also), for link checks."""
    out = []
    for t in g["terms"]:
        out += re.findall(r"\]\(([^)\s]+)\)", t.get("more", ""))
        out += [s["url"] for s in t.get("see", [])]
    return out


def glossary_jsonld(g: dict, base: str) -> list[str]:
    url = f"{base}/glossary/"
    dump = lambda o: json.dumps(o, ensure_ascii=False, indent=1).replace("</", "<\\/")
    terms = []
    for t in g["terms"]:
        d = {"@type": "DefinedTerm", "@id": f"{url}#{t['id']}", "name": t["term"],
             "description": t["definition"], "url": f"{url}#{t['id']}",
             "inDefinedTermSet": url + "#set"}
        if t.get("aka"):
            d["alternateName"] = t["aka"]
        terms.append(d)
    return [
        dump({"@context": "https://schema.org", "@type": "DefinedTermSet", "@id": url + "#set", "url": url,
              "name": g["title"], "description": g["meta_description"], "inLanguage": "en-US",
              "dateModified": g["updated"],
              "publisher": {"@type": "Organization", "name": "DeRot", "url": base + "/"},
              "hasDefinedTerm": terms}),
        dump({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
            {"@type": "ListItem", "position": 2, "name": "Glossary", "item": url}]}),
    ]


def render_glossary(env, common: dict, cfg: dict, render_markdown) -> str | None:
    g = load_glossary()
    if not g:
        return None
    base = cfg["site"]["base_url"]
    terms = []
    for t in g["terms"]:
        more_html, _ = render_markdown(t.get("more", ""))
        terms.append({**t, "more_html": more_html})
    groups = [{**grp, "terms": sorted([t for t in terms if t["group"] == grp["id"]], key=lambda t: t["term"].lower())}
              for grp in g["groups"]]
    d = dt.date.fromisoformat(g["updated"])
    return env.get_template("glossary.html").render(
        **common, g=g, groups=groups, terms=terms, updated_human=f"{d.strftime('%B')} {d.day}, {d.year}",
        meta_title=g["meta_title"], meta_description=g["meta_description"], og_title=g["title"],
        canonical=f"{base}/glossary/", og_image=cfg["site"]["default_og_image"],
        jsonld=glossary_jsonld(g, base))
