# DeRot SEO/GEO content engine

An automated system that publishes 3 researched, fact-checked articles a week to derot.org, aimed at our audience (wellness-minded scrollers). It plans what to write from a keyword map, researches each topic on the live web, writes in the DeRot voice, checks quality, and publishes. GitHub Actions runs everything; nothing runs on your Mac.

## How a run works

```
keywords.json --score--> shortlist --Claude planner--> plan (primary + secondary keywords, angle, format)
      --Claude + web search--> research notes + verified URL list --> structured brief
      --Claude writer--> article JSON (body, FAQ, takeaways, meta, social kit)
      --QA gate (deterministic) + Claude fact-check + Claude editor--> revise up to 2x
      --pass--> seo/content/posts/<slug>.json --build--> /blog/<slug>/ + hubs, sitemap, RSS, llms.txt, OG image
      --fail--> seo/content/drafts/<slug>.json + GitHub issue (nothing published)
```

**Choosing what to write.** Every keyword in `data/keywords.json` has volume, difficulty, and business-fit tiers, a cluster, and a format. The planner scores them (low difficulty weighs heavily because derot.org is a new domain), adds seasonal and Search Console boosts, rotates clusters so topics stay balanced, holds each cluster's pillar guide until two supporting articles exist, and skips anything that would cannibalize an existing post. Claude then picks from the top 8 and chooses secondary keywords to fold in.

**Quality gate.** An article only publishes if all of these pass:
- Every external link comes from the research step's actual search results (invented URLs are dropped automatically), and every cited source resolves.
- At least 3 sources, 2 of them authoritative (journals, NIH, universities, major clinics).
- Brand rules: no em-dashes, never "lock" for DeRot, no "phone addiction" framing, no cure/treatment claims, at most 4 product mentions.
- SEO structure: keyword in the title, first 100 words, and meta; 4+ H2 sections; answer-first opening; takeaways; FAQ; 2+ internal links to real pages; length right for the format.
- A separate Claude fact-check (with its own web searches) and a senior-editor review scoring 8/10 or higher with no blocking issues.

**What every article gets.** A canonical URL, BlogPosting + FAQPage + BreadcrumbList (+ HowTo) structured data with citations, a custom OG image, a table of contents, a "Try it now" link to the matching free breathing tool, sources, a safety disclaimer, related posts, a newsletter signup, and the App Store button once you set `app_store_url`. Sitemap, RSS, `llms.txt`, `llms-full.txt`, topic hubs, and the homepage "From the blog" block all update on every publish, and IndexNow notifies Bing (and through it ChatGPT search and Copilot) within minutes.

**Every publish also writes a social kit** to `seo/out/social/`: X, Threads, LinkedIn, Pinterest pin copy, a newsletter blurb, and a 20-30 second faceless short-video script.

**Every publish is also posted to Instagram, Threads, and Facebook** automatically: an Instagram carousel, a Threads post, and a Facebook link post, each with its own copy, on a schedule. Setup and controls: [SOCIAL.md](SOCIAL.md).

## Schedule

| Workflow | When | What |
|---|---|---|
| SEO publish | Mon, Wed, Fri 13:17 UTC | One article (capped at 3 per rolling 7 days, 20h minimum gap) |
| SEO weekly maintenance | Sun 14:05 UTC | Search Console pull, keyword discovery, refreshes one article older than 120 days, posts a report issue |
| SEO build | On push to posts, templates, or tools | Re-renders all generated pages |

## Deploy (about 15 minutes)

1. **Connect your Claude subscription** (the default, `llm.backend: subscription` in `config.yaml`):
   - On your Mac, in Terminal, run `claude setup-token`. Approve it in the browser, then copy the token it prints. It lasts one year, so put a reminder in your calendar.
   - Optional 30-second check that the token works: `echo "Say OK" | CLAUDE_CODE_OAUTH_TOKEN=<token> claude -p --model claude-opus-5-5 --tools ""` should print `OK`.
   - GitHub > derot-website > Settings > Secrets and variables > Actions > New repository secret: name `CLAUDE_CODE_OAUTH_TOKEN`, value the token.
   - Prefer pay-as-you-go instead? Set `llm.backend: api` and add an `ANTHROPIC_API_KEY` secret.
2. **Let Actions push.** Settings > Actions > General > Workflow permissions: "Read and write permissions". If you use `publishing.mode: review`, also tick "Allow GitHub Actions to create and approve pull requests".
3. **Commit and push** this folder, `.github/workflows/`, and the generated files (see the commit message in the handoff).
4. **Dry run first.** Actions > SEO publish > Run workflow, mode `dry-run`. When it finishes, download the `seo-dry-run` artifact and read the article (`blog/<slug>/index.html` opens in a browser). Nothing is committed in dry-run mode.
5. **First live post.** Run it again with mode `publish`. After that the schedule takes over.
6. **Cloudflare checks** (dash.cloudflare.com > derot.org):
   - SSL/TLS > Edge Certificates > **Always Use HTTPS: on.** Right now `http://derot.org` serves a duplicate copy of the site instead of redirecting.
   - Security > Bots / **AI Crawl Control**: make sure AI crawlers (GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot) are **allowed** and "managed robots.txt" is off, or Cloudflare will override our robots.txt and block the engines we want to be cited by.
