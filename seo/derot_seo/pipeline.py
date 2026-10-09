"""Orchestration: one function per CLI command."""
from __future__ import annotations

import datetime as dt
import json
import os
import traceback
from pathlib import Path
from typing import Optional

from . import generate as gen
from . import keywords as kw
from . import qa, site
from .llm import make_llm
from .util import (DATA_DIR, DRAFTS_DIR, OUT_DIR, POSTS_DIR, append_jsonl, load_config, load_posts,
                   now_utc, parse_ts, read_json, today, write_json)

EXIT_PUBLISHED, EXIT_SKIPPED, EXIT_PARKED = 0, 10, 20
RUN_LOG = DATA_DIR / "runs.jsonl"


def _gh_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def _summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)


def cadence_block(cfg: dict, posts: list[dict]) -> Optional[str]:
    pub = cfg["publishing"]
    week_ago = (today() - dt.timedelta(days=7)).isoformat()
    recent = [p for p in posts if p["date_published"] > week_ago]
    if len(recent) >= pub["max_posts_per_week"]:
        return f"weekly cap reached ({len(recent)} posts in the last 7 days)"
    stamps = [p.get("published_at") for p in posts if p.get("published_at")]
    if stamps:
        last = max(parse_ts(x) for x in stamps)
        hours = (now_utc() - last).total_seconds() / 3600
        if hours < pub["min_hours_between_posts"]:
            return f"last post was {hours:.1f}h ago (min {pub['min_hours_between_posts']}h)"
    return None


def _social_file(post: dict, cfg: dict) -> Path:
    url = f"{cfg['site']['base_url']}/blog/{post['slug']}/"
    s = post.get("social", {})
    md = f"""# Social kit: {post['title']}

URL: {url}
Primary keyword: {post['primary_keyword']}

## X
{s.get('x_post', '').replace('{url}', url)}

## Threads
{s.get('threads_post', '').replace('{url}', url)}

## LinkedIn
{s.get('linkedin_post', '').replace('{url}', url)}

## Pinterest
Title: {s.get('pinterest_title', '')}
Description: {s.get('pinterest_description', '')}
Image: {cfg['site']['base_url']}/img/blog/{post['slug']}.png
Link: {url}

## Newsletter blurb
{s.get('newsletter_blurb', '')}

## Short video script (TikTok / Reels / Shorts, faceless)
{s.get('short_video_script', '')}
"""
    path = OUT_DIR / "social" / f"{post['date_published']}-{post['slug']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return path


