# DeRot Social Kit: Amber (2026-10-08)

Replaces the cream and green kit in `../Social-Profiles-2026-07-26/` (kept for reference). Bios are unchanged; see that folder's kit doc for per-platform copy.

## Files

- `icon-master-1024.png`: the approved Horizon icon render (`DeRot/docs/redesign/10-app-icon-horizon-1024.png`). Every profile photo is a straight resize of it. The sun sits center-right of the horizon, so it survives the circular crop on every platform.
- `profile-photos/`: Facebook, Instagram and Threads at 320, Reddit at 256, TikTok at 200.
- `covers/derot_facebook_cover_1640x624.png`: uploaded at 2x. The sun sits left and the text right, clear of the desktop avatar overlap.
- `covers/derot_reddit_banner_1920x384.png`: centered text for the mobile crop, with the sun off to the right.

The site's link-preview image lives at `../../img/og-image.png` (1200x630).

## Regenerating

Templates and scripts live outside this public repo, in `Derot Parent/Screenshots/tools/` (`cover.html`, `frame.html`, `render_frames.py`, `flatten.swift`). They render with headless Chrome using the app's bundled fonts.

If the shipped Icon Composer icon differs visibly from the approved render, re-export the profile photos from that icon so they match the home screen.
