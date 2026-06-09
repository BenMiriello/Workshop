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

## The set

| file | scenario | species | license | source |
|---|---|---|---|---|
| `single_oak-titmouse.wav` | single bird, clear song | Oak Titmouse | CC BY-NC-SA | [iNat 191980897](https://www.inaturalist.org/observations/191980897) |
| `single_nuttalls-woodpecker.wav` | single bird, sharp calls | Nuttall's Woodpecker | CC BY | [iNat 103864760](https://www.inaturalist.org/observations/103864760) |
| `repeated_california-towhee.m4a` | repeated chip notes (last-chirp case) | California Towhee | CC BY-NC | [iNat 80443952](https://www.inaturalist.org/observations/80443952) |
| `repeated_dark-eyed-junco.m4a` | rapid trill (dense notes) | Dark-eyed Junco | CC BY-NC | [iNat 111447052](https://www.inaturalist.org/observations/111447052) |
| `group_birdnet-sapsucker-woods.wav` | multi-species reference soundscape | several | CC BY-NC-SA 4.0 | [BirdNET-Analyzer](https://github.com/birdnet-team/BirdNET-Analyzer/tree/main/birdnet_analyzer/example) |
| `group_dawn-chorus-temperate.mp3` | dense dawn chorus | many (UK garden) | CC BY-SA 4.0 | [Commons](https://commons.wikimedia.org/wiki/File:Dawn_Chorus_2020-05-06_0500.mp3) |
| `group_rainforest-dawn-chorus.ogg` | very dense, out-of-range stress | many (India) | CC BY 4.0 | [Commons](https://commons.wikimedia.org/wiki/File:RainforestDawnChorus_Selaliparai_DM.ogg) |

Attribution (required by the CC BY / BY-SA / BY-NC licenses) is recorded per clip
in `manifest.json`.

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

**In-browser (the real path)** — play a clip near the device mic while *Listen*
is active on the Birds page to watch the live spectrogram markers place, hop, and
disambiguate. (Browsers can't capture file audio as a mic input directly; route
the file through a virtual audio device, or just play it on a speaker.)

## Gaps to fill later

- An **urban clip with traffic/machine noise + birds** (tests the non-bird
  filter + false positives). Not yet sourced cleanly — add to `fetch.sh` when found.
- True **two-species, well-separated** clips (current multi-bird coverage comes
  from the soundscapes). iNat hybrid recordings exist but aren't ideal.
