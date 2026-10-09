# Social posting: Instagram, Threads, Facebook

Every article the blog engine publishes also goes out on Instagram, Threads, and the DeRot Facebook Page with copy and images made for each platform. Nothing to do day to day once the tokens are in place.

## What gets posted

| Platform | Format | Default time (UTC) |
|---|---|---|
| Instagram | Carousel: cover, 3 to 5 takeaway slides, closing slide. Caption with a hook, 3 to 6 short paragraphs, "Full guide: link in bio.", 3 to 5 hashtags. Alt text on every slide. | 23:30 (7:30pm ET) |
| Threads | Short conversational post with the article link, plus the cover slide | 16:30 (12:30pm ET) |
| Facebook Page | 2 to 4 short paragraphs with the article's link card (OG image), link tagged `utm_source=facebook` | 16:30 (12:30pm ET) |

Slides are 1080x1350 JPEGs in the Amber look (DM Serif Display and DM Sans, ember glow, horizon line, app icon on the closing slide).

## How it works

```
article publishes (SEO publish workflow)
  --Claude (one call, from the article text only)--> slide text + 3 captions
  --copy gate--> no em-dashes, brand rules, no numbers or links that are not in the article, platform length limits
       fail: one automatic rewrite; still failing: package is HELD and an issue opens
  --Pillow--> img/social/<slug>/slide-NN.jpg   (deployed with the article)
  --> seo/content/social/<slug>.json            (copy, images, a time slot per platform)

SEO social workflow, hourly at :40
  --> anything due whose images are live on derot.org? post it, write the id + permalink back into the JSON, commit
```

- If social prep fails during publishing, the article still publishes; the hourly run notices the missing package and makes it.
- A post that errors is retried after 1h, then 2h; on the third failure it is marked `failed` and an issue labeled `seo-social` opens (at most one a day).
- Nothing is ever posted for an article that has been unpublished, or more than 72 hours late.
- Sundays: the Threads token is refreshed and every login is checked; a broken login opens an issue.

## One-time setup (about 30 minutes)

### 1. Accounts
- Instagram must be a **professional account** (Business or Creator) **linked to the DeRot Facebook Page**. In Instagram: Settings > Account type and tools. Link it from the Page: Meta Business Suite > Settings > Linked accounts > Instagram.
- Set the Instagram and Threads bio link to `https://derot.org/`. Captions say "link in bio" for both articles and the launch list, and the homepage has both.

### 2. Meta app
1. [developers.facebook.com](https://developers.facebook.com/apps) > Create app, under the NinetyFourVentures business portfolio. Add these use cases:
   - Manage everything on your Page
   - Manage messaging and content on Instagram (the "API setup with Facebook login" variant)
   - Access the Threads API (permissions `threads_basic`, `threads_content_publish`)
2. Leave the app **unpublished (development mode)**. You only post to accounts you own and you are the app's admin, so App Review is not needed.
3. App roles > Roles > add your Threads account as a **Threads Tester**, then accept the invite in the Threads app: Settings > Account > Website permissions > Invites.

### 3. Facebook + Instagram token (never expires)
1. Open the [Graph API Explorer](https://developers.facebook.com/tools/explorer/), select the app, choose "Get User Access Token" with these permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`. Approve the DeRot Page and Instagram account when asked.
2. Copy the token, then from `seo/` run:
   ```
   python -m derot_seo social setup-facebook
   ```
   It asks for the app ID and app secret (App settings > Basic) and the token (hidden input), swaps it for a Page token that does not expire, finds the linked Instagram account, and offers to save `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN`, `META_IG_USER_ID` as GitHub secrets through the `gh` CLI.

### 4. Threads token (60 days, refreshed weekly)
1. In the app: Use cases > Access the Threads API > Settings. Note the **Threads app secret** (it differs from the main app secret) and generate a user token for your tester account.
2. Run `python -m derot_seo social setup-threads`. It swaps the 1-hour token for a 60-day one and offers to save `THREADS_USER_ID` and `THREADS_ACCESS_TOKEN`.
3. So the token renews itself: create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new) limited to the derot-website repo with **Secrets: Read and write**, and save it as the secret `SOCIAL_SECRETS_PAT`. Without it, re-run step 2 every 60 days. The PAT itself expires (a year at most), so put a reminder on the calendar.

### 5. Test
1. Actions > SEO social > Run workflow > `maintenance`: should print OK with the account name for all three.
2. Actions > SEO social > Run workflow > `dry-run`: shows what is due without posting.
3. The next published article posts on its own. To post a due item right away: Run workflow > `post`.

Every platform is independent: one with no secrets just waits, and the others still post.

## Day to day

- **See what is queued:** `python -m derot_seo social status`, or open `seo/content/social/<slug>.json`.
- **Review before it posts:** open `seo/out/social/<slug>-preview.html` from the publish run's artifact (dry run) or locally. It shows the slides, every caption with its length, and the schedule.
- **Hold a post:** set `"approved": false` in the JSON (or one platform's `"status": "skipped"`) and push before its time slot.
- **Edit copy:** edit the JSON and push. If you changed slide text, run `python -m derot_seo social render --slug <slug>` and commit the new images.
- **Approve everything by hand:** set `social.require_approval: true` in `config.yaml`; packages then arrive with `"approved": false` and wait for you to flip it.
- **Retry or re-time:** `python -m derot_seo social reschedule --slug <slug>` re-queues every platform that has not posted, from now (useful after a failure or a late review-mode merge).
- **Regenerate copy and slides:** `python -m derot_seo social prepare --slug <slug> --force` (platforms that already posted are left alone).
- **Change times, formats, or platforms:** `social:` in `config.yaml`. `threads.media` can be `cover`, `carousel` (all slides), or `link` (text with a link preview). `facebook.format` can be `link` or `photos` (the carousel as a photo post).

## Promo plan

Besides article shares, a hand-written promotional calendar for Threads and Instagram lives in `content/promo/plan.yaml`. It has four pre-launch weeks (Threads Mon to Fri, Instagram Tue and Sat) plus a launch week that stays held until you release it. The strategy, calendar, and launch steps are in `../marketing/Promo-Plan-2026-10/README.md`, and every post with its images is in `../marketing/Promo-Plan-2026-10/calendar.html`.

`social promo-build` checks every post against the copy rules (including the consumer rules: no vagus, heart rate, or nervous-system wording, and no "available now" before launch), renders the images to `img/social/promo-<id>/`, and queues each post as `content/social/promo-<id>.json`. The same hourly workflow posts them. `social launch --date YYYY-MM-DD` queues launch week once `site.app_store_url` is set.

## Cost

One model call per article (two if the first draft fails the gate), made through whichever backend `llm.backend` selects. On the subscription backend it counts against your plan; on the API backend it is roughly $0.10 to $0.40. Promo posts are hand-written, so they cost nothing to run. The hourly check takes a few seconds of Actions time when nothing is due.

## Files

| Path | Purpose |
|---|---|
| `derot_seo/social.py` | copy prompt and gate, packages, scheduling, posting |
| `derot_seo/social_images.py` | slide renderer |
| `derot_seo/meta_api.py` | Instagram, Facebook Page, and Threads clients; token helpers |
| `derot_seo/social_cli.py` | `python -m derot_seo social ...` |
| `.github/workflows/seo-social.yml` | hourly posting, Sunday token refresh and login check |
| `content/social/<slug>.json` | the queue and the record of what posted |
| `../img/social/<slug>/` | slide images (public, so Meta can fetch them) |
