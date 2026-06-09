# Birds — Recordings system plan

Save the audio clip for each detection, under explicit per-bird rules and a hard
storage budget. Browser-mic path only for v1 (the yard/Pi path already records
its own clips natively — see §9). Nothing here changes the privacy boundary:
clips are full-fidelity and stay tailnet-private; `publish.sh` already aborts on
any clip leak.

## 0. Reality check (grounds the defaults)

- Host disk is **94% full (≈121 GB free of 1.8 TB)**. The cap is not theoretical;
  it must default conservative and never run the host out of space.
- Browser path currently keeps **no** audio: `analyze_chunk` transcodes the
  incoming chunk to a temp 48k-mono WAV, analyzes it, and `os.unlink`s it in a
  `finally`. The recording feature is "stop deleting it, encode + file it, under
  rules."
- A live chunk is the **3 s BirdNET window**, posted every 1 s (overlapping).
  Storage dedup already collapses a continuing song to one detection row
  (`DEDUP_S = 2.5`). So **one recording == one stored detection row** is the
  natural unit; we never store the 1 s overlap spam.
- Raw 48k/16-bit mono WAV ≈ **288 KB / 3 s**. Encoded **MP3 64 kbps mono ≈ 24 KB
  / 3 s** (~12×). `ffmpeg` has `libmp3lame`, `libopus`, `aac`, `libvorbis`.

## 1. What gets recorded (the gate)

Recording hooks at the **same point as row storage**, after every existing gate,
so the recording rules inherit them for free:

1. range filter (impossible-here species already dropped),
2. confidence floor (`smart_settings.min_conf`),
3. dedup (2.5 s),
4. trust status (muted → not stored → not recorded).

On top of those, a clip is captured for a stored detection **iff**:

- `is_bird == 1` — **the non-bird filter applies to recording too.** Engine /
  Human vocal / Dog / frogs etc. are never recorded. (Matches the display
  default; no per-species override for noise classes.)
- the species' recording pref is `record = on` **or** `keep_all = on`
  (see §3). Default is **off** — "recordings only get made for birds when set
  that way." A bird heard with recording off is still detected and counted; it
  just leaves no clip.
- AND storage admits it (see §5).

> The display confidence floor is retroactive (raising it hides old rows). A clip
> already on disk stays tied to its row; if the row is later hidden by the floor,
> the clip simply isn't surfaced. Clips are not re-deleted by a floor change.

## 2. Audio format & files

- Encode the analyzed WAV to **MP3 64 kbps mono** (`-c:a libmp3lame -b:a 64k
  -ac 1`). Chosen for universal `<audio>` playback (incl. older Safari). ~24 KB
  per 3 s clip. *(Alternative: Opus 48k ≈ 18 KB, smaller, fine on the operator's
  modern Apple devices — a one-line codec swap if we'd rather. MP3 is the safe
  default.)*
- Clips live under **`~/birdnet/data/clips/<location_id>/<ts>_<sciSlug>_<detId>.mp3`**,
  separate from yard clips, never published.
- **One file per recording row.** If a single 3 s chunk yields two recordable
  species (uncommon), each gets its own copy — keeps "delete this recording"
  independent and trivial. (~24 KB duplication; not worth a refcount.)
- Encoding happens **once per chunk**, only when at least one detection in that
  chunk is recordable: encode to a temp MP3, then copy to each recording's final
  path. Move the existing `os.unlink(wav)` to after this step.

## 3. Data model

New table (app-managed clips, clean usage accounting):

```sql
CREATE TABLE recordings (
  id              INTEGER PRIMARY KEY,
  det_id          INTEGER,        -- FK browser_detections.id (informational)
  scientific_name TEXT,
  common_name     TEXT,
  location_id     INTEGER,
  device_token    TEXT,
  ts              TEXT,           -- detection time (UTC iso, like detections)
  path            TEXT,           -- relative to clips root
  bytes           INTEGER,
  duration_s      REAL,
  confidence      REAL,
  pinned          INTEGER DEFAULT 0   -- "save this recording" (protected)
);
CREATE INDEX idx_rec_sci ON recordings(scientific_name);
CREATE INDEX idx_rec_ts  ON recordings(ts);
```

Per-species recording prefs:

