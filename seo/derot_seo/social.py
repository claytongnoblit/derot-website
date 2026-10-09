"""Social posting for every blog article: Instagram, Threads, and a Facebook Page.

prepare  When an article publishes, Claude writes platform copy and carousel
         slide text from the article (and only the article), the brand QA gate
         checks it, slides are rendered to img/social/<slug>/, and a package is
         queued in seo/content/social/<slug>.json with a time slot per platform.
post     The hourly "SEO social" workflow posts whatever is due once the images
         are live on derot.org, and records each post's id and permalink back
         into the package (so a package is also the audit trail).

The package JSON is the source of truth. To hold a post, set "approved": false
or a platform's "status": "skipped". To change copy, edit the JSON (re-run
`social render` if you changed slide text).
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
from pathlib import Path
from typing import Callable, Optional

import requests

from . import qa, site
from . import social_images as simg
from .util import (OUT_DIR, POSTS_DIR, SEO_DIR, SITE_DIR, load_posts, now_utc, read_json, write_json)

SOCIAL_DIR = SEO_DIR / "content" / "social"
IMG_DIR = SITE_DIR / "img" / "social"
PLATFORMS = ("facebook", "threads", "instagram")
EXIT_OK, EXIT_FAILED, EXIT_NEEDS_REVIEW = 0, 30, 31

THREADS_MAX_BYTES = 500
IG_CAPTION_MAX = 2200

COPY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["cover_headline", "points", "instagram_caption", "instagram_hashtags",
                 "threads_text", "facebook_message"],
    "properties": {
        "cover_headline": {"type": "string"},
        "points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["headline", "body"],
            "properties": {"headline": {"type": "string"}, "body": {"type": "string"}}}},
        "instagram_caption": {"type": "string"},
        "instagram_hashtags": {"type": "array", "items": {"type": "string"}},
        "threads_text": {"type": "string"},
        "facebook_message": {"type": "string"},
    },
}


def scfg(cfg: dict) -> dict:
    return cfg.get("social") or {}


def package_path(slug: str) -> Path:
    return SOCIAL_DIR / f"{slug}.json"


def load_package(slug: str) -> Optional[dict]:
    return read_json(package_path(slug))


def save_package(pkg: dict) -> None:
    write_json(package_path(pkg["slug"]), pkg)


def article_url(cfg: dict, slug: str) -> str:
    return f"{cfg['site']['base_url']}/blog/{slug}/"


# ------------------------------------------------------------ copy
def _article_text(post: dict) -> str:
    parts = [post["title"], post.get("dek", ""), post["body_markdown"], *post.get("key_takeaways", []),
             *post.get("howto_steps", [])]
    for f in post.get("faqs", []):
        parts += [f["question"], f["answer"]]
    return "\n".join(parts)


def _prompt(post: dict, cfg: dict, tool: dict) -> str:
    sc = scfg(cfg)
    n_min, n_max = sc.get("points", [3, 5])
    faqs = "\n".join(f"Q: {f['question']}\nA: {f['answer']}" for f in post.get("faqs", []))
    return f"""Turn this derot.org article into social posts for Instagram, Threads, and Facebook.

Every claim, number, and technique in the social copy must come from the article below. Do not add studies, statistics, mechanisms, or advice the article does not contain. Keep claims as cautious as the article keeps them. Never give medical advice or imply breathing treats a condition.

PLATFORM BRIEFS
- cover_headline: the first carousel slide. A hook that names the feeling or the promise, plain and specific, under 70 characters. Not a question. No clickbait.
- points: {n_min}-{n_max} carousel slides, one idea each, in reading order, that let someone get real value without leaving Instagram. headline under 48 characters; body under 190 characters, concrete (counts, timings, what it feels like). If the article teaches a technique, one slide gives the exact steps. Do not end with a call to action; a closing slide is added automatically.
- instagram_caption: the first line is a hook under 125 characters (it shows before "more"). Then 3-6 short paragraphs that add to the slides rather than repeat them. Links are not clickable on Instagram, so no URLs: the caption ends with the exact line "Full guide: link in bio." Under 1500 characters.
- instagram_hashtags: 3-5 specific, relevant tags without the # sign (for example breathwork, nervoussystemregulation). No generic tags like love or instagood, nothing about addiction or detox.
- threads_text: conversational, like a thoughtful person sharing one useful thing they learned. 1-3 short paragraphs, under 380 characters before the link. End with {{url}} on its own line (it is replaced with the article link). No hashtags.
- facebook_message: 2-4 short paragraphs, under 700 characters, warm and practical, ending with a line inviting people to read the full guide. Do not include the URL (the link card is attached automatically).