def create_article(llm, cfg: dict, data: dict, posts: list[dict], forced_keyword: Optional[str] = None,
                   check_links: bool = True, existing: Optional[dict] = None) -> tuple[Optional[dict], dict]:
    """Plan (or reuse a plan for refresh), research, write, and gate one article.
    Returns (article_or_None, log)."""
    system = gen.brand_system(cfg)
    log: dict = {"stages": []}
    if existing:
        the_plan = {k: existing[k] for k in ("primary_keyword", "secondary_keywords", "cluster", "content_type")}
        the_plan.update(working_title=existing["title"], angle=existing.get("angle", ""),
                        search_intent=existing.get("search_intent", ""), reader_problem=existing.get("reader_problem", ""),
                        cannibalization_check="refresh of existing article", rationale="scheduled refresh")
        others = [p for p in posts if p["slug"] != existing["slug"]]
    else:
        the_plan = gen.plan(llm, cfg, data, posts, system, forced_keyword)
        others = posts
    log["plan"] = the_plan
    log["stages"].append("plan")

    brief = gen.research(llm, cfg, the_plan, system)
    log["stages"].append("research")
    log["dropped_sources"] = brief.get("dropped_sources", [])
    if len(brief["sources"]) < cfg["quality"]["min_sources"]:
        # One more research attempt with an explicit nudge before giving up.
        the_plan_retry = {**the_plan, "angle": the_plan["angle"] + " (prioritize finding peer-reviewed and authoritative sources)"}
        brief = gen.research(llm, cfg, the_plan_retry, system)
        log["stages"].append("research-retry")

    if existing:
        # The live article's citations were verified when it was published; keep them
        # citable (QA re-checks that they still resolve) alongside the new research.
        have = {qa.normalize_url(s["url"]) for s in brief["sources"]}
        for i, s in enumerate(existing.get("sources", [])):
            if qa.normalize_url(s["url"]) not in have:
                brief["sources"].append({"id": f"E{i + 1}", "url": s["url"], "title": s["title"],
                                         "publisher": s.get("publisher", ""), "year": s.get("year", ""),
                                         "key_findings": "cited in the current version of this article"})

    inv = site.inventory(others, kw.cluster_map(data), extra_cluster=the_plan["cluster"])
    if existing:
        article = gen.revise(llm, cfg, the_plan, brief, inv, system, existing,
                             "Refresh this article: update it with the newest research in the brief, correct anything "
                             "outdated, improve sections that underperform on helpfulness or structure, and keep the URL.")
    else:
        article = gen.write(llm, cfg, the_plan, brief, inv, system)
        # slug collision guard
        taken = {p["slug"] for p in posts} | {f.stem for f in DRAFTS_DIR.glob("*.json")}
        base_slug, n = article["slug"], 2
        while article["slug"] in taken:
            article["slug"] = f"{base_slug[:64]}-{n}"
            n += 1
    log["stages"].append("write")

    max_rev = cfg["quality"]["max_revisions"]
    prior: list = []
    for attempt in range(max_rev + 1):
        result = qa.check(article, the_plan, brief, inv, cfg, check_links=check_links)
        review = gen.review(llm, cfg, the_plan, brief, article, result, system, prior=prior)
        rec = {"attempt": attempt, "qa_errors": result.errors, "qa_warnings": result.warnings,
               "fixes": result.fixes, "overall": review["overall"], "scores": review["scores"],
               "blocking": [gen.issue_text(b) for b in review["blocking_issues"]], "verdict": review["verdict"]}
        log.setdefault("reviews", []).append(rec)
        if result.ok and gen.review_passes(review, cfg):
            article.update(review_score=review["overall"], review_scores=review["scores"])
            log["stages"].append(f"approved@{attempt}")
            break
        # Apply the editor's exact wording fixes directly instead of paying for a rewrite.
        applied, remaining = gen.apply_edits(article, review["blocking_issues"])
        rec["edits_applied"] = len(applied)
        if applied and not remaining and review["overall"] >= cfg["quality"].get("min_score_with_edits", cfg["quality"]["min_review_score"] - 1):
            recheck = qa.check(article, the_plan, brief, inv, cfg, check_links=check_links)
            if recheck.ok:
                article.update(review_score=review["overall"], review_scores=review["scores"],
                               editor_edits=len(applied))
                log["stages"].append(f"approved-with-edits@{attempt}")
                break
            result = recheck
        if attempt == max_rev:
            log["stages"].append("parked")
            article.update(review_score=review["overall"], review=review, qa_errors=result.errors)
            return _finalize(article, the_plan, existing), {**log, "brief": brief, "parked": True}
        prior = review["blocking_issues"]
        issues = "\n".join([*(f"- [automated] {e}" for e in result.errors),
                            *(f"- [automated warning] {w}" for w in result.warnings),
                            *(f"- [editor, blocking] {gen.issue_text(b)}" for b in remaining),
                            *(f"- [editor] {i}" for i in review["improvements"][:8])])
        article = gen.revise(llm, cfg, the_plan, brief, inv, system, article, issues)
        log["stages"].append(f"revise{attempt + 1}")
    return _finalize(article, the_plan, existing), {**log, "brief": brief, "parked": False}


