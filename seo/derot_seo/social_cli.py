"""`python -m derot_seo social <action>` (run from seo/)."""
from __future__ import annotations

import datetime as dt
import getpass
import os
import shutil
import subprocess
import sys

from . import social
from .pipeline import _gh_output, _summary
from .util import SITE_DIR, load_config, load_posts, now_utc


def _post_by_slug(slug: str):
    p = next((p for p in load_posts() if p["slug"] == slug), None)
    if not p:
        raise SystemExit(f"No published post with slug {slug!r} in seo/content/posts/")
    return p


def _secret(env_name: str, prompt: str) -> str:
    v = os.environ.get(env_name)
    return v if v else getpass.getpass(prompt + ": ").strip()


def _offer_gh_secrets(values: dict) -> None:
    """Set GitHub Actions secrets through the gh CLI (values go over stdin, never argv)."""
    print()
    if not shutil.which("gh") or not sys.stdin.isatty():
        print("Add these as repository secrets (GitHub > derot-website > Settings > Secrets and variables > Actions):")
        for k, v in values.items():
            print(f"  {k} = {v}")
        return
    ans = input(f"Save {', '.join(values)} as GitHub Actions secrets with the gh CLI now? [y/N] ").strip().lower()
    if ans != "y":
        for k, v in values.items():
            print(f"  {k} = {v}")
        return
    for k, v in values.items():
        r = subprocess.run(["gh", "secret", "set", k], input=v, text=True, cwd=SITE_DIR, capture_output=True)
        print(f"  {k}: {'saved' if r.returncode == 0 else 'FAILED ' + r.stderr.strip()}")


