# DeRot promo plan: Threads and Instagram, pre-launch to launch

*Built 2026-10-09. Every post below is written, rendered, and queued in the automatic poster. Open `calendar.html` in this folder to see each one with its images.*

## Goal

Grow a launch list and a small, warm audience on Threads and Instagram before DeRot hits the App Store, then turn both into downloads in launch week. Success before launch is launch-list signups (derot.org/#newsletter) and follows. Saves on Instagram and replies on Threads come next, because those are what each platform rewards.

## Who and how

The audience is wellness-minded adults who scroll. They notice the feeling (a tight jaw, shallow breath, that wired buzz) and they bristle at addiction talk. The copy follows `../DeRot-Copy-Rules.md`:
- Lead with the felt experience. No mechanism jargon (vagus, heart rate, nervous system) in promo posts.
- "Block" is fine. "Lock" is never used to describe DeRot.
- Always mid-scroll, never "breathe before you open."
- Pre-launch posts never claim DeRot is available.

The build step enforces all of these, and the test suite fails if a post breaks one.

## Content pillars

| Day | Pillar | What it does | Example |
|---|---|---|---|
| Mon | Feeling | Names the wired-after-scroll state so people feel seen | "The feed doesn't feel stressful while you're in it. That's the tricky part." |
| Tue | Reset | Gives a 60-second technique people can use right now (and save) | Box breathing, 4-7-8, the physiological sigh, each linked to a free timer on derot.org/tools |
| Wed | Point of view | Stakes out "regulate, don't restrict" against blockers and guilt | "Hot take: 'use your phone less' is the wrong goal." |
| Thu | Product | Shows how DeRot works with real app screens | Mid-scroll pause, firmness levels, privacy, "Nicely done." |
| Fri | Building / conversation | Invites replies and shows the thinking behind the app | "What made you turn your app blocker off?" |

About one post in three carries a call to action, so the feed reads as useful first and promotional second.

## Cadence

| | Mon | Tue | Wed | Thu | Fri | Sat |
|---|---|---|---|---|---|---|
| Threads promo (21:00 UTC) | Feeling | Reset | POV | Product | Building | |
| Instagram promo (23:30 UTC) | | Carousel | | | | Single image |
| Blog share, automatic (Threads 16:30, Instagram 23:30) | Article | | Article | | Article | |

That's 5 Threads promo posts and 2 Instagram promo posts a week, on top of the 3 blog shares. Threads rewards frequent text posts. Instagram rewards fewer posts that are worth saving.

## Calendar (pre-launch: Oct 12 to Nov 7)

**Week 1: introduce the idea**
- Mon, Threads: the "check one thing, surface 20 minutes later" moment
- Tue, Instagram: *Meet DeRot* carousel (6 slides with real screens: pick apps, mid-scroll pause, breathing, done)
- Tue, Threads: 60-second calm breathing (in for 4, out for 6)
- Wed, Threads: minutes are not the problem, the feeling is
- Thu, Threads: how DeRot works in 3 steps (carousel of 3 screens)
- Fri, Threads: "what made you turn your app blocker off?"
- Sat, Instagram: *Scroll all you want. Just don't leave wired.*

**Week 2: be useful**
- Mon, Threads: signs the scroll has gotten to you
- Tue, Instagram: *Three 60-second resets for three moments* carousel
- Tue, Threads: box breathing, with the free timer link
- Wed, Threads: "use your phone less" is the wrong goal
- Thu, Threads: the three firmness levels (with the firmness screen)
- Fri, Threads: DeRot never stands at the door
- Sat, Instagram: *Regulate, don't restrict.*

**Week 3: how it works**
- Mon, Threads: the feed doesn't feel stressful while you're in it
- Tue, Instagram: *Choose how firmly DeRot holds you to the reset* carousel
- Tue, Threads: 4-7-8, with the free timer link
- Wed, Threads: you don't need to delete everything
- Thu, Threads: what DeRot doesn't need from you (privacy)
- Fri, Threads: "which app leaves you the most wired?"
- Sat, Instagram: *Signs the scroll has gotten to you* carousel

**Week 4: build toward launch**
- Mon, Threads: scrolling in bed
- Tue, Instagram: *What DeRot doesn't ask for* carousel (privacy)
- Tue, Threads: the physiological sigh, with the guide link
- Wed, Threads: keep scrolling and feel okay beats quit and feel guilty
- Thu, Threads: "Nicely done. Notice how you feel now." (with the screen)
- Fri, Threads: DeRot is almost here, join the launch list
- Sat, Instagram: *A calm reset for the apps that wind you up.*

## Launch week (held until you release it)

| Day | Threads | Instagram |
|---|---|---|
| 0 | "DeRot is live on the App Store" + 4-slide carousel + link | *DeRot is here.* carousel (pause, breathe, done, download) |
| 1 | Setup in three steps + link | |
| 2 | | *Your apps wind you up. DeRot brings you back down.* |
| 3 | "What did you notice after your first reset?" | |
| 5 | The breathing patterns, Pro custom routines + link | *Set up DeRot in three steps* carousel |

To release: set `site.app_store_url` in `seo/config.yaml`, then run `python -m derot_seo social launch --date YYYY-MM-DD` from `seo/` and push. The App Store link drops into the Threads posts automatically.

## If launch is later than Nov 7

The pre-launch calendar runs out on Nov 7. Options:
- Write weeks 5 and up in `seo/content/promo/plan.yaml` (same format) and run `social promo-build`.
- Repeat the best performers from weeks 1 to 4 with fresh wording.

Either way, the blog shares keep going three times a week.

## What still needs a person (15 minutes a day)

The automation posts; it can't take part in the conversation, and both platforms reward that.
- Reply to every comment and reply within a few hours of a post going out, especially the Friday question posts.
- On Threads, spend 10 minutes replying thoughtfully to people in breathwork, wellness, and digital wellbeing conversations. Be a person first; mention DeRot only when it is the honest answer.
- Set the Instagram and Threads bio link to **https://derot.org/** (the homepage has both the launch list and the latest articles; captions say "link in bio" for both).

## Measuring

Check weekly, in the platforms' own insights:
- Launch-list signups (newsletter provider)
- Follows on each platform
- Instagram saves and shares per post
- Threads replies and reposts per post

After week 2, double down on whichever pillar wins (likely Reset on Instagram and Feeling or POV on Threads), and trim the weakest.

## Running it

From `seo/`:
- `python -m derot_seo social promo-build`: renders images and queues every pre-launch post (run after editing `plan.yaml`). Posts that already went out are never touched; slots already in the past are skipped instead of bunched up.
- `python -m derot_seo social promo-build --start 2026-10-19`: shifts the whole pre-launch calendar to start on another Monday (for example if the Meta logins take longer to set up).
- `python -m derot_seo social promo-calendar`: refreshes `calendar.html`.
- `python -m derot_seo social launch --date YYYY-MM-DD`: queues launch week.
- `python -m derot_seo social status`: shows what is queued, posted, or failed.

To hold any single post, set `"approved": false` in `seo/content/social/promo-<id>.json` and push before its time. The hourly SEO social workflow does the posting; logins and setup are in `seo/SOCIAL.md`.
