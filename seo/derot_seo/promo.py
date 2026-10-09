"""Promotional posts for Threads and Instagram, from seo/content/promo/plan.yaml.

`build` renders every post's images to img/social/promo-<id>/ and writes one
package per post into seo/content/social/promo-<id>.json, the same queue the
article posts use, so the hourly SEO social workflow posts them on schedule.
Launch posts stay out of the queue until `launch --date` (which needs the App
Store URL in config.yaml). Posted packages are never rewritten.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Optional

import yaml

from . import qa, social
from . import social_images as simg
from .util import SEO_DIR, SITE_DIR, now_utc, read_json

PLAN = SEO_DIR / "content" / "promo" / "plan.yaml"
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Consumer copy rules (marketing/DeRot-Copy-Rules.md): lead with the felt
# experience, keep mechanism jargon out of promo copy.
CONSUMER_BANNED = [
    (r"\bvagus\b", "copy rules: no 'vagus nerve' in consumer copy"),
    (r"\bheart rate\b", "copy rules: no 'heart rate' in consumer copy"),
    (r"\bnervous[- ]system\b", "copy rules: no 'nervous system' in consumer copy"),
    (r"\bphysiolog(y|ical)\b(?! sigh)", "copy rules: no 'physiology' in consumer copy"),
    (r"\bbreathing gate\b", "copy rules: no 'breathing gate'"),
    (r"\bstrict\b", "copy rules: avoid 'strict'"),
    (r"\bdetox\b(?!\w)", "copy rules: no detox framing"),
]
PRELAUNCH_BANNED = r"\b(available now|out now|download (it|now|today|derot)|on the app store|now on iphone|is live|is here)\b"
ALLOWED_HOSTS = ("derot.org", "apps.apple.com")


def load_plan(path: Path = PLAN) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _all_posts(plan: dict) -> list[dict]:
    return [{**p, "phase": "prelaunch"} for p in plan.get("prelaunch") or []] + \
           [{**p, "phase": "launch"} for p in plan.get("launch") or []]


def check_post(post: dict) -> list[str]:
    """Brand and platform checks for one hand-written promo post."""
    errors = []
    fields = [("text", post.get("text", "")) if post["platform"] == "threads" else ("caption", post.get("caption", ""))]
    for i, s in enumerate(post.get("slides") or []):
        fields += [(f"slides[{i}].headline", s.get("headline", "")), (f"slides[{i}].body", s.get("body", ""))]
    for name, text in fields:
        if re.search(r"[—–]", text):
            errors.append(f"{name}: em or en dash")
        for pattern, label in qa.BANNED + CONSUMER_BANNED:
            m = re.search(pattern, text, re.I)
            if m:
                errors.append(f"{name}: {label} ('{m.group(0)}')")
        if qa.LOCK_RE.search(text):
            errors.append(f"{name}: DeRot described with 'lock'")
        if post["phase"] == "prelaunch":
            m = re.search(PRELAUNCH_BANNED, text, re.I)
            if m:
                errors.append(f"{name}: says DeRot is available, but it is pre-launch ('{m.group(0)}')")
        for u in social.URLISH_RE.findall(text):
            if not any(h in u.lower() for h in ALLOWED_HOSTS):
                errors.append(f"{name}: link or domain outside derot.org ('{u}')")
    if post["platform"] == "threads":
        sample = post.get("text", "").replace("{url}", post.get("url") or "https://apps.apple.com/app/derot/id0000000000")
        if len(sample.encode("utf-8")) > social.THREADS_MAX_BYTES:
            errors.append(f"text is {len(sample.encode('utf-8'))} bytes (Threads max {social.THREADS_MAX_BYTES})")
    else:
        cap = post.get("caption", "")
        if not re.search(r"link in bio", cap, re.I):
            errors.append("caption: Instagram links are not clickable, so say 'link in bio'")
        if "{url}" in cap or "http" in cap:
            errors.append("caption: no URLs on Instagram")
        if len(cap) + sum(len(h) + 2 for h in post.get("hashtags") or []) > social.IG_CAPTION_MAX:
            errors.append("caption too long")
        if not post.get("slides"):
            errors.append("instagram post needs slides")
        n = len(post.get("slides") or []) + (1 if post.get("closing") else 0)
        if n > 10:
            errors.append(f"{n} images (Instagram carousels max out at 10)")
    for s in post.get("slides") or []:
        if s.get("template") == "phone" and not (simg.SCREENS / f"{s['screen']}.jpg").exists():
            errors.append(f"unknown screen '{s['screen']}'")
    return errors


def check_plan(plan: dict) -> list[str]:
    posts = _all_posts(plan)
    ids = [p["id"] for p in posts]
    errors = [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    by_id = {p["id"]: p for p in posts}
    for p in posts:
        errors += [f"{p['id']}: {e}" for e in check_post(p)]
        ref = p.get("images")
        if ref:
            src = by_id.get(ref["post"])
            if not src or not src.get("slides"):
                errors.append(f"{p['id']}: images refer to unknown post {ref['post']}")
            else:
                n = len(src["slides"]) + (1 if src.get("closing") else 0)
                if any(not 1 <= i <= n for i in ref["slides"]):
                    errors.append(f"{p['id']}: slide numbers out of range for {ref['post']}")
                if src["phase"] != p["phase"]:
                    errors.append(f"{p['id']}: borrows images from a {src['phase']} post")
        if p["phase"] == "prelaunch" and p.get("day") not in DAYS:
            errors.append(f"{p['id']}: day must be one of {', '.join(DAYS)}")
    return errors


def _slot(plan: dict, post: dict, launch_date: Optional[dt.date]) -> dt.datetime:
    hh, mm = (int(x) for x in str(plan["times"][post["platform"]]).split(":"))
    if post["phase"] == "launch":
        day = launch_date + dt.timedelta(days=int(post["day"]))
    else:
        start = plan["start"] if isinstance(plan["start"], dt.date) else dt.date.fromisoformat(str(plan["start"]))
        day = start + dt.timedelta(days=7 * (int(post["week"]) - 1) + DAYS[post["day"]])
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=dt.timezone.utc)


def _images(post: dict, base_url: str) -> list[dict]:
    out_dir = social.IMG_DIR / f"promo-{post['id']}"
    paths = simg.render_promo(post["slides"], post.get("kicker", ""), out_dir, post.get("closing"))
    alts = []
    for s in post["slides"]:
        alts.append(" ".join(x for x in (s.get("headline", ""), s.get("body", "")) if x).strip())
    if post.get("closing"):
        head, lines = simg.CLOSINGS[post["closing"]]
        alts.append(" ".join(head) + " " + " ".join(lines) + " Regulate, don't restrict.")
    return [{"path": p.relative_to(SITE_DIR).as_posix(), "url": f"{base_url}/{p.relative_to(SITE_DIR).as_posix()}",
             "alt": a} for p, a in zip(paths, alts)]


def build(cfg: dict, plan: Optional[dict] = None, launch_date: Optional[dt.date] = None,
          now: Optional[dt.datetime] = None, phases: tuple = ("prelaunch",)) -> dict:
    """Render and queue promo posts. Returns counts. Raises ValueError on copy-rule errors."""
    plan = plan or load_plan()
    errors = check_plan(plan)
    if errors:
        raise ValueError("Promo plan has problems:\n" + "\n".join(f"- {e}" for e in errors))
    now = now or now_utc()
    sc = social.scfg(cfg)
    lead = dt.timedelta(minutes=sc.get("min_lead_minutes", 60))
    base = cfg["site"]["base_url"]
    app_url = cfg["site"].get("app_store_url") or ""
    if "launch" in phases and not (launch_date and app_url):
        raise ValueError("Launch posts need a launch date and site.app_store_url in config.yaml.")
    posts = [p for p in _all_posts(plan) if p["phase"] in phases]
    rendered: dict[str, list[dict]] = {}
    counts = {"queued": 0, "kept": 0, "past": 0, "launch_previewed": 0}
    if "launch" not in phases:  # render launch images now so they can be reviewed in the calendar
        for p in _all_posts(plan):
            if p["phase"] == "launch" and p.get("slides"):
                _images(p, base)
                counts["launch_previewed"] += 1

    for p in sorted(posts, key=lambda x: 0 if x.get("slides") else 1):  # sources before borrowers
        slug = f"promo-{p['id']}"
        existing = social.load_package(slug)
        if existing and any(s.get("status") == "posted" for s in existing.get("platforms", {}).values()):
            counts["kept"] += 1
            continue
        if p.get("slides"):
            rendered[p["id"]] = _images(p, base)
            images = rendered[p["id"]]
        elif p.get("images"):
            src = rendered.get(p["images"]["post"])
            if src is None:  # source already posted: reuse its published images
                src = (social.load_package(f"promo-{p['images']['post']}") or {}).get("images", [])
            images = [src[i - 1] for i in p["images"]["slides"] if i - 1 < len(src)]
        else:
            images = []
        url = p.get("url") or (app_url if p["phase"] == "launch" else plan["cta_url"])
        slot = _slot(plan, p, launch_date)
        status = "queued"
        if slot < now:  # never burst a backlog: a missed slot is skipped, not bunched up
            status = "skipped"
        elif slot < now + lead:
            slot = (now + lead).replace(second=0, microsecond=0)
        if p["platform"] == "threads":
            copy = {"threads_text": p["text"]}
        else:
            copy = {"instagram_caption": p["caption"], "instagram_hashtags": p.get("hashtags") or []}
        pkg = {
            "kind": "promo", "slug": slug, "phase": p["phase"], "pillar": p.get("pillar"),
            "title": (p.get("text") or p.get("caption") or "").split("\n")[0][:90],
            "url": url, "approved": True, "copy": copy, "images": images,
            "threads_images": images if p["platform"] == "threads" else None,
            "platforms": {p["platform"]: {"status": status, "scheduled_at": slot.isoformat(), "attempts": 0,
                                          **({"error": "date passed before the plan was built"} if status == "skipped" else {})}},
            "built_at": now.replace(microsecond=0).isoformat(),
        }
        social.save_package(pkg)
        counts["queued" if status == "queued" else "past"] += 1
    return counts


def _disk_images(post: dict, by_id: dict) -> list[dict]:
    """Rendered images for a post that has no package yet (launch posts)."""
    src = post if post.get("slides") else by_id.get((post.get("images") or {}).get("post"), {})
    if not src:
        return []
    files = sorted((social.IMG_DIR / f"promo-{src['id']}").glob("slide-*.jpg"))
    if src is not post:
        files = [files[i - 1] for i in post["images"]["slides"] if i - 1 < len(files)]
    return [{"path": f.relative_to(SITE_DIR).as_posix(), "alt": ""} for f in files]


def calendar_html(cfg: dict, out: Path, plan: Optional[dict] = None) -> Path:
    """One page with every promo post (pre-launch and launch) for review."""
    import html as h
    import os
    plan = plan or load_plan()
    e = h.escape
    rel = os.path.relpath(SITE_DIR, out.parent)
    rows = []
    cur_week = None
    by_id = {p["id"]: p for p in _all_posts(plan)}
    for p in _all_posts(plan):
        pkg = social.load_package(f"promo-{p['id']}") or {}
        st = (pkg.get("platforms") or {}).get(p["platform"], {})
        if p["phase"] == "launch":
            when, week = f"Launch day {p['day']}", "Launch week (held until `social launch`)"
        else:
            slot = _slot(plan, p, None)
            when = slot.strftime("%a %b %-d, %H:%M UTC")
            week = f"Week {p['week']}: {(slot - dt.timedelta(days=DAYS[p['day']])).strftime('%b %-d')}"
        if week != cur_week:
            rows.append(f"<h2>{e(week)}</h2>")
            cur_week = week
        imgs = pkg.get("images") or _disk_images(p, by_id)
        strip = "".join(f'<img loading="lazy" src="{e(rel + "/" + i["path"])}" alt="{e(i["alt"])}">' for i in imgs)
        text = p.get("text", "").replace("{url}", pkg.get("url") or p.get("url") or "(App Store link)") \
            if p["platform"] == "threads" else p.get("caption", "") + "\n\n" + " ".join("#" + t for t in p.get("hashtags") or [])
        badge = st.get("status", "not queued")
        if st.get("permalink"):
            badge = f'<a href="{e(st["permalink"])}">posted</a>'
        rows.append(f"""<article class="post {p['platform']}">