def _finalize(article: dict, the_plan: dict, existing: Optional[dict]) -> dict:
    stamp = now_utc().replace(microsecond=0).isoformat()
    a = {
        "slug": article["slug"], "title": article["title"], "meta_title": article["meta_title"],
        "meta_description": article["meta_description"], "dek": article["dek"],
        "primary_keyword": the_plan["primary_keyword"], "secondary_keywords": the_plan["secondary_keywords"],
        "cluster": the_plan["cluster"], "content_type": the_plan["content_type"],
        "angle": the_plan.get("angle", ""), "search_intent": the_plan.get("search_intent", ""),
        "reader_problem": the_plan.get("reader_problem", ""),
        "key_takeaways": article["key_takeaways"], "body_markdown": article["body_markdown"],
        "faqs": article["faqs"], "howto_steps": article.get("howto_steps", []),
        "sources": article.get("sources", []), "social": article.get("social", {}),
        "word_count": article.get("word_count"), "review_score": article.get("review_score"),
        "review_scores": article.get("review_scores"), "editor_edits": article.get("editor_edits", 0),
        "date_published": existing["date_published"] if existing else today().isoformat(),
        "date_modified": today().isoformat(),
        "published_at": existing.get("published_at", stamp) if existing else stamp,
    }
    if existing:
        a["refresh_history"] = existing.get("refresh_history", []) + [today().isoformat()]
    for k in ("review", "qa_errors"):
        if k in article:
            a[k] = article[k]
    return a


# ---------------------------------------------------------------- commands
def _refresh_gsc(cfg: dict) -> None:
    if not os.environ.get("GSC_SERVICE_ACCOUNT_JSON"):
        return
    try:
        from . import gsc
        gsc.pull(cfg)
    except Exception as e:  # Search Console is a bonus signal, never a blocker
        print(f"Search Console pull failed (continuing without it): {type(e).__name__}: {e}")


def _prepare_social(llm, cfg: dict, article: dict) -> str:
    """Queue Instagram/Threads/Facebook posts. Never blocks the article: if this
    fails, the hourly social workflow retries it (`social prepare --missing`)."""
    from . import social as social_posts
    if not social_posts.scfg(cfg).get("enabled"):
        return "Social posting: off"
    try:
        pkg = social_posts.prepare(article, cfg, llm)
        social_posts.preview_html(pkg, cfg)
    except Exception as e:
        traceback.print_exc()
        return f"Social posts: preparation failed ({type(e).__name__}), the social workflow will retry"
    when = ", ".join(f"{n} {s['scheduled_at'][:16].replace('T', ' ')} UTC" for n, s in pkg["platforms"].items())
    if not pkg.get("approved"):
        return f"Social posts: HELD for review ({'; '.join(pkg.get('qa_errors') or ['approval required'])})"
    return f"Social posts queued: {when}"


