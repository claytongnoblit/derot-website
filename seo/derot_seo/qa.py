"""Deterministic quality gate. Every check here is cheap and objective; the
editorial judgment lives in the model review (generate.py). Errors block
publishing; warnings are passed to the reviewer and the run log."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

from .util import jaccard, tokens, word_count

DASH_RE = re.compile(r"\s*[—–]\s*")
LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
RAW_HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")

BANNED = [
    # brand rules
    (r"\bphone addiction\b", "brand: 'phone addiction' framing"),
    (r"\baddicts?\b", "brand: 'addict' framing"),
    (r"\bcrush (your )?screen time\b", "brand: hustle framing"),
    (r"\btake back control\b", "brand: 'take back control'"),
    (r"\bbeat your phone\b", "brand: 'beat your phone'"),
    (r"\bfind your zen\b", "brand: 'find your zen'"),
    (r"\bbreathe before (you|it) open", "brand: DeRot is mid-scroll, not breathe-before-open"),
    (r"\bscreen time control\b", "brand: category is not screen time control"),
    # AI-writing tells
    (r"\bdelve", "style: 'delve'"),
    (r"\bin today's (fast-paced|digital|modern)", "style: cliche opener"),
    (r"\bin the digital age\b", "style: cliche"),
    (r"\bgame[- ]changer\b", "style: 'game-changer'"),
    (r"\btapestry\b", "style: 'tapestry'"),
    (r"\bembark\b", "style: 'embark'"),
    (r"\bunleash", "style: 'unleash'"),
    (r"\belevate your\b", "style: 'elevate your'"),
    (r"\bnavigat(e|ing) the complexities\b", "style: cliche"),
    (r"\bit'?s (important|worth) (to note|noting)\b", "style: filler"),
    (r"\bin conclusion\b", "style: 'in conclusion'"),
    (r"\blet'?s dive in\b", "style: 'let's dive in'"),
    (r"\bbuckle up\b", "style: 'buckle up'"),
    # medical overclaiming
    (r"\bcures?\b", "caution: 'cure' wording (fine only when saying breathing is not a cure)"),
    (r"\b(treats?|treatment for) (anxiety|panic|insomnia|depression|adhd|ptsd)\b", "caution: treatment wording (fine in a caveat; the reviewer judges claims)"),
    (r"\bguarantee[sd]?\b", "caution: guarantee wording"),
]
LOCK_RE = re.compile(r"\bDeRot\b([^.!?\n]{0,60}?)\block(s|ed|ing)?\b(?!\s*screen)", re.I)
NEGATION_RE = re.compile(r"\b(never|not|no|won'?t|doesn'?t|don'?t|without|isn'?t|instead of)\b|n't\b", re.I)


def lock_violations(text: str) -> list[str]:
    """Sentences where DeRot itself is said to lock something (negations like
    'DeRot never locks you out' are allowed by the copy rules)."""
    out = []
    for m in LOCK_RE.finditer(text):
        if not NEGATION_RE.search(m.group(1)):
            out.append(m.group(0))
    return out


@dataclass
class QAResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_text(self) -> str:
        parts = [f"ERROR: {e}" for e in self.errors] + [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(parts) or "No automated issues."


def normalize_url(url: str) -> str:
    try:
        u = urlparse(url.strip())
    except ValueError:
        return url.strip()
    q = [(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith("utm_")]
    path = u.path.rstrip("/") or "/"
    host = u.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Known aliases for the same document.
    m = re.match(r"^/pmc/articles/(PMC\d+)", path, re.I)
    if host == "ncbi.nlm.nih.gov" and m:
        host, path = "pmc.ncbi.nlm.nih.gov", f"/articles/{m.group(1).upper()}"
    m = re.match(r"^/pubmed/(\d+)", path)
    if host == "ncbi.nlm.nih.gov" and m:
        host, path = "pubmed.ncbi.nlm.nih.gov", f"/{m.group(1)}"
    if host == "pmc.ncbi.nlm.nih.gov":
        path = re.sub(r"^/articles/(pmc\d+)", lambda x: "/articles/" + x.group(1).upper(), path, flags=re.I)
    if host in ("dx.doi.org",):
        host = "doi.org"
    return urlunparse(("https", host, path, "", urlencode(q), ""))


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_authoritative(url: str, domains: list[str]) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in domains) or host.endswith(".gov") or host.endswith(".edu")


def fix_dashes(text: str) -> tuple[str, int]:
    """Replace em/en dashes used as punctuation. Number ranges become hyphens."""
    n = len(re.findall(r"[—–]", text))
    if not n:
        return text, 0
    text = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", text)
    text = DASH_RE.sub(", ", text)
    text = re.sub(r",\s*([.,;:!?])", r"\1", text)
    return text, n


def autofix(article: dict) -> list[str]:
    """Mechanical fixes applied before checks. Returns descriptions."""
    fixes = []
    for key in ("title", "meta_title", "meta_description", "dek", "body_markdown"):
        if key in article and isinstance(article[key], str):
            article[key], n = fix_dashes(article[key])
            if n:
                fixes.append(f"replaced {n} dash(es) in {key}")
    for listkey in ("key_takeaways",):
        new = []
        for t in article.get(listkey, []):
            t2, n = fix_dashes(t)
            if n:
                fixes.append(f"replaced {n} dash(es) in {listkey}")
            new.append(t2)
        article[listkey] = new
    for listkey in ("howto_steps",):
        new = []
        for t in article.get(listkey, []):
            t2, n = fix_dashes(t)
            if n:
                fixes.append(f"replaced {n} dash(es) in {listkey}")
            new.append(t2)
        article[listkey] = new
    social = article.get("social") or {}
    for k, v in list(social.items()):
        if isinstance(v, str):
            social[k], n = fix_dashes(v)
            if n:
                fixes.append(f"replaced {n} dash(es) in social.{k}")
    for f in article.get("faqs", []):
        for k in ("question", "answer"):
            f[k], n = fix_dashes(f[k])
            if n:
                fixes.append(f"replaced {n} dash(es) in faq")
    # The template renders the H1; strip any H1 the model added.
    body = article.get("body_markdown", "")
    if re.search(r"^# ", body, re.M):
        article["body_markdown"] = re.sub(r"^# .*\n?", "", body, flags=re.M).lstrip()
        fixes.append("removed H1 from body")
    return fixes


def check(article: dict, plan: dict, brief: dict, inventory: dict, cfg: dict,
          check_links: bool = True) -> QAResult:
    q = cfg["quality"]
    r = QAResult()
    r.fixes = autofix(article)
    body = article.get("body_markdown", "")
    pk = plan["primary_keyword"]

    # --- metadata
    mt, md = article.get("meta_title", ""), article.get("meta_description", "")
    if not 20 <= len(mt) <= 62:
        r.errors.append(f"meta_title length {len(mt)} (want 20-62): {mt!r}")
    if not 110 <= len(md) <= 160:
        r.errors.append(f"meta_description length {len(md)} (want 110-160)")
    if len(article.get("title", "")) > 90:
        r.errors.append("title over 90 characters")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", article.get("slug", "")) or len(article.get("slug", "")) > 70:
        r.errors.append(f"bad slug {article.get('slug')!r}")

    # --- keyword placement (variant-tolerant: token overlap)
    def has_kw(text: str, thresh: float = 0.75) -> bool:
        kt = tokens(pk)
        if not kt:
            return True
        return len(kt & tokens(text)) / len(kt) >= thresh

    first100 = " ".join(re.sub(r"[#*\[\]()]", " ", body).split()[:100])
    for label, text in (("title", article.get("title", "")), ("first 100 words", first100)):
        if not has_kw(text, 0.5):
            r.errors.append(f"primary keyword '{pk}' not reflected in the {label}")
        elif not has_kw(text, 0.75):
            r.warnings.append(f"primary keyword '{pk}' only partly reflected in the {label}")
    if not has_kw(md, 0.6):
        r.warnings.append("primary keyword weak in meta description")
    h2s = re.findall(r"^## (.+)$", body, re.M)
    if len(h2s) < 4:
        r.errors.append(f"only {len(h2s)} H2 sections (want >= 4)")
    if h2s and not any(has_kw(h, 0.5) for h in h2s):
        r.warnings.append("no H2 reflects the primary keyword")

    # --- length
    wc = word_count(body)
    lo, hi = q["word_count"].get(plan["content_type"], [1200, 2600])
    if wc < lo:
        r.errors.append(f"body is {wc} words (min {lo} for {plan['content_type']})")
    elif wc > hi * 1.25:
        r.warnings.append(f"body is {wc} words (target max {hi})")
    article["word_count"] = wc

    # --- answer-first + structure
    first_para = next((p for p in body.split("\n\n") if p.strip() and not p.startswith("#")), "")
    fp_wc = word_count(first_para)
    if fp_wc > 90:
        r.warnings.append(f"opening paragraph is {fp_wc} words; answer-first wants 40-60")
    kt = article.get("key_takeaways", [])
    if not 3 <= len(kt) <= 6:
        r.errors.append(f"{len(kt)} key takeaways (want 3-6)")
    faqs = article.get("faqs", [])
    if not 3 <= len(faqs) <= 8:
        r.errors.append(f"{len(faqs)} FAQs (want 3-8)")
    for f in faqs:
        if word_count(f.get("answer", "")) < 15:
            r.warnings.append(f"FAQ answer too thin: {f.get('question')!r}")

    # --- banned language
    full = "\n".join([article.get("title", ""), md, article.get("dek", ""), body, *kt,
                      *article.get("howto_steps", []),
                      *[v for v in (article.get("social") or {}).values() if isinstance(v, str)],
                      *[f["question"] + " " + f["answer"] for f in faqs]])
    for pat, label in BANNED:
        m = re.search(pat, full, re.I)
        if m:
            target = r.errors if label.startswith(("brand", "health")) else r.warnings
            target.append(f"{label}: '{m.group(0)}'")
    for v in lock_violations(full):
        r.errors.append(f"brand: DeRot described as locking: '{v.strip()[:120]}' (say block or pause)")
    html_hits = RAW_HTML_RE.findall(full)
    if html_hits:
        r.errors.append(f"raw HTML is not allowed in article text: {html_hits[:3]}")
    if re.search(r"[—–]", full):
        r.errors.append("em/en dash remains")
    mentions = len(re.findall(r"\bDeRot\b", body))
    if mentions > q["max_product_mentions"]:
        r.errors.append(f"DeRot mentioned {mentions}x in body (max {q['max_product_mentions']})")

    # --- links
    allowed_ext = {normalize_url(s["url"]): s for s in brief.get("sources", [])}
    internal_paths = set(inventory.keys())
    internal_count = 0
    external_used = set()
    for text, url in LINK_RE.findall(body):
        if url.startswith("#") or url.startswith("mailto:"):
            continue
        url = re.sub(r"^https?://(www\.)?derot\.org", "https://derot.org", url)
        if url.startswith("/") or url.startswith("https://derot.org"):
            path = url.replace("https://derot.org", "") or "/"
            path = path.split("#")[0]
            if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
                path += "/"
            if path not in internal_paths:
                r.errors.append(f"internal link to unknown page {url}")
            else:
                internal_count += 1
        elif url.startswith("http"):
            n = normalize_url(url)
            if n not in allowed_ext:
                r.errors.append(f"external link not from research sources: {url}")
            external_used.add(n)
        else:
            r.errors.append(f"malformed link target {url!r}")
        if text.strip().lower() in {"here", "click here", "this", "link", "this link"}:
            r.warnings.append(f"weak anchor text '{text}'")
    if internal_count < q["min_internal_links"]:
        r.errors.append(f"{internal_count} internal links (min {q['min_internal_links']})")

    # --- sources
    used_ids = set(article.get("sources_used", []))
    sources = [s for s in brief.get("sources", []) if s["id"] in used_ids or normalize_url(s["url"]) in external_used]
    article["sources"] = [{"title": s["title"], "url": s["url"], "publisher": s.get("publisher", ""),
                           "year": s.get("year", "")} for s in sources]
    if len(sources) < q["min_sources"]:
        r.errors.append(f"{len(sources)} sources cited (min {q['min_sources']})")
    auth = [s for s in sources if is_authoritative(s["url"], q["authoritative_domains"])]
    if len(auth) < q["min_authoritative_sources"]:
        r.errors.append(f"{len(auth)} authoritative sources (min {q['min_authoritative_sources']})")
    if len(external_used) < 2:
        r.errors.append("fewer than 2 inline citations in the body")

    if check_links:
        for s in sources:
            status = link_status(s["url"])
            if status in (404, 410):
                r.errors.append(f"source returns {status}: {s['url']}")
            elif status is None or status >= 500:
                r.warnings.append(f"source unreachable ({status}): {s['url']}")

    # --- duplicate title guard against existing posts
    for path, page in inventory.items():
        if page.get("type") == "post" and page.get("slug") != article.get("slug"):
            if jaccard(page["title"], article.get("title", "")) >= 0.8:
                r.errors.append(f"title too similar to existing post {path}")
    return r


_LINK_CACHE: dict[str, int | None] = {}


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _registry_status(url: str):
    """NCBI and many publishers block or mislead automated link checks (403s, fake 404s).
    For PubMed Central, PubMed, and DOI links, ask the official registries instead.
    Returns 200 / 404, None if the registry is unreachable, or "n/a" if not applicable."""
    n = normalize_url(url)
    m = re.match(r"^https://pmc\.ncbi\.nlm\.nih\.gov/articles/(?:PMC)?(\d+)", n, re.I)
    db = "pmc" if m else None
    if not m:
        m = re.match(r"^https://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", n)
        db = "pubmed" if m else None
    if db:
        for attempt in range(3):
            time.sleep(0.4 + attempt)  # NCBI allows ~3 requests/second without an API key
            try:
                r = requests.get(EUTILS, params={"db": db, "id": m.group(1), "retmode": "json"}, timeout=20)
                data = r.json()
            except (requests.RequestException, ValueError):
                continue
            if "result" not in data:  # rate limited or service error: unknown, never "dead"
                continue
            rec = data["result"].get(m.group(1)) or {}
            return 200 if rec.get("title") and not rec.get("error") else 404
        return None
    m = re.match(r"^https://doi\.org/(10\..+)$", n)
    if m:
        try:
            r = requests.get(f"https://api.crossref.org/works/{m.group(1)}", timeout=20)
            return 200 if r.status_code == 200 else (404 if r.status_code == 404 else None)
        except requests.RequestException:
            return None
    return "n/a"


def link_status(url: str) -> int | None:
    if url in _LINK_CACHE:
        return _LINK_CACHE[url]
    reg = _registry_status(url)
    if reg != "n/a":
        _LINK_CACHE[url] = reg
        return reg
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DeRotLinkCheck/1.0; +https://derot.org)"}
    status = None
    try:
        resp = requests.head(url, allow_redirects=True, timeout=15, headers=headers)
        status = resp.status_code
        if status in (403, 405, 429) or status >= 400:
            resp = requests.get(url, allow_redirects=True, timeout=20, headers=headers, stream=True)
            status = resp.status_code
            resp.close()
    except requests.RequestException:
        status = None
    _LINK_CACHE[url] = status
    return status
