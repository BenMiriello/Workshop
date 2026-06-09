# Bird-audio test clips

A small, diverse, CC-licensed set for testing detection and the spectrogram
marker localization (`frontend/src/pages/apps/birds/specLocalize.ts`) on real
audio — single birds, repeated-call birds, and dense multi-species soundscapes.

## Why these sources (not xeno-canto)

xeno-canto would be the obvious choice, but its API + site are now behind an
**Anubis** anti-bot proof-of-work wall, so a plain `curl`/script gets "Access
Denied". These sources are directly fetchable and CC-licensed:

- **iNaturalist** (`static.inaturalist.org`) — per-species, much of it South Bay;
  same source the app already uses client-side for photos.
- **Wikimedia Commons** (`upload.wikimedia.org`) — dawn-chorus soundscapes.
- **BirdNET-Analyzer repo** — the canonical Sapsucker Woods test soundscape, so
  results are ground-truthable against BirdNET's own published output.

## Fetching

The workshop box blocks outbound shell traffic (egress-guard), so it can't pull
these itself. Run from your laptop (or any networked machine) and copy over:

```sh
cd birdnet/test-clips && ./fetch.sh
```

Binaries are gitignored; `manifest.json` + `fetch.sh` are the reproducible
record. Full provenance (license + attribution + source page) per clip is in
`manifest.json`.

## The set (~19 clips)

`manifest.json` is the authoritative list (file, scenario, species, license,
attribution, source page). By category:

- **single bird, clear** — Oak Titmouse, Nuttall's Woodpecker, American Robin,
  Song Sparrow, Spotted Towhee, White-crowned Sparrow, Bewick's Wren, Black
  Phoebe, Lesser Goldfinch.
- **frequency extremes** — Anna's Hummingbird + Bushtit (very high), Mourning
  Dove (very low coo), American Crow (harsh broadband) — exercise the vertical
  (frequency) placement across the band.
- **repeated-call / dense** — California Towhee (chips), Dark-eyed Junco (trill)
  — the last-chirp + dense-note cases.
- **urban** — House Sparrow.
- **multi-species soundscapes** — BirdNET Sapsucker Woods (reference,
  ground-truthable), temperate dawn chorus (UK garden), rainforest dawn chorus
  (out-of-range stress for the range filter).

Licenses are a mix of CC0 / CC BY / CC BY-NC / CC BY-NC-SA / CC BY-SA;
attribution (required by all but CC0) is per clip in `manifest.json`.

## Using them

**Detection / localizer-input replay** — feed a clip through the local analyzer
exactly like the live recorder (3s windows, 1s step) and print the per-window
confidence series the localizer consumes:

```sh
python3 replay.py group_birdnet-sapsucker-woods.wav
# with the range filter, e.g. Bay Area in late May:
python3 replay.py single_oak-titmouse.wav --lat 37.4 --lon -122.1 --week 23
```

Needs `ffmpeg` and the analyzer on `127.0.0.1:8012`. This shows what the
multi-species / repeated-chirp cases actually produce, and whether the range
filter flags the rainforest clip's species as out-of-range.

**Batch correctness scan** — `./test_all.sh [--lat .. --lon .. --week ..]` runs
every clip and prints just the per-species summary. For a single-bird clip the
summary should list ONLY that species; extras = a mis-ID to chase. This is the
loop for fine-tuning the accuracy knobs against the whole set.

**Per-call localization (Phase 2 engine)** — `locate.py <clip> "<scientific name>"
[--start S]` finds WHERE in a 3s window a species' call is, by occlusion: mask a
sliding sub-window, re-score, and the mask that most drops the species'
confidence sits on the call. Works with the existing model (no gradients/
downloads) via the live `/analyze`. Crisp for clear calls (Oak Titmouse: masking
7.1–8.0s drops conf 0.46→0.00); noisier at low baseline confidence. This is the
de-risked core for the planned service `/locate` endpoint + marker refinement of
ambiguous ("position uncertain") detections.

**Headless browser (the REAL pipeline, automated)** — `browser_replay.js` launches
headless Chrome with a clip fed in as the *mic* (Chrome's fake-audio-capture),
clicks Listen, and exercises the actual in-browser path (FFT heatmap →
`localizeEvents` → DOM marker placement) — the only way to test marker placement
on real audio without a live mic. Prints the rendered marker chips over time and
saves a screenshot. No npm installs (uses Playwright's cached Chromium + Node's
built-in WebSocket over raw CDP); needs ffmpeg, the dev server, and the analyzer.

```sh
node browser_replay.js group_birdnet-sapsucker-woods.wav 26000 /tmp/shot.png
node browser_replay.js repeated_california-towhee.m4a            # repeated-chirp case
```

**Manual in-browser** — or just play a clip near the device mic while *Listen* is
active on the Birds page and watch the markers place, hop, and disambiguate.

## Gaps still open

- An **urban clip with real traffic/machine noise + birds** (tests the non-bird
  "Engine/Noise" filter + false positives). Wikimedia Commons searches for this
  returned no usable audio; House Sparrow is only an urban-*species* proxy, and
  the BirdNET soundscape's "Engine" detection partly covers it. Add to `fetch.sh`
  when a clean source turns up (Internet Archive / freesound are candidates).
- A true **two-species, well-separated** clip (current multi-bird coverage is via
  the soundscapes). iNat hybrid recordings exist but aren't ideal.
