# Birds — presence vs. count, and the "vocalization event" reframing (WORK IN PROGRESS)

**Status: WIP / not scheduled.** Captured 2026-06-09 while building spectrogram detection-marker
localization. This is a separate, larger effort — do NOT fold it into the marker work. It records
the idea so it isn't lost.

## The problem

Today everything counts **individual detections**. A detection is one 3-second analysis window in
which BirdNET scored a species above the floor. That number conflates two very different things:

- **Presence** — *was this species here?* (binary-ish, what most people actually care about.)
- **Talkativeness / activity** — *how much did it vocalize?* (a loud, chatty bird racks up dozens of
  detections; a quiet-but-present one barely registers.)

So raw counts overstate noisy birds and understate quiet residents, and "37 detections" tells you
almost nothing about whether 37 means "one bird sang for a minute" or "the yard was full of them."

## Two layers to separate

### 1. Vocalization events, not samples
Collapse a burst of overlapping/contiguous detections of the same species into one **vocalization
event** with an **intensity** (how many calls/how long). "5 chirps in 10s" becomes *one event,
intensity 5* — not five detections. This is the same burst-grouping the marker-localization work
already needs (per-species confidence runs with a gap threshold), so the plumbing can be shared.

Benefits ripple everywhere: counts, charts, recordings (one clip per event, not per overlapping
window), and the activity histograms all become "events" instead of "samples."

### 2. Time-binned presence/intensity bands
Bucket events into time bins (selectable **5 / 15 min**, maybe hourly for long views). Per bin per
species render **presence** *distinctly from* **intensity**:

- **Presence** — detected at all in the bin → a solid, full-strength mark. A quiet bird that called
  once still reads as *present*.
- **Intensity** — event count / total vocalization time in the bin → opacity, height, or a small
  count, layered *on top of* presence so it never erases it.

A chatty bird looks more intense without burying everyone else; a one-call resident still shows up.

### Drill-down preserved
The individual chirps stay visible underneath — zoom/expand a bin to get back to the raw detections
and the spectrogram pins (the marker work). Nothing is thrown away; the bands are a *lens*, not a
replacement.

## Open questions / decisions to make later
- Bin sizes + whether they auto-scale with the visible time range.
- Intensity metric: event count vs. summed vocalization seconds vs. peak concurrency estimate.
- Can we ever estimate **number of individuals** (not just calls)? Probably not from BirdNET alone;
  would need spatial mics or song-type clustering. Treat as research, not a near-term feature.
- How this interacts with the trust model (suggested/confirmed/rejected) and the non-bird filter.
- Whether "presence" should require ≥N events or ≥some confidence to avoid a single false hit
  painting a bin as present.

## Why it's deferred
The marker-localization work (putting the ID card on the actual chirp, disambiguating simultaneous
birds) is big and tricky on its own. This reframing is orthogonal and at least as large. Build the
marker work first; come back to this as its own pass. The shared piece to keep clean is the
**per-species burst/event grouping** — design it in the marker work so this can reuse it.
