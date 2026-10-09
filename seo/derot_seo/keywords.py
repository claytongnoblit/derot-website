"""Keyword universe: scoring, shortlisting, cannibalization guard, and
weekly expansion (Google Autocomplete + Search Console + Claude triage)."""
from __future__ import annotations

import json
import random
import time
from typing import Optional

import requests

from .util import DATA_DIR, jaccard, read_json, today, tokens, write_json

KEYWORDS_PATH = DATA_DIR / "keywords.json"
GSC_PATH = DATA_DIR / "gsc_snapshot.json"

# Status lifecycle:
#   queued -> published (primary keyword of a post)
#   queued -> covered   (secondary keyword folded into a post)
#   queued -> parked    (draft failed QA; retried later after `retry_after`)
#   tool                (served by a static /tools/ page, never planned)
#   rejected            (off-brand or cannibalizing, never planned)
PLANNABLE = {"queued", "parked"}


def load_universe() -> dict:
    data = read_json(KEYWORDS_PATH)
    if not data:
        raise FileNotFoundError(f"{KEYWORDS_PATH} missing")
    for k in data["keywords"]:
        k.setdefault("status", "queued")
        if k.get("content_type") == "tool" and k["status"] == "queued":
            k["status"] = "tool"
    return data


def save_universe(data: dict) -> None:
    write_json(KEYWORDS_PATH, data)


def cluster_map(data: dict) -> dict:
    return {c["id"]: c for c in data["clusters"]}


def _cannibalizes(keyword: str, posts: list[dict]) -> Optional[str]:
    """Same search intent as an existing post's target: identical stemmed token sets
    ("box breathing" == "how to do box breathing"). Narrower or broader variants are
    NOT covered (a "4-7-8 breathing dizzy" post must not swallow "4-7-8 breathing");
    they are scored down by similarity instead and the planner judges intent."""
    kt = tokens(keyword)
    for p in posts:
        for t in [p.get("primary_keyword", "")] + p.get("secondary_keywords", []):
            if t and (t.strip().lower() == keyword.strip().lower() or (kt and tokens(t) == kt)):
                return p["slug"]
    return None


def _max_similarity(keyword: str, posts: list[dict]) -> float:
    best = 0.0
    for p in posts:
        for t in [p.get("primary_keyword", "")] + p.get("secondary_keywords", []):
            if t:
                best = max(best, jaccard(keyword, t))
    return best


def score_keyword(k: dict, data: dict, posts: list[dict], cfg: dict, gsc_queries: dict) -> tuple[float, list[str]]:
    w = cfg["keywords"]["weights"]
    clusters = cluster_map(data)
    why = []
    s = (w["business_fit"] * k.get("business_fit", 3)
         + w["volume"] * k.get("volume_tier", 2)
         + w["ease"] * (6 - k.get("difficulty_tier", 3)))
    c = clusters.get(k.get("cluster"), {})
    s += w["cluster_priority"] * c.get("priority", 3)

    month = today().month
    nxt = month % 12 + 1
    seas = k.get("seasonality") or []
    if month in seas or nxt in seas:
        s += w["seasonal_bonus"]
        why.append("seasonal")

    q = gsc_queries.get(k["keyword"].lower())
    if q and q.get("impressions", 0) >= 20:
        s += w["gsc_bonus"]
        why.append(f"gsc {q['impressions']} impr @ pos {q.get('position', 0):.0f}")

    sim = _max_similarity(k["keyword"], posts)
    if sim >= 0.4:
        s -= w.get("similarity_penalty", 6.0) * sim
        why.append(f"similar to existing post ({sim:.2f})")

    recent = posts[:4]
    same = sum(1 for p in recent if p.get("cluster") == k.get("cluster"))
    if same:
        s -= w["recent_cluster_penalty"] * same
        why.append(f"cluster used {same}x recently")

    cluster_posts = [p for p in posts if p.get("cluster") == k.get("cluster")]
    has_pillar = any(p.get("content_type") == "pillar" for p in cluster_posts)
    if k.get("content_type") == "pillar":
        if has_pillar:
            s -= 100  # one pillar per cluster
        elif len(cluster_posts) >= 2:
            s += w["pillar_ready_bonus"]
            why.append("pillar ready")
        else:
            s -= w["pillar_too_early_penalty"]
    if k.get("status") == "parked":
        s -= 3
    return round(s, 2), why


def shortlist(data: dict, posts: list[dict], cfg: dict, n: Optional[int] = None) -> list[dict]:
    n = n or cfg["keywords"]["shortlist_size"]
    gsc = (read_json(GSC_PATH, {}) or {}).get("queries", {})
    out = []
    for k in data["keywords"]:
        if k.get("status") not in PLANNABLE:
            continue
        if k.get("business_fit", 3) <= 2:
            continue
        if k.get("status") == "parked" and k.get("retry_after", "") > today().isoformat():
            continue
        clash = _cannibalizes(k["keyword"], posts)
        if clash:
            k["status"] = "covered"
            k["covered_by"] = clash
            continue
        sc, why = score_keyword(k, data, posts, cfg, gsc)
        out.append({**k, "score": sc, "why": why})
    out.sort(key=lambda x: (-x["score"], x["keyword"]))
    return out[:n]