```sql
CREATE TABLE species_recording (
  scientific_name TEXT PRIMARY KEY,
  record          INTEGER DEFAULT 0,  -- capture clips for new detections
  keep_all        INTEGER DEFAULT 0   -- never auto-evict this species' clips
);
```

`keep_all` implies `record` (UI couples them: turning on "save all recordings"
turns on recording). Created/migrated in `_conn()` like the other tables.

Global settings reuse the existing `app_settings` k/v + `get_setting/set_setting`:

| key                | default      | meaning                                   |
|--------------------|--------------|-------------------------------------------|
| `rec_storage_mb`   | `1024`       | total cap for app-managed clips (1 GB)    |
| `rec_when_full`    | `evict_old`  | `evict_old` \| `stop`                     |
| `rec_warn_ratio`   | `0.9`        | warn banner threshold (fraction of cap)   |
| `rec_last_skip_ts` | (unset)      | set when an eligible clip was skipped full |

(`rec_format`, `rec_warn_ratio` are not surfaced in the UI v1.)

## 4. Protection tiers (what eviction may touch)

- **rolling** — `record=on, keep_all=off`, clip not pinned → evictable (FIFO by
  `ts`, oldest first).
- **keep_all species** — every clip of a species with `keep_all=on` → protected.
  ("Save all recordings of this bird.")
- **pinned recording** — a single clip with `pinned=1` → protected, regardless of
  species pref. ("Save this recording.")

Protected clips are never auto-deleted. They go only by an explicit manual
delete (with confirm) — and the UI states the consequence: a pinned clip is the
last to go; eviction deletes everything else first.

## 5. Storage logic & eviction

On each recordable detection, inside the `_conn()` transaction (single uvicorn
worker → no extra lock needed):

```
used      = SUM(bytes) over recordings
protected = SUM(bytes) where pinned=1 OR species.keep_all=1
cap       = rec_storage_mb * 1MB
need      = size of the new clip (~24KB)

if used + need <= cap:
    write clip, insert row
elif rec_when_full == 'evict_old':
    free = used - protected               # bytes we are allowed to reclaim
    if (cap - protected) >= need:
        delete oldest rolling clips (file + row) until used + need <= cap
        write clip, insert row
    else:
        # protected clips alone exceed the budget — cannot make room
        skip; set rec_last_skip_ts = now   -> drives the ERROR banner
else:  # 'stop'
    skip; set rec_last_skip_ts = now       -> drives the ERROR banner
```

Key consequences (match the spec):

- Normal `evict_old` that frees room by dropping old clips → **no warning, no
  error.** Silent, expected.
- `stop` mode that hits the cap → it stopped recording new clips → **error.**
- `evict_old` where pinned + keep_all alone fill the budget so nothing can be
  freed → **error** ("too many saved recordings").

File and DB row are always deleted together; an eviction that removes a file also
removes its row.

## 6. Banner state (homepage)

`GET /birds/recordings/status` computes, live from sizes:

```
state = 'ok'
if no species set to record and used == 0:  state = 'ok'      # feature idle
warn  = used >= warn_ratio*cap  OR  protected >= warn_ratio*cap
no_room = used + avg_clip > cap
error = (rec_when_full=='stop'   and no_room and any_species_record)
     OR (rec_when_full=='evict_old' and (cap-protected) < avg_clip and any_species_record)
     OR (rec_last_skip_ts within last ~10 min)
state = 'error' if error else 'warn' if warn else 'ok'
```

Returns `{state, message, used_bytes, cap_bytes, protected_bytes, fix_path}`.

**Birds home** (`Birds.tsx`) renders a banner **only when `state != ok`**:

- `warn` → a small `StatusPill` kind=`warn`: "Recording storage almost full" +
  link to the recordings settings.
- `error` → small `StateBlock`/`StatusPill` kind=`danger`: stop-mode →
  "Recordings paused — storage full"; evict-mode-but-all-protected → "Can't free
  space — too many saved recordings." Links to the fix page.

Not a big slab — one calm line at the top of the dashboard, gone entirely when
`ok`. (Design-system: warn/danger kinds, lucide icon, never bare red text.)

## 7. API (routes/birds.py)

