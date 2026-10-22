# Reel audio

Drop background tracks here (.m4a, .mp3, .aac or .wav). Each reel gets one, picked by
its slug, so tracks rotate across posts and a re-render keeps the same one.

- Use CC0 (public domain) tracks only, e.g. freepd.com. This repo is public and the
  reels are served from derot.org, so the raw files and the audio are both out in the
  open. Meta Sound Collection is licensed for use on Meta products only, so it doesn't fit.
- Calm, slow, no vocals, and audible from the first second (no long fade in).
- No trimming needed: a reel uses the start of the track, loops it if it's short, and
  fades out at the end.
- Volume is `social.reels.volume` in seo/config.yaml.
- An empty folder means silent reels.

After adding or removing tracks, run `python3 -m derot_seo social promo-build` so queued
reels re-render. Posted reels are never touched.