def mark(data: dict, keyword: str, status: str, **extra) -> None:
    for k in data["keywords"]:
        if k["keyword"].lower() == keyword.lower():
            k["status"] = status
            k.update(extra)
            return
    # Planner may phrase a keyword slightly differently; add it so state is tracked.
    data["keywords"].append({"keyword": keyword.lower(), "status": status, "source": "planner", **extra})


def cluster_keywords(data: dict, cluster_id: str) -> list[str]:
    return [k["keyword"] for k in data["keywords"] if k.get("cluster") == cluster_id]


# ---------------------------------------------------------------- expansion
AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
MODIFIERS = ["", "how to ", "why ", "what is ", "best ", "does "]


def autocomplete(query: str, timeout: float = 8.0) -> list[str]:
    try:
        r = requests.get(AUTOCOMPLETE_URL, params={"client": "firefox", "hl": "en", "gl": "us", "q": query},
                         timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (DeRot keyword research)"})
        if r.status_code != 200:
            return []
        data = json.loads(r.text)
        return [s.lower().strip() for s in data[1] if isinstance(s, str)]
    except Exception:
        return []


def gather_candidates(data: dict, cfg: dict, rng: Optional[random.Random] = None) -> list[str]:
    rng = rng or random.Random(today().isoformat())
    exp = cfg["keywords"]["expansion"]
    existing = {k["keyword"].lower() for k in data["keywords"]}
    seeds_pool = [k["keyword"] for k in data["keywords"] if k.get("business_fit", 0) >= 4]
    seeds_pool += [c["pillar_keyword"] for c in data["clusters"]]
    seeds = rng.sample(seeds_pool, min(exp["seeds_per_run"], len(seeds_pool)))
    found: list[str] = []
    for seed in seeds:
        for mod in MODIFIERS[: 3]:
            q = (mod + seed) if not seed.startswith(mod.strip()) else seed
            for s in autocomplete(q):
                if s not in existing and s not in found and 2 <= len(s.split()) <= 9:
                    found.append(s)
            time.sleep(0.4)
    gsc = (read_json(GSC_PATH, {}) or {}).get("queries", {})
    for q, row in gsc.items():
        if q not in existing and q not in found and row.get("impressions", 0) >= 10 and len(q.split()) >= 2:
            found.append(q)
    return found[:150]


TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keywords"],
    "properties": {
        "keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["keyword", "keep", "cluster", "intent", "volume_tier", "difficulty_tier",
                             "business_fit", "content_type", "angle"],
                "properties": {
                    "keyword": {"type": "string"},
                    "keep": {"type": "boolean"},
                    "cluster": {"type": "string"},
                    "intent": {"type": "string", "enum": ["informational", "commercial", "transactional", "navigational"]},
                    "volume_tier": {"type": "integer"},
                    "difficulty_tier": {"type": "integer"},
                    "business_fit": {"type": "integer"},
                    "content_type": {"type": "string", "enum": ["pillar", "how-to", "explainer", "comparison", "list", "faq", "glossary", "tool"]},
                    "angle": {"type": "string"},
                },
            },
        }
    },
}


def triage(llm, data: dict, candidates: list[str], cfg: dict, voice: str) -> list[dict]:
    if not candidates:
        return []
    clusters = "\n".join(f"- {c['id']}: {c['name']} ({c['description']})" for c in data["clusters"])
    prompt = f"""You maintain the keyword plan for derot.org. Triage these newly discovered search queries.

Clusters:
{clusters}

For each candidate decide keep=true only if: it is a real query a member of our audience would type, it fits a cluster, it is not a near-duplicate of an existing planned keyword, it is not a medical-treatment query that needs a clinician, and it is not off-brand. Estimate volume_tier and difficulty_tier (1-5, 5 = highest/hardest) from your knowledge; business_fit 1-5 (5 = reader very likely to love DeRot). Give a one-sentence DeRot angle.

Existing planned keywords (do not duplicate): {json.dumps([k['keyword'] for k in data['keywords']][:400])}

Candidates:
{json.dumps(candidates)}"""
    res = llm.structured("triage", "utility", voice, prompt, TRIAGE_SCHEMA, max_tokens=32000,
                         mock_hint={"candidates": candidates})
    exp = cfg["keywords"]["expansion"]
    valid_clusters = {c["id"] for c in data["clusters"]}
    existing = {k["keyword"].lower() for k in data["keywords"]}
    added = []
    for k in res.get("keywords", []):
        kw = k["keyword"].lower().strip()
        if (not k["keep"] or kw in existing or k["cluster"] not in valid_clusters
                or k["business_fit"] < exp["min_business_fit"]):
            continue
        rec = {
            "keyword": kw, "cluster": k["cluster"], "intent": k["intent"],
            "volume_tier": max(1, min(5, k["volume_tier"])),
            "difficulty_tier": max(1, min(5, k["difficulty_tier"])),
            "business_fit": max(1, min(5, k["business_fit"])),
            "content_type": k["content_type"], "angle": k["angle"], "seasonality": [],
            "status": "tool" if k["content_type"] == "tool" else "queued",
            "source": "expansion", "added": today().isoformat(),
        }
        data["keywords"].append(rec)
        existing.add(kw)
        added.append(rec)
        if len(added) >= exp["max_new_keywords_per_run"]:
            break
    return added