def run(a, mock: bool = False) -> int:
    cfg = load_config()
    sc = social.scfg(cfg)
    now = dt.datetime.fromisoformat(a.now) if getattr(a, "now", None) else now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)

    if a.action == "prepare":
        if not sc.get("enabled"):
            print("social.enabled is false in config.yaml; nothing to do.")
            return 0
        posts = [_post_by_slug(a.slug)] if a.slug else social.missing_posts(cfg, now) if a.missing else []
        if not posts:
            print("Nothing to prepare." if a.missing else "Pass --slug SLUG or --missing.")
            return 0
        from .llm import make_llm
        llm = make_llm(cfg, mock=mock)
        held = 0
        for p in posts:
            try:
                pkg = social.prepare(p, cfg, llm, now=now, force=a.force)
            except Exception as e:
                print(f"PREPARE FAILED {p['slug']}: {type(e).__name__}: {e}")
                continue
            social.preview_html(pkg, cfg)
            held += 0 if pkg.get("approved") else 1
            print(f"Prepared {p['slug']}: {len(pkg['images'])} slides, "
                  + ", ".join(f"{n} {s['scheduled_at']}" for n, s in pkg["platforms"].items())
                  + ("" if pkg.get("approved") else "  [HELD: " + "; ".join(pkg.get("qa_errors") or ["approval required"]) + "]"))
        if llm.usage.cost_usd:
            print(f"Estimated API cost: ${llm.usage.cost_usd:.2f}")
        return social.EXIT_NEEDS_REVIEW if held else 0

    if a.action == "post":
        if not sc.get("enabled"):
            print("social.enabled is false in config.yaml; nothing to post.")
            return 0
        res = social.post_due(cfg, now=now, dry_run=a.dry_run, slug=a.slug, platform=a.platform)
        res["held for review"] = social.unnotified_holds(mark=not a.dry_run)
        lines = ["### Social posting"]
        for k in ("posted", "waiting", "skipped", "failed", "held for review"):
            lines += [f"- **{k}**: {x}" for x in res[k]]
        if len(lines) == 1:
            lines.append("- nothing due")
        _summary("\n".join(lines))
        _gh_output(posted=len(res["posted"]), failed=len(res["failed"]))
        if res["failed"]:
            return social.EXIT_FAILED
        return social.EXIT_NEEDS_REVIEW if res["held for review"] else 0

    if a.action == "status":
        print("\n".join(social.status_lines()))
        return 0

    if a.action in ("preview", "render"):
        slugs = [a.slug] if a.slug else [f.stem for f in sorted(social.SOCIAL_DIR.glob("*.json"))]
        for slug in slugs:
            pkg = social.load_package(slug)
            if not pkg or not pkg.get("copy") or pkg.get("kind") == "promo":
                print(f"{slug}: no article package (promo posts: `social promo-calendar`)")
                continue
            if a.action == "render":
                pkg["images"] = social.render_images(pkg, cfg, _post_by_slug(slug))
                social.save_package(pkg)
            print(social.preview_html(pkg, cfg))
        return 0

    if a.action == "reschedule":
        # Re-queue unposted platforms from now (after an outage, a late PR merge, or a fixed failure).
        if not a.slug:
            raise SystemExit("Pass --slug SLUG.")
        pkg = social.load_package(a.slug)
        if not pkg or not pkg.get("copy"):
            raise SystemExit(f"No package for {a.slug}")
        post = _post_by_slug(a.slug)
        fresh = social.schedule_for(cfg, {**post, "date_published": now.date().isoformat()}, now)
        for name, st in fresh.items():
            if (a.platform and name != a.platform) or pkg["platforms"].get(name, {}).get("status") == "posted":
                continue
            pkg["platforms"][name] = st
            print(f"{name}: queued for {st['scheduled_at']}")
        social.save_package(pkg)
        return 0

    if a.action in ("promo-build", "launch", "promo-calendar"):
        from . import promo
        plan = promo.load_plan()
        if a.action == "launch":
            if not a.date:
                raise SystemExit("Pass --date YYYY-MM-DD (the App Store release day).")
            counts = promo.build(cfg, plan, launch_date=dt.date.fromisoformat(a.date), now=now, phases=("launch",))
            print(f"Launch posts queued: {counts['queued']} (from {a.date}), already posted: {counts['kept']}")
        elif a.action == "promo-build":
            if a.start:
                plan["start"] = dt.date.fromisoformat(a.start)
                if plan["start"].weekday() != 0:
                    raise SystemExit("--start must be a Monday.")
            counts = promo.build(cfg, plan, now=now)
            print(f"Promo posts queued: {counts['queued']}, already posted (kept): {counts['kept']}, "
                  f"date already passed (skipped): {counts['past']}, launch posts rendered for review: "
                  f"{counts['launch_previewed']}")
        out = promo.calendar_html(cfg, SITE_DIR / "marketing" / "Promo-Plan-2026-10" / "calendar.html", plan)
        print(f"Calendar: {out}")
        return 0

    if a.action == "check":
        from .meta_api import MetaError
        clients = social.make_clients(cfg)
        ok = True
        for name in social.PLATFORMS:
            enabled = ((sc.get("platforms") or {}).get(name) or {}).get("enabled")
            c = clients.get(name)
            if not c:
                print(f"{name:<10} no credentials{'' if not enabled else ' (enabled in config: posts will wait)'}")
                ok = ok and not enabled
                continue
            try:
                info = c.whoami()
                extra = ""
                if name == "instagram":
                    lim = c.publishing_limit().get("data", [{}])[0]
                    extra = f", {lim.get('quota_usage', '?')} posts in the last 24h"
                print(f"{name:<10} OK: {info.get('username') or info.get('name')} ({info.get('id', '')}){extra}")
            except MetaError as e:
                ok = False
                print(f"{name:<10} ERROR: {e}")
        return 0 if ok else 1

    if a.action == "setup-facebook":
        from .meta_api import exchange_facebook
        print("Exchanges a short-lived user token from the Graph API Explorer for a Page token that does not expire.")
        app_id = os.environ.get("META_APP_ID") or input("Meta app ID: ").strip()
        secret = _secret("META_APP_SECRET", "Meta app secret (hidden)")
        short = _secret("META_USER_TOKEN", "Short-lived user token from Graph API Explorer (hidden)")
        res = exchange_facebook(app_id, secret, short, sc.get("graph_version", "v26.0"))
        pages = res["pages"]
        if not pages:
            print("No Pages found. Make sure you granted access to the DeRot Page in the Explorer dialog.")
            return 1
        for i, p in enumerate(pages):
            ig = p.get("instagram_business_account") or {}
            print(f"[{i}] {p['name']} (page {p['id']})  Instagram: {ig.get('username', 'not linked')} {ig.get('id', '')}")
        idx = 0 if len(pages) == 1 else int(input("Which Page? ").strip())
        p = pages[idx]
        ig = p.get("instagram_business_account") or {}
        if not ig:
            print("This Page has no linked Instagram professional account; Instagram posting will stay off.")
        values = {"META_PAGE_ID": p["id"], "META_PAGE_ACCESS_TOKEN": p["access_token"]}
        if ig.get("id"):
            values["META_IG_USER_ID"] = ig["id"]
        _offer_gh_secrets(values)
        return 0

    if a.action == "setup-threads":
        from .meta_api import exchange_threads
        print("Exchanges a short-lived Threads token (from the app's Threads token generator) for a 60-day token.")
        secret = _secret("THREADS_APP_SECRET", "Threads app secret (hidden)")
        short = _secret("THREADS_SHORT_TOKEN", "Short-lived Threads user token (hidden)")
        res = exchange_threads(secret, short)
        user = res.get("user") or {}
        print(f"Threads account: @{user.get('username')} ({user.get('id')}), token valid "
              f"{int(res.get('expires_in', 0)) // 86400} days (the weekly job refreshes it).")
        _offer_gh_secrets({"THREADS_USER_ID": str(user.get("id", "")), "THREADS_ACCESS_TOKEN": res["access_token"]})
        return 0

    if a.action == "refresh-threads-token":
        from .meta_api import ThreadsClient
        tok = os.environ.get("THREADS_ACCESS_TOKEN")
        if not tok:
            print("THREADS_ACCESS_TOKEN is not set; nothing to refresh.")
            return 0
        res = ThreadsClient(tok).refresh_token()
        new = res["access_token"]
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::add-mask::{new}")
        if a.out:
            fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(new)
        print(f"Threads token refreshed; valid for {int(res.get('expires_in', 0)) // 86400} days.")
        return 0
    return 2