| method | path | purpose |
|---|---|---|
| GET  | `/birds/recordings/settings` | global rec settings |
| POST | `/birds/recordings/settings` | update cap / when-full (Edit-mode Save) |
| GET  | `/birds/recordings/status` | banner state + usage meter data |
| GET  | `/birds/recordings` | list (filters: `sci`, `location_id`, `pinned`, paginated) |
| GET  | `/birds/recordings/{id}/audio` | stream the clip (`audio/mpeg`, Range-capable) |
| POST | `/birds/recordings/{id}/pin` | `{pinned: bool}` |
| DELETE | `/birds/recordings/{id}` | delete one clip (confirm in UI) |
| DELETE | `/birds/recordings?sci=<sci>` | clear all clips for a bird |
| GET/POST | `/birds/species/{sci}/recording` | per-species `{record, keep_all}` |

All recording controls are **separate** from the existing trust-status controls
(`confirm/reject/mute/auto`). Rejecting a detection never deletes its clip;
clearing a clip never changes the detection's status. (Operator emphasized this.)

## 8. Frontend (design-system primitives only)

- **`Birds.tsx`** — the conditional banner (§6). Polls `/recordings/status` on
  the same cadence as the dashboard refresh.
- **`BirdSettings.tsx` → new "Recordings" section** —
  - master default (record new species off/on),
  - **storage cap** Input/`Stepper` + **when-full** `Segmented`
    (Evict oldest / Stop when full) → these write config, so **Edit-mode +
    sticky `ActionBar` Save**, not commit-on-change.
  - a **usage meter**: used / cap with the protected portion marked. Design
    system has no progress bar → add a small `Meter` primitive (`.ws-meter` in
    `components.css` + `src/ui`) rather than a one-off; it's reusable (budget app
    could use it too).
- **`BirdDetail.tsx` → "Recordings" subsection** (below the existing stats, kept
  visually distinct from confirm/reject):
  - toggle **Record this species** (`record`), toggle **Save all recordings**
    (`keep_all`) — commit-on-change (view-ish prefs, cheap),
  - list of this species' clips, each with a **play** control, a **pin** toggle
    ("Save this recording"), and a **delete** (ConfirmDialog naming the
    consequence),
  - **Clear all recordings for this bird** (ConfirmDialog).
- **Optional `/apps/birds/recordings` page** — global list + filters + the usage
  meter; this is the banner's `fix_path` target. (Could instead deep-link to the
  settings section; a dedicated page is cleaner for browsing/bulk delete.)
- **Inline play** in the live log / detection lists where a row has a clip — a
  small play button (reuse a tiny `AudioClip` component wrapping one shared
  `Audio` object so only one plays at a time).

## 9. Yard / Pi path (out of scope for v1, noted)

BirdNET-Go on the Brain box records its own clips (referenced by
`notes.clip_name`) under its own retention config (`brain/config.yaml`). That DB
is read-only from Workshop and the clips are on another machine. v1 does **not**
fold yard clips into this budget. Two clean follow-ups:
1. surface + play yard clips in the app (a proxy route to the Brain box),
2. optionally bring them under one unified budget + retention later.

The §1–§8 system governs only app-managed (browser-captured) clips.

## 10. Housekeeping

- **Reconcile**: a `recordings/reconcile` (or startup sweep) that re-sums file
  sizes vs DB and removes orphan files / dangling rows, so the meter can't drift
  from disk reality.
- Clip writes go to `~/birdnet/data/clips/`; ensure the dir exists; never add it
  to anything `publish.sh` reads.
- Keep encoding off the request hot path only if it becomes a problem — MP3 of a
  3 s clip is a few ms; fine inline for now.

## 11. Decisions (locked 2026-06-06)

1. **Audio format** — **MP3 64 kbps mono** (~24 KB/3 s). `libmp3lame`.
2. **Default cap** — **1 GB** (`rec_storage_mb = 1024`); ~40k clips, adjustable
   in-app.
3. **Per-species scope** — **global per species** for v1
   (`species_recording` keyed by `scientific_name`, no location column).
4. **Recordings UI home** — **dedicated `/apps/birds/recordings` page** (the
   banner's `fix_path`); per-bird clips also reachable from `BirdDetail`.
5. **Yard clips** — v1 = **browser path only**; surface/unify yard clips later
   (§9).
</content>
</invoke>