All copy follows the DeRot voice and hard copy rules: no em-dashes or en-dashes, never say DeRot "locks" anything, no "phone addiction" or detox framing, no guilt about screen time, none of the listed AI-writing tells. Mention DeRot the app at most once across each platform's copy, and only if it genuinely fits.

The free browser tool for this topic: {tool['label']} at derot.org{tool['url']}.

ARTICLE
Title: {post['title']}
Dek: {post.get('dek', '')}
Key takeaways:
{chr(10).join('- ' + t for t in post.get('key_takeaways', []))}
How-to steps: {'; '.join(post.get('howto_steps', [])) or 'none'}

{post['body_markdown']}

FAQ
{faqs}"""


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
URLISH_RE = re.compile(r"https?://\S+|www\.\S+|\b[a-z0-9-]+\.(?:com|net|io|co|app)\b", re.I)


def _strings(copy: dict) -> list[tuple[str, str]]:
    out = [("cover_headline", copy["cover_headline"]), ("instagram_caption", copy["instagram_caption"]),
           ("threads_text", copy["threads_text"]), ("facebook_message", copy["facebook_message"])]
    for i, p in enumerate(copy["points"]):
        out += [(f"points[{i}].headline", p["headline"]), (f"points[{i}].body", p["body"])]
    return out


def check_copy(copy: dict, post: dict, cfg: dict) -> tuple[list[str], list[str]]:
    """Mechanical fixes in place, then the brand and safety gate. Returns (errors, fixes)."""
    sc = scfg(cfg)
    errors, fixes = [], []
    url = article_url(cfg, post["slug"])

    for key in ("cover_headline", "instagram_caption", "threads_text", "facebook_message"):
        copy[key], n = qa.fix_dashes(copy[key].strip())
        if n:
            fixes.append(f"replaced {n} dash(es) in {key}")
    for p in copy["points"]:
        for k in ("headline", "body"):
            p[k], n = qa.fix_dashes(p[k].strip())
            if n:
                fixes.append(f"replaced {n} dash(es) in a slide")

    # Instagram: no links, always the bio line, clean hashtags.
    if not re.search(r"link in bio", copy["instagram_caption"], re.I):
        copy["instagram_caption"] = copy["instagram_caption"].rstrip() + "\n\nFull guide: link in bio."
        fixes.append("added 'link in bio' line")
    tags = []
    for t in copy["instagram_hashtags"]:
        t = re.sub(r"[^A-Za-z0-9_]", "", t.lstrip("#"))
        if t and t.lower() not in {x.lower() for x in tags} and not re.search(r"addict|detox", t, re.I):
            tags.append(t)
    copy["instagram_hashtags"] = tags[: sc.get("max_hashtags", 5)]

    # Threads: the link goes in the text.
    if "{url}" not in copy["threads_text"]:
        copy["threads_text"] = copy["threads_text"].rstrip() + "\n\n{url}"
        fixes.append("added link to threads_text")
    copy["facebook_message"] = copy["facebook_message"].replace("{url}", "").strip()

    # Limits
    n_min, n_max = sc.get("points", [3, 5])
    if not n_min <= len(copy["points"]) <= n_max:
        errors.append(f"points: need {n_min}-{n_max} slides, got {len(copy['points'])}")
    if len(copy["cover_headline"]) > 80:
        errors.append(f"cover_headline is {len(copy['cover_headline'])} chars (max 80)")
    for i, p in enumerate(copy["points"]):
        if len(p["headline"]) > 56:
            errors.append(f"points[{i}].headline is {len(p['headline'])} chars (max 56)")
        if len(p["body"]) > 220:
            errors.append(f"points[{i}].body is {len(p['body'])} chars (max 220)")
    ig = instagram_caption(copy)
    if len(ig) > IG_CAPTION_MAX:
        errors.append(f"instagram caption is {len(ig)} chars with hashtags (max {IG_CAPTION_MAX})")
    tb = len(copy["threads_text"].replace("{url}", url).encode("utf-8"))
    if tb > THREADS_MAX_BYTES:
        errors.append(f"threads_text is {tb} bytes with the link (Threads max {THREADS_MAX_BYTES})")
    if len(copy["facebook_message"]) > 1200:
        errors.append(f"facebook_message is {len(copy['facebook_message'])} chars (max 1200)")

    # Brand, safety, and grounding
    source = _article_text(post)
    source_nums = set(NUM_RE.findall(source))
    for field, text in _strings(copy):
        for pattern, label in qa.BANNED:
            m = re.search(pattern, text, re.I)
            if m and not (label.startswith("caution: 'cure'") and re.search(r"\bnot a cure\b", text, re.I)):
                errors.append(f"{field}: {label} ('{m.group(0)}')")
        lm = qa.LOCK_RE.search(text)
        if lm:
            errors.append(f"{field}: DeRot described with 'lock'")
        stripped = text.replace("{url}", "")
        for u in URLISH_RE.findall(stripped):
            if "derot.org" not in u.lower():
                errors.append(f"{field}: contains a link or domain ('{u}'); only derot.org is allowed")
        for num in NUM_RE.findall(stripped):
            if num not in source_nums and num not in {"1", "2", "3", "60"}:
                errors.append(f"{field}: the number {num} does not appear in the article (no new facts)")
    mentions = sum(len(re.findall(r"\bDeRot\b", t)) for f, t in _strings(copy) if f != "cover_headline")
    if mentions > 4:
        errors.append(f"DeRot is mentioned {mentions} times across the copy (max 4)")
    return sorted(set(errors)), fixes


def instagram_caption(copy: dict) -> str:
    tags = " ".join("#" + t for t in copy.get("instagram_hashtags", []))
    return copy["instagram_caption"].rstrip() + (f"\n\n{tags}" if tags else "")


def generate_copy(llm, cfg: dict, post: dict, tool: dict) -> tuple[dict, list[str], list[str]]:
    from .generate import brand_system
    system = brand_system(cfg)
    role = "social" if "social" in cfg["models"] else "writer"
    prompt = _prompt(post, cfg, tool)
    copy = llm.structured("social", role, system, prompt, COPY_SCHEMA, max_tokens=16000, mock_hint={"post": post})
    errors, fixes = check_copy(copy, post, cfg)
    if errors:
        retry = (prompt + "\n\nYOUR PREVIOUS DRAFT failed these automated checks. Fix every one and return the "
                 "complete copy again.\n" + "\n".join(f"- {e}" for e in errors)
                 + "\n\nPREVIOUS DRAFT:\n" + repr(copy))
        copy = llm.structured("social_revise", role, system, retry, COPY_SCHEMA, max_tokens=16000,
                              mock_hint={"post": post, "errors": errors})
        errors, more = check_copy(copy, post, cfg)
        fixes += more
    return copy, errors, fixes


# ------------------------------------------------------------ images
def render_images(pkg: dict, cfg: dict, post: dict) -> list[dict]:
    clusters = {c["id"]: c for c in (read_json(SEO_DIR / "data" / "keywords.json", {}) or {}).get("clusters", [])}
    kicker = clusters.get(post.get("cluster"), {}).get("name", "DeRot journal")
    tool = site.tool_for(post, cfg)
    copy = pkg["copy"]
    slides = [{"headline": copy["cover_headline"]}] + copy["points"]
    out_dir = IMG_DIR / pkg["slug"]
    paths = simg.render_set(slides, kicker, tool, out_dir)
    base = cfg["site"]["base_url"]
    alts = ([f"{copy['cover_headline']}. Cover slide of a DeRot carousel."]
            + [f"{p['headline']}. {p['body']}" for p in copy["points"]]
            + [f"Read the full guide free at derot.org/blog, link in bio. Try it now, free: "
               f"{tool['label']} at derot.org/tools."])
    return [{"path": str(p.relative_to(SITE_DIR)), "url": f"{base}/{p.relative_to(SITE_DIR).as_posix()}", "alt": a}
            for p, a in zip(paths, alts)]


# ------------------------------------------------------------ scheduling
def schedule_for(cfg: dict, post: dict, now: dt.datetime) -> dict[str, dict]:
    sc = scfg(cfg)
    lead = dt.timedelta(minutes=sc.get("min_lead_minutes", 60))
    pub = dt.date.fromisoformat(post["date_published"])
    out = {}
    for name in PLATFORMS:
        pc = (sc.get("platforms") or {}).get(name) or {}
        if not pc.get("enabled", False):
            continue
        hh, mm = (int(x) for x in str(pc.get("post_at", "16:30")).split(":"))
        slot = dt.datetime.combine(pub + dt.timedelta(days=int(pc.get("day_offset", 0))), dt.time(hh, mm),
                                   tzinfo=dt.timezone.utc)
        if slot < now + lead:
            slot = (now + lead).replace(second=0, microsecond=0)
        out[name] = {"status": "queued", "scheduled_at": slot.isoformat(), "attempts": 0}
    return out


# ------------------------------------------------------------ prepare
def prepare(post: dict, cfg: dict, llm, now: Optional[dt.datetime] = None, force: bool = False) -> dict:
    """Write copy, render slides, and queue one article. Returns the package."""
    now = now or now_utc()
    existing = load_package(post["slug"])
    if existing and existing.get("copy") and not force:
        return existing
    sc = scfg(cfg)
    tool = site.tool_for(post, cfg)
    try:
        copy, errors, fixes = generate_copy(llm, cfg, post, tool)
    except Exception as e:
        stub = existing or {"slug": post["slug"]}
        stub.update(prepare_error=f"{type(e).__name__}: {e}"[:500],
                    prepare_attempts=int(stub.get("prepare_attempts", 0)) + 1,
                    updated_at=now.replace(microsecond=0).isoformat())
        save_package(stub)
        raise
    pkg = {
        "slug": post["slug"], "title": post["title"], "url": article_url(cfg, post["slug"]),
        "created_at": now.replace(microsecond=0).isoformat(),
        "approved": (not sc.get("require_approval", False)) and not errors,
        "qa_errors": errors, "qa_fixes": fixes,
        "copy": copy,
        "platforms": schedule_for(cfg, post, now),
    }
    if errors:
        pkg["needs_review"] = True
    pkg["images"] = render_images(pkg, cfg, post)
    if existing:  # keep what already went out on a forced regenerate
        for name, st in (existing.get("platforms") or {}).items():
            if st.get("status") == "posted":
                pkg["platforms"][name] = st
    save_package(pkg)
    return pkg


def missing_posts(cfg: dict, now: dt.datetime) -> list[dict]:
    """Recent posts with no package yet (for example if prepare failed during publish)."""
    sc = scfg(cfg)
    cutoff = (now.date() - dt.timedelta(days=sc.get("backfill_days", 7))).isoformat()
    out = []
    for p in load_posts():
        if p["date_published"] < cutoff:
            continue
        pkg = load_package(p["slug"])
        if pkg is None or (not pkg.get("copy") and int(pkg.get("prepare_attempts", 0)) < 3):
            out.append(p)
    return out


# ------------------------------------------------------------ posting
def make_clients(cfg: dict, env: Optional[dict] = None, **client_kw) -> dict:
    """Clients for every platform whose credentials are present."""
    from .meta_api import FacebookPageClient, InstagramClient, ThreadsClient
    env = env if env is not None else os.environ
    ver = scfg(cfg).get("graph_version", "v26.0")
    out = {}
    page_token = env.get("META_PAGE_ACCESS_TOKEN")
    if page_token and env.get("META_PAGE_ID"):
        out["facebook"] = FacebookPageClient(page_token, env["META_PAGE_ID"], ver, **client_kw)
    if page_token and env.get("META_IG_USER_ID"):
        out["instagram"] = InstagramClient(page_token, env["META_IG_USER_ID"], ver, **client_kw)
    if env.get("THREADS_ACCESS_TOKEN"):
        out["threads"] = ThreadsClient(env["THREADS_ACCESS_TOKEN"], env.get("THREADS_USER_ID") or "me", **client_kw)
    return out


def images_live(images: list[dict], session=None) -> Optional[str]:
    """None if every image URL serves an image, else a reason."""
    http = session or requests
    for im in images:
        try:
            r = http.head(im["url"], timeout=20, allow_redirects=True)
        except requests.RequestException as e:
            return f"{im['url']}: {type(e).__name__}"
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return f"{im['url']}: HTTP {r.status_code}"
    return None


def _publish_one(name: str, client, pkg: dict, cfg: dict) -> dict:
    sc = scfg(cfg)
    copy = pkg["copy"]
    images = [{"url": i["url"], "alt": i["alt"]} for i in pkg["images"]]
    if name == "instagram":
        return client.post(instagram_caption(copy), images)
    if name == "threads":
        mode = ((sc.get("platforms") or {}).get("threads") or {}).get("media", "cover")
        media = {"carousel": images, "cover": images[:1], "link": []}.get(mode, images[:1])
        if pkg.get("kind") == "promo":  # promo posts choose their own images (often none)
            media = [{"url": i["url"], "alt": i["alt"]} for i in pkg.get("threads_images") or []]
        return client.post(copy["threads_text"].replace("{url}", pkg["url"]), media)
    if name == "facebook":
        fc = (sc.get("platforms") or {}).get("facebook") or {}
        link = pkg["url"]
        if fc.get("utm", True):
            link += "?utm_source=facebook&utm_medium=social&utm_campaign=blog"
        if fc.get("format", "link") == "photos":
            return client.post_photos(copy["facebook_message"] + "\n\n" + link, images)
        return client.post_link(copy["facebook_message"], link)
    raise ValueError(name)


def due_items(cfg: dict, now: dt.datetime, slug: Optional[str] = None,
              platform: Optional[str] = None) -> list[tuple[dict, str]]:
    sc = scfg(cfg)
    enabled = {n for n in PLATFORMS if ((sc.get("platforms") or {}).get(n) or {}).get("enabled")}
    live = {p.stem for p in POSTS_DIR.glob("*.json")}
    out = []
    for f in sorted(SOCIAL_DIR.glob("*.json")):
        pkg = read_json(f)
        if not pkg.get("copy") or (slug and pkg["slug"] != slug) or not pkg.get("approved"):
            continue
        if pkg.get("kind") != "promo" and pkg["slug"] not in live:  # article was unpublished
            continue
        for name, st in (pkg.get("platforms") or {}).items():
            if name not in enabled or (platform and name != platform) or st.get("status") != "queued":
                continue
            when = dt.datetime.fromisoformat(st["scheduled_at"])
            retry = st.get("retry_after")
            if when <= now and (not retry or dt.datetime.fromisoformat(retry) <= now):
                out.append((pkg, name))
    return out


def post_due(cfg: dict, now: Optional[dt.datetime] = None, clients: Optional[dict] = None,
             dry_run: bool = False, slug: Optional[str] = None, platform: Optional[str] = None,
             check_images: Callable[[list], Optional[str]] = images_live,
             log: Callable[[str], None] = print) -> dict:
    """Post everything due. Writes results into each package as it goes."""
    sc = scfg(cfg)
    now = now or now_utc()
    clients = make_clients(cfg) if clients is None else clients
    max_attempts = sc.get("max_attempts", 3)
    max_late = dt.timedelta(hours=sc.get("max_late_hours", 72))
    result = {"posted": [], "failed": [], "waiting": [], "skipped": []}
    image_ok: dict[str, Optional[str]] = {}
    for pkg_snapshot, name in due_items(cfg, now, slug, platform):
        pkg = load_package(pkg_snapshot["slug"])  # re-read: an earlier platform may have updated it
        st = pkg["platforms"][name]
        tag = f"{pkg['slug']} -> {name}"
        if now - dt.datetime.fromisoformat(st["scheduled_at"]) > max_late:
            st.update(status="skipped", error=f"more than {max_late} late; not posting stale content")
            save_package(pkg)
            result["skipped"].append(tag)
            log(f"SKIP {tag}: stale")
            continue
        client = clients.get(name)
        if client is None:
            result["waiting"].append(f"{tag} (no credentials)")
            log(f"WAIT {tag}: credentials not configured")
            continue
        if pkg["slug"] not in image_ok:
            image_ok[pkg["slug"]] = None if dry_run else check_images(pkg["images"])
        if image_ok[pkg["slug"]]:
            result["waiting"].append(f"{tag} (images not live yet)")
            log(f"WAIT {tag}: images not live yet ({image_ok[pkg['slug']]})")
            continue
        if dry_run:
            result["posted"].append(f"{tag} (dry run)")
            log(f"DRY RUN {tag}")
            continue
        from .meta_api import MetaError
        try:
            res = _publish_one(name, client, pkg, cfg)
        except MetaError as e:
            st["attempts"] = int(st.get("attempts", 0)) + 1
            st["error"] = str(e)[:500]
            if st["attempts"] >= max_attempts:
                st["status"] = "failed"
                result["failed"].append(f"{tag}: {e}")
                log(f"FAIL {tag}: {e}")
            else:
                st["retry_after"] = (now + dt.timedelta(hours=st["attempts"])).isoformat()
                result["waiting"].append(f"{tag} (retrying: {e})")
                log(f"RETRY LATER {tag}: {e}")
            save_package(pkg)
            continue
        st.update(status="posted", id=res["id"], permalink=res.get("permalink"),
                  posted_at=now_utc().replace(microsecond=0).isoformat(), error=None, retry_after=None)
        st["attempts"] = int(st.get("attempts", 0)) + 1
        save_package(pkg)
        result["posted"].append(f"{tag}: {res.get('permalink') or res['id']}")
        log(f"POSTED {tag}: {res.get('permalink') or res['id']}")
    return result


def unnotified_holds(mark: bool = True) -> list[str]:
    """Packages held by the copy gate that nobody has been told about yet."""
    out = []
    for f in sorted(SOCIAL_DIR.glob("*.json")):
        pkg = read_json(f)
        if pkg.get("needs_review") and not pkg.get("approved") and not pkg.get("review_notified"):
            out.append(f"{pkg['slug']}: " + "; ".join(pkg.get("qa_errors") or []))
            if mark:
                pkg["review_notified"] = True
                save_package(pkg)
    return out


# ------------------------------------------------------------ preview
def preview_html(pkg: dict, cfg: dict) -> Path:
    """A local page showing the slides and each platform's copy, for review."""
    e = html.escape
    copy = pkg["copy"]
    out = OUT_DIR / "social" / f"{pkg['slug']}-preview.html"
    rel = os.path.relpath(SITE_DIR, out.parent)
    slides = "".join(f'<img src="{e(rel + "/" + i["path"])}" alt="{e(i["alt"])}" title="{e(i["alt"])}">'
                     for i in pkg["images"])
    threads = copy["threads_text"].replace("{url}", pkg["url"])
    rows = "".join(f"<tr><td>{n}</td><td>{e(s.get('status', ''))}</td><td>{e(s.get('scheduled_at', ''))}</td>"
                   f"<td>{e(s.get('permalink') or s.get('error') or '')}</td></tr>"
                   for n, s in pkg.get("platforms", {}).items())
    issues = "".join(f"<li>{e(x)}</li>" for x in pkg.get("qa_errors", []))
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Social preview: {e(pkg['title'])}</title>
<style>
body{{margin:0;background:#0C0907;color:#F5EDE3;font:16px/1.5 system-ui,-apple-system,sans-serif;padding:24px 16px}}
main{{max-width:1100px;margin:0 auto}} h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;color:#F2C48A;text-transform:uppercase;letter-spacing:.08em;margin:32px 0 10px}}
.strip{{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}} .strip img{{width:260px;border-radius:10px;flex:none}}
pre{{white-space:pre-wrap;background:#160F0B;border:1px solid #2a211a;border-radius:10px;padding:14px;font:15px/1.5 system-ui,sans-serif;margin:0}}
.meta{{color:#B9AC9E;font-size:13px;margin-top:6px}} table{{border-collapse:collapse;width:100%;font-size:14px}} td{{border-top:1px solid #2a211a;padding:6px 8px;vertical-align:top}}
.warn{{background:#3a170a;border:1px solid #E8692E;border-radius:10px;padding:10px 14px}} a{{color:#F2A341}}
</style></head><body><main>
<h1>{e(pkg['title'])}</h1><div class="meta"><a href="{e(pkg['url'])}">{e(pkg['url'])}</a> · approved: {pkg.get('approved')}</div>
{f'<h2>Needs review</h2><ul class="warn">{issues}</ul>' if issues else ''}
<h2>Carousel (Instagram{', Threads' if ((scfg(cfg).get('platforms') or {}).get('threads') or {}).get('media') == 'carousel' else ''})</h2><div class="strip">{slides}</div>
<h2>Instagram caption</h2><pre>{e(instagram_caption(copy))}</pre><div class="meta">{len(instagram_caption(copy))} / {IG_CAPTION_MAX} characters</div>
<h2>Threads</h2><pre>{e(threads)}</pre><div class="meta">{len(threads.encode('utf-8'))} / {THREADS_MAX_BYTES} bytes</div>
<h2>Facebook</h2><pre>{e(copy['facebook_message'])}</pre><div class="meta">Posted with the article's link card.</div>
<h2>Schedule (UTC)</h2><table>{rows}</table>
</main></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out


def status_lines() -> list[str]:
    lines = []
    for f in sorted(SOCIAL_DIR.glob("*.json"), reverse=True):
        pkg = read_json(f)
        flag = "" if pkg.get("approved") else "  [held]"
        if pkg.get("prepare_error"):
            flag = f"  [prepare failed x{pkg.get('prepare_attempts')}: {pkg['prepare_error'][:80]}]"
        lines.append(f"{pkg['slug']}{flag}")
        for n, s in (pkg.get("platforms") or {}).items():
            extra = s.get("permalink") or s.get("error") or ""
            lines.append(f"    {n:<10} {s.get('status', ''):<8} {s.get('scheduled_at', '')}  {extra}")
    return lines or ["No social packages yet."]
