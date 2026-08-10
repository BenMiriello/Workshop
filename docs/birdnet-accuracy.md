# BirdNET accuracy — why it stops reporting birds it never heard

Raw BirdNET is eager: out of the box it will tag birds that cannot possibly be
where you are, and it will announce a species off a single 3-second blip. The
Birds app layers four filters over the model so the detections it keeps are
trustworthy. This doc is the rationale + the knobs; the code lives in
`backend/services/birds_analyze.py`, `apps/birds/analyzer/service.py`, and (for the
always-on yard Pi) `apps/birds/brain/config.yaml`.

## The four layers

1. **Range / occurrence filter (biggest win).** BirdNET ships a second "meta"
   model that takes latitude + longitude + week-of-year and returns each
   species' eBird checklist frequency (0–1) for that place and time. We feed the
   listening location into it, so the classifier only considers species that
   actually occur here this week. At Los Altos in late spring this cuts the
   candidate set from **6,522 species to ~235 (3.6%)** — i.e. ~96% of the
   world's species become "impossible here" and are dropped. The kept top is
   exactly right (Common Raven, Mourning Dove, House Finch, California
   Scrub-Jay). The cutoff (`sf_thresh`) is kept **low** on purpose: set it high
   and you silently lose genuinely-rare local visitors (owls, vagrants) — the
   classic over-correction. We'd rather keep a rare-but-possible bird and lean
   on confidence + consensus than erase it.

2. **Confidence floor.** BirdNET confidence is **not** a probability — Cornell's
   own authors say so; the same score means different precision for different
   species. It is a precision/recall knob. We floor it at 0.5 (BirdNET-Pi ships
   0.7, BirdNET-Go 0.8). The old browser path ran at 0.25, which is below what
   anyone trusts.

3. **New-species consensus.** A species never confirmed at this location must be
   detected `new_species_hits` times (default 3) within a 15s rolling window
   before it is recorded at all. This defeats one-off spurious hits — the
   adaptation of BirdNET-Go's "deep detection" idea to our 1s live cadence.
   Species already established here record immediately (then dedup within 2.5s so
   a continuing song is one row, not one per second).

4. **Occurrence prior as a second axis.** We store the eBird occurrence value
   alongside the acoustic confidence on every detection (the BirdWeather
   "Confidence × Probability" pattern) and surface it as "Local likelihood" on
   the species page. It's a soft signal you can read, not a hard gate — so a real
   rarity is visible-but-flagged rather than deleted.

Nothing is lost: every detection still rides back in the live response flagged
with `in_range` / `occurrence` / `provisional`, so the UI can show what was heard
and why something was withheld. Only confirmed, in-range, above-floor hits are
persisted. Non-bird noise classes (Engine, Dog, Human, …) bypass range/consensus
(they have no eBird range) but are still stored and hidden by the display toggle.

## Tuning

Live, per-install, from **Birds → Settings → Detection accuracy** (persisted in
`app_settings`, read on every chunk via `smart_settings()`):

| Setting | Default | Effect |
|---|---|---|
| `min_conf` | 0.5 | acoustic confidence floor |
| `sf_thresh` | 0.01 | range-filter strictness (Lenient 0.001 / Balanced 0.01 / Strict 0.05) |
| `new_species_hits` | 3 | consensus hits to confirm a new species |
| `sensitivity` | 1.0 | sigmoid scaling; leave at 1.0 |
| `prior_floor` | 0.0 | display-only occurrence cutoff |

## Trust model — suggested / confirmed / rejected

Hard filters cut the impossible; human curation handles the plausible-but-wrong
(a species that's in-range and above the floor but wasn't actually here — e.g. a
Belted Kingfisher heard once). Every stored detection has a status:

- **suggested** — survived the hard filters but the species isn't vouched for near
  here yet. The default home view shows *confirmed* only; suggestions live behind
  a tab.
- **confirmed** — auto (a trust anchor covers it) or manual.
- **rejected** — rejected by hand, or auto-rejected by a mute anchor.

**Radius trust anchors** (`species_area` table). Confirming species S at a point
arms an *auto-confirm* anchor; future S within `trust_radius_km` (default 5) of
any anchor auto-confirms. Muting arms an *auto-reject* anchor. The radius is a
labor knob, not correctness — correctness is suggested-by-default: a species you
never confirm never enters the confirmed view, at any radius. The first manual
confirm arms the anchor and sweeps that species' nearby suggestions to confirmed;
toggling auto-confirm off locks it from re-arming.

**New arrivals.** A species' first local appearance is tracked unreviewed; it
drives a badge count and (in the feed) pins to the top until acknowledged.
Acknowledged = you tapped into it OR decided — any of those clears it.

Endpoints: `/suggestions`, `/new-count`, `/confirm`, `/reject`,
`/detection/status`, `/species/{review,auto,mute}`. Reads take `?status=`
(default confirmed; `all` for the species page). Engine: `birds_analyze.py`
`_classify` / `confirm_detections` / `mute_species` / `set_species_auto`.

## The always-on yard Pi (BirdNET-Go)

`apps/birds/brain/config.yaml` carries the equivalent settings for the production
path: `birdnet.threshold: 0.65`, `rangefilter.threshold: 0.01`, `overlap: 2.0`
(deep detection — required hits = `max(1, 3 / max(0.1, 3.0 - overlap))` ≈ 3), and
`realtime.dynamicthreshold` for per-species recent-history-aware thresholds.

## Sources

- Wood & Kahl 2024, *Guidelines for appropriate use of BirdNET scores* (Cornell
  K. Lisa Yang Center), J. Ornithology — DOI 10.1007/s10336-024-02144-5.
  Confidence is not calibrated; recommends logit-space calibration + ≥2-day rule.
- BirdNET species range model — github.com/birdnet-team/geomodel ;
  Analyzer discussion #234.
- BirdNET-Analyzer CLI (`--min_conf`, `--sensitivity`, `--sf_thresh`) —
  birdnet-team.github.io/BirdNET-Analyzer/usage/cli.html
- BirdNET-Go deep detection + dynamic thresholds —
  github.com/tphakala/birdnet-go/wiki/Realtime-Analysis
- BirdWeather Confidence vs Probability — birdweather.com/faqs
- birdnetlib (range filter via lat/lon/date on Recording; `species_class.return_list`) —
  joeweiss.github.io/birdnetlib/getting-started/