def cmd_publish(mock: bool = False, keyword: Optional[str] = None, force: bool = False,
                check_links: bool = True) -> int:
    cfg = load_config()
    data = kw.load_universe()
    posts = load_posts()
    if not force:
        block = cadence_block(cfg, posts)
        if block:
            _summary(f"### Skipped: {block}")
            _gh_output(result="skipped")
            return EXIT_SKIPPED
    _refresh_gsc(cfg)
    llm = make_llm(cfg, mock=mock)
    started = now_utc()
    try:
        article, log = create_article(llm, cfg, data, posts, forced_keyword=keyword, check_links=check_links)
    except Exception as e:
        append_jsonl(RUN_LOG, {"at": started.isoformat(), "command": "publish", "result": "error",
                               "error": f"{type(e).__name__}: {e}", "cost_usd": round(llm.usage.cost_usd, 3)})
        traceback.print_exc()
        _gh_output(result="error")
        raise
    record = {"at": started.isoformat(), "command": "publish", "stages": log["stages"],
              "keyword": log["plan"]["primary_keyword"], "slug": article["slug"],
              "reviews": log.get("reviews"), "cost_usd": round(llm.usage.cost_usd, 3),
              "searches": llm.usage.searches}
    if log["parked"]:
        write_json(DRAFTS_DIR / f"{article['slug']}.json", {**article, "brief": log["brief"]})
        kw.mark(data, log["plan"]["primary_keyword"], "parked",
                retry_after=(today() + dt.timedelta(days=21)).isoformat(), draft=article["slug"])
        kw.save_universe(data)
        append_jsonl(RUN_LOG, {**record, "result": "parked"})
        last = log["reviews"][-1]
        _summary(f"### Draft parked (did not pass quality gate)\n\n**{article['title']}**\n\n"
                 f"Review score {last['overall']}/10.\n\nBlocking issues:\n"
                 + "\n".join(f"- {b}" for b in last["blocking"] + last["qa_errors"])
                 + f"\n\nDraft saved to `seo/content/drafts/{article['slug']}.json`. To publish it, fix the JSON and "
                   f"run the SEO build workflow with promote_slug `{article['slug']}`; otherwise the keyword retries in 3 weeks.")
        _gh_output(result="parked", slug=article["slug"], title=article["title"].replace("\n", " "))
        return EXIT_PARKED

    write_json(POSTS_DIR / f"{article['slug']}.json", article)
    kw.mark(data, article["primary_keyword"], "published", post=article["slug"], published=today().isoformat())
    for s in article["secondary_keywords"]:
        if s.lower() != article["primary_keyword"].lower():
            kw.mark(data, s, "covered", covered_by=article["slug"])
    kw.save_universe(data)
    write_json(DATA_DIR / "briefs" / f"{article['slug']}.json", log["brief"])
    cfg_now = load_config()
    site.build(cfg_now, data)
    social = _social_file(article, cfg)
    social_line = _prepare_social(llm, cfg_now, article)
    record["cost_usd"] = round(llm.usage.cost_usd, 3)
    append_jsonl(RUN_LOG, {**record, "result": "published"})
    url = f"{cfg['site']['base_url']}/blog/{article['slug']}/"
    _summary(f"### Published: [{article['title']}]({url})\n\n"
             f"- Primary keyword: `{article['primary_keyword']}` ({article['content_type']}, cluster `{article['cluster']}`)\n"
             f"- {article['word_count']} words, {len(article['sources'])} sources, review {article['review_score']}/10\n"
             f"- Path: {' -> '.join(log['stages'])}\n- Estimated API cost: ${llm.usage.cost_usd:.2f}\n"
             f"- Social kit: `seo/out/social/{social.name}`\n- {social_line}")
    _gh_output(result="published", slug=article["slug"], url=url, title=article["title"].replace("\n", " "))
    return EXIT_PUBLISHED


def cmd_build(regenerate_images: bool = False) -> int:
    cfg = load_config()
    files = site.build(cfg, kw.load_universe(), regenerate_images=regenerate_images)
    print(f"Built {len(files)} files.")
    return 0


def cmd_plan(n: int = 10) -> int:
    cfg = load_config()
    data = kw.load_universe()
    posts = load_posts()
    for k in kw.shortlist(data, posts, cfg, n):
        print(f"{k['score']:6.1f}  {k['keyword']:<55} {k.get('cluster', ''):<32} {k.get('content_type', '')} {' '.join(k['why'])}")
    block = cadence_block(cfg, posts)
    print(f"\nCadence: {'BLOCKED: ' + block if block else 'clear to publish'}")
    return 0


def cmd_refresh(mock: bool = False, slug: Optional[str] = None, check_links: bool = True) -> int:
    cfg = load_config()
    if not cfg["refresh"]["enabled"] and not slug:
        print("Refresh disabled.")
        return EXIT_SKIPPED
    data = kw.load_universe()
    posts = load_posts()
    _refresh_gsc(cfg)
    if slug:
        target = next((p for p in posts if p["slug"] == slug), None)
    else:
        target = pick_refresh(cfg, posts)
    if not target:
        _summary("### Refresh: nothing due")
        return EXIT_SKIPPED
    llm = make_llm(cfg, mock=mock)
    article, log = create_article(llm, cfg, data, posts, check_links=check_links, existing=target)
    record = {"at": now_utc().isoformat(), "command": "refresh", "slug": target["slug"],
              "stages": log["stages"], "cost_usd": round(llm.usage.cost_usd, 3)}
    if log["parked"]:
        append_jsonl(RUN_LOG, {**record, "result": "parked"})
        _summary(f"### Refresh of `{target['slug']}` did not pass review; the live article is unchanged.")
        return EXIT_PARKED
    write_json(POSTS_DIR / f"{target['slug']}.json", article)
    site.build(cfg, data)
    append_jsonl(RUN_LOG, {**record, "result": "refreshed"})
    _summary(f"### Refreshed: {article['title']} (review {article['review_score']}/10)")
    _gh_output(result="refreshed", slug=target["slug"],
               url=f"{cfg['site']['base_url']}/blog/{target['slug']}/")
    return 0