<div class="meta"><span class="plat">{p['platform'].title()}</span><span>{e(when)}</span><span class="pill">{e(p.get('pillar') or '')}</span><span class="st">{badge}</span><code>{e(p['id'])}</code></div>
{f'<div class="strip">{strip}</div>' if strip else ''}<pre>{e(text.strip())}</pre></article>""")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeRot promo calendar</title><style>
:root{{color-scheme:dark}} body{{margin:0;background:#0C0907;color:#F5EDE3;font:15px/1.5 system-ui,-apple-system,sans-serif;padding:24px 16px}}
main{{max-width:1000px;margin:0 auto}} h1{{font-size:24px;margin:0 0 4px}} .lede{{color:#B9AC9E;margin:0 0 8px}}
h2{{font-size:13px;color:#F2C48A;text-transform:uppercase;letter-spacing:.1em;margin:36px 0 12px;border-top:1px solid #2a211a;padding-top:20px}}
.post{{background:#160F0B;border:1px solid #2a211a;border-radius:12px;padding:14px;margin:0 0 14px}}
.meta{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13px;color:#B9AC9E;margin-bottom:10px}}
.plat{{font-weight:700;color:#F5EDE3}} .instagram .plat{{color:#F2A341}} .pill{{border:1px solid #3a2c20;border-radius:99px;padding:0 8px}}
.st{{color:#E9D9C6}} code{{margin-left:auto;font-size:12px;color:#7d7064}} a{{color:#F2A341}}
.strip{{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;margin-bottom:10px}} .strip img{{width:200px;border-radius:8px;flex:none}}
pre{{white-space:pre-wrap;margin:0;font:inherit;color:#E9D9C6}}
</style></head><body><main><h1>DeRot promo calendar</h1>
<p class="lede">Threads Mon to Fri, Instagram Tue and Sat, plus the automatic blog shares. Source: seo/content/promo/plan.yaml.</p>
{''.join(rows)}</main></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