7. **Search engines.**
   - Google Search Console: add the domain property `derot.org` (DNS TXT record in Cloudflare), submit `https://derot.org/sitemap.xml`.
   - Bing Webmaster Tools: import from Search Console. Bing powers ChatGPT search and Copilot results.
8. **Optional, recommended: connect Search Console data** so the planner learns from real rankings. In Google Cloud, create a service account, enable the Search Console API, download its JSON key, add the service account's email as a user (Restricted) on the Search Console property, then save the whole JSON as the `GSC_SERVICE_ACCOUNT_JSON` secret.

## Day-to-day

- **Nothing to do** unless an issue opens. Failed or parked runs open a GitHub issue labeled `seo`; the weekly report is an issue labeled `seo-report`.
- **Prefer to approve each post?** Set `publishing.mode: review` in `config.yaml`; each article arrives as a pull request to merge. Only one article waits for review at a time; scheduled runs skip while a `seo/...` pull request is open.
- **Force a topic:** Actions > SEO publish > Run workflow, enter a keyword.
- **Edit a published post:** change its JSON in `seo/content/posts/` and push; SEO build re-renders. Do not edit generated HTML in `blog/` (it is overwritten).
- **Unpublish:** delete the post JSON and push. Its pages and image are removed on the next build.
- **Parked draft:** fix the JSON in `seo/content/drafts/`, push, then run Actions > SEO build with `promote_slug` set to the draft's slug. Promote re-checks it, resets its dates, and publishes it.
- **At App Store launch:** set `site.app_store_url` in `config.yaml` and push. Every article's CTA switches to the App Store button, and the writer stops calling the app pre-launch.
- **Change the voice or rules:** `brand/voice.md` and `brand/seo-geo-playbook.md` are loaded into every prompt.
- **Add keywords:** append to `data/keywords.json` (same fields); the weekly job also adds up to 30 vetted keywords a week on its own.

## Privacy note

derot-website is a public repository (GitHub Pages requires that on the free plan), so everything committed here, including drafts, research briefs, the run log, Actions logs, and issues, is publicly readable. Search Console data is therefore never committed or printed: it is pulled fresh at the start of each run into a gitignored file, and the weekly report shows only counts. Keyword strings discovered from Search Console do get added to `data/keywords.json`.

## Local commands (from `seo/`)

```
pip install -r requirements.txt
python -m pytest                      # offline test suite (mock model, temp copy of the site)
python -m derot_seo plan              # see what it would write next and why
python -m derot_seo build             # re-render all generated pages
python -m derot_seo --mock publish --no-link-check   # offline dry run (writes into this checkout; revert after)
python -m derot_seo publish           # real run (needs ANTHROPIC_API_KEY)
python -m derot_seo weekly            # maintenance + report
python -m derot_seo refresh --slug x  # refresh one article now
python -m derot_seo promote <slug>    # publish a fixed draft
```

## Cost

**Subscription backend (default):** no per-article bill; runs count against your Claude plan's usage limits (the 5-hour session and weekly limits shared with your own Claude Code use). A publish run is roughly 10 to 14 Opus requests with web search; on a Max plan that should be comfortable at 3 a week, on Pro it may be tight on busy weeks. If a run hits a limit it fails cleanly, opens an issue, and the next scheduled run tries again. The `cost_usd` figures in the run log are Claude Code's list-price estimates, not charges. Anthropic's terms describe subscription auth as for "ordinary, individual usage" and point anyone building a product or service to API keys; publishing to your own site is not the prohibited case, but if this grows into heavier use, switch to `llm.backend: api`.

**API backend:** each article runs about 8 to 12 Claude Opus 5.5 calls plus 10 to 15 web searches. Expect roughly $2 to $5 per published article (about $30 to $60 a month at 3 a week); every run logs its estimated cost to `data/runs.jsonl`, and the weekly report shows the month's spend. Effort levels per stage are in `config.yaml` if you want to trade quality for cost.

## Files

| Path | Purpose |
|---|---|
| `config.yaml` | cadence, models, quality thresholds, scoring weights |
| `brand/voice.md`, `brand/seo-geo-playbook.md` | voice, copy rules, YMYL rules, SEO/GEO writing rules |
| `data/keywords.json` | keyword plan and status (`queued`, `published`, `covered`, `parked`, `tool`) |
| `data/briefs/`, `data/runs.jsonl` | research briefs and run log (audit trail) |
| `content/posts/`, `content/drafts/` | article source of truth |
| `templates/`, `assets/blog.css`, `pages/about.md` | page design |
| `out/social/`, `out/reports/` | social kits and weekly reports |