def pick_refresh(cfg: dict, posts: list[dict]) -> Optional[dict]:
    cutoff = (today() - dt.timedelta(days=cfg["refresh"]["min_age_days"])).isoformat()
    gsc = (read_json(DATA_DIR / "gsc_snapshot.json", {}) or {}).get("pages", {})
    base = cfg["site"]["base_url"]
    due = [p for p in posts if (p.get("date_modified") or p["date_published"]) < cutoff]
    if not due:
        return None

    def priority(p):
        g = gsc.get(f"{base}/blog/{p['slug']}/", {})
        # Striking distance (positions 5-20 with impressions) first, then oldest.
        striking = 1 if 5 <= g.get("position", 0) <= 20 and g.get("impressions", 0) >= 50 else 0
        return (-striking, -g.get("impressions", 0), p.get("date_modified") or p["date_published"])
    return sorted(due, key=priority)[0]


def cmd_weekly(mock: bool = False) -> int:
    """Maintenance: Search Console pull, keyword expansion, report."""
    cfg = load_config()
    data = kw.load_universe()
    lines = [f"## DeRot SEO weekly report, {today().isoformat()}", ""]
    try:
        from . import gsc
        snap = gsc.pull(cfg)
    except Exception as e:  # never let GSC break maintenance
        snap = None
        lines.append(f"- Search Console pull failed: `{type(e).__name__}: {e}`")
    if snap:
        striking = [q for q, r in snap["queries"].items() if 8 <= r["position"] <= 25 and r["impressions"] >= 10]
        # Counts only: this repo, its issues, and its Action logs are public, so detailed
        # Search Console data stays in the uncommitted snapshot used by the planner.
        lines += [f"- Search Console: connected ({snap['range'][0]} to {snap['range'][1]}), "
                  f"{len(snap['pages'])} pages with impressions, {len(striking)} striking-distance queries "
                  "boosted in planning. Full numbers: search.google.com/search-console", ""]
    elif not os.environ.get("GSC_SERVICE_ACCOUNT_JSON"):
        lines.append("- Search Console not connected (add the `GSC_SERVICE_ACCOUNT_JSON` secret to enable).")

    added = []
    if cfg["keywords"]["expansion"]["enabled"]:
        try:
            cands = (['breathing for focus at work', 'calm breathing before bed', 'why do i feel wired at night']
                     if mock else kw.gather_candidates(data, cfg))
            if cands:
                llm = make_llm(cfg, mock=mock)
                added = kw.triage(llm, data, cands, cfg, gen.brand_system(cfg))
                kw.save_universe(data)
            lines.append(f"### Keyword expansion\n- {len(cands)} candidates found, {len(added)} added to the plan")
            lines += [f"  - `{a['keyword']}` ({a['cluster']}, fit {a['business_fit']}, diff {a['difficulty_tier']})" for a in added[:15]]
        except Exception as e:
            lines.append(f"- Keyword expansion failed: `{type(e).__name__}: {e}`")

    posts = load_posts()
    week_ago = (today() - dt.timedelta(days=7)).isoformat()
    lines += ["", "### Published in the last 7 days"]
    recent = [p for p in posts if p["date_published"] > week_ago]
    lines += [f"- [{p['title']}]({cfg['site']['base_url']}/blog/{p['slug']}/) (`{p['primary_keyword']}`, review {p.get('review_score')}/10)"
              for p in recent] or ["- none"]
    drafts = sorted(DRAFTS_DIR.glob("*.json"))
    if drafts:
        lines += ["", "### Parked drafts needing attention"] + [f"- `seo/content/drafts/{d.name}`" for d in drafts]
    lines += ["", "### Up next (planner shortlist)"]
    lines += [f"- `{k['keyword']}` (score {k['score']}, {k.get('content_type')})" for k in kw.shortlist(data, posts, cfg, 6)]
    counts: dict = {}
    for k in data["keywords"]:
        counts[k.get("status", "queued")] = counts.get(k.get("status", "queued"), 0) + 1
    lines += ["", f"### Keyword plan status\n- " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))]
    runs = [json.loads(l) for l in (RUN_LOG.read_text().splitlines() if RUN_LOG.exists() else []) if l.strip()]
    month = today().isoformat()[:7]
    spend = sum(r.get("cost_usd", 0) for r in runs if r.get("at", "").startswith(month))
    lines += ["", f"### API spend this month (estimated): ${spend:.2f}"]
    report = "\n".join(lines)
    out = OUT_DIR / "reports" / f"{today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    _summary(report)
    return 0


