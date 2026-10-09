"""CLI: python -m derot_seo <command> (run from the seo/ directory)."""
from __future__ import annotations

import argparse
import sys

from . import pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="derot_seo", description="DeRot SEO/GEO content engine")
    ap.add_argument("--mock", action="store_true", help="use the offline mock model (no API calls)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("publish", help="plan, write, gate, and publish one article")
    p.add_argument("--keyword", help="force a specific primary keyword")
    p.add_argument("--force", action="store_true", help="ignore the weekly cadence cap")
    p.add_argument("--no-link-check", action="store_true", help="skip live HTTP checks of source URLs")

    b = sub.add_parser("build", help="re-render every generated page from post JSON")
    b.add_argument("--images", action="store_true", help="regenerate OG images too")

    pl = sub.add_parser("plan", help="show the keyword shortlist and cadence state")
    pl.add_argument("-n", type=int, default=12)

    r = sub.add_parser("refresh", help="update an aging article with new research")
    r.add_argument("--slug")
    r.add_argument("--no-link-check", action="store_true")

    pr = sub.add_parser("promote", help="publish a hand-fixed draft from content/drafts")
    pr.add_argument("slug")

    sub.add_parser("weekly", help="Search Console pull, keyword expansion, weekly report")
    sub.add_parser("validate", help="static checks on config, keywords, and posts")

    so = sub.add_parser("social", help="Instagram, Threads, and Facebook posts for each article")
    so.add_argument("action", choices=["prepare", "post", "status", "preview", "render", "reschedule", "check",
                                       "promo-build", "promo-calendar", "launch",
                                       "setup-facebook", "setup-threads", "refresh-threads-token"])
    so.add_argument("--slug", help="limit to one article")
    so.add_argument("--platform", choices=["facebook", "threads", "instagram"])
    so.add_argument("--missing", action="store_true", help="prepare: every recent post without a package")
    so.add_argument("--force", action="store_true", help="prepare: regenerate copy and images")
    so.add_argument("--dry-run", action="store_true", help="post: show what is due without posting")
    so.add_argument("--now", help="post: pretend it is this ISO time (UTC)")
    so.add_argument("--out", help="refresh-threads-token: file to write the new token to")
    so.add_argument("--start", help="promo-build: move the pre-launch calendar to start this Monday")
    so.add_argument("--date", help="launch: the App Store release day (YYYY-MM-DD)")

    ix = sub.add_parser("indexnow", help="notify Bing/Yandex/etc. of changed URLs")
    ix.add_argument("urls", nargs="*")

    a = ap.parse_args(argv)
    if a.cmd == "publish":
        return pipeline.cmd_publish(mock=a.mock, keyword=a.keyword, force=a.force,
                                    check_links=not a.no_link_check)
    if a.cmd == "build":
        return pipeline.cmd_build(regenerate_images=a.images)
    if a.cmd == "plan":
        return pipeline.cmd_plan(a.n)
    if a.cmd == "refresh":
        return pipeline.cmd_refresh(mock=a.mock, slug=a.slug, check_links=not a.no_link_check)
    if a.cmd == "promote":
        return pipeline.cmd_promote(a.slug)
    if a.cmd == "weekly":
        return pipeline.cmd_weekly(mock=a.mock)
    if a.cmd == "validate":
        return pipeline.cmd_validate()
    if a.cmd == "social":
        from . import social_cli
        return social_cli.run(a, mock=a.mock)
    if a.cmd == "indexnow":
        from .indexnow import ping
        return ping(a.urls)
    return 2


if __name__ == "__main__":
    sys.exit(main())