def cmd_validate() -> int:
    """Static sanity checks for CI: config, keyword file, post files."""
    cfg = load_config()
    data = kw.load_universe()
    problems = []
    ids = {c["id"] for c in data["clusters"]}
    seen = set()
    for k in data["keywords"]:
        if k["keyword"] in seen:
            problems.append(f"duplicate keyword {k['keyword']}")
        seen.add(k["keyword"])
        if k.get("cluster") and k["cluster"] not in ids:
            problems.append(f"keyword {k['keyword']} has unknown cluster {k['cluster']}")
    for p in load_posts():
        for f in ("slug", "title", "meta_title", "meta_description", "body_markdown", "date_published", "cluster"):
            if not p.get(f):
                problems.append(f"post {p.get('slug')} missing {f}")
        for f in DRAFT_ONLY_FIELDS:
            if f in p:
                problems.append(f"post {p.get('slug')} still has draft field '{f}' (use the promote command)")
        try:
            if p.get("published_at"):
                parse_ts(p["published_at"])
            dt.date.fromisoformat(p["date_published"])
        except (ValueError, KeyError) as e:
            problems.append(f"post {p.get('slug')} has a bad date: {e}")
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


DRAFT_ONLY_FIELDS = ("brief", "review", "qa_errors")


def cmd_promote(slug: str) -> int:
    """Publish a hand-fixed parked draft: move it to posts with fresh dates,
    strip review-only fields, update keyword state, and rebuild."""
    src = DRAFTS_DIR / f"{slug}.json"
    if not src.exists():
        print(f"No draft {src}")
        return 1
    post = read_json(src)
    for k in DRAFT_ONLY_FIELDS:
        post.pop(k, None)
    stamp = now_utc().replace(microsecond=0).isoformat()
    post.update(date_published=today().isoformat(), date_modified=today().isoformat(), published_at=stamp)
    cfg = load_config()
    data = kw.load_universe()
    inv = site.inventory(load_posts(), kw.cluster_map(data), extra_cluster=post.get("cluster"))
    plan_like = {"primary_keyword": post["primary_keyword"], "content_type": post.get("content_type", "explainer")}
    brief_like = {"sources": [{"id": f"P{i}", **s} for i, s in enumerate(post.get("sources", []))]}
    result = qa.check(dict(post, sources_used=[f"P{i}" for i in range(len(post.get("sources", [])))]),
                      plan_like, brief_like, inv, cfg, check_links=False)
    if not result.ok:
        print("Draft still fails the automated checks:\n" + result.as_text())
        return 1
    write_json(POSTS_DIR / f"{slug}.json", post)
    src.unlink()
    kw.mark(data, post["primary_keyword"], "published", post=slug, published=today().isoformat())
    kw.save_universe(data)
    site.build(cfg, data)
    _summary(f"### Promoted draft: {post['title']}")
    _gh_output(result="published", slug=slug, url=f"{cfg['site']['base_url']}/blog/{slug}/")
    return 0
