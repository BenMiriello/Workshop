<!-- author: self; chat: 95f7fd7fe8f6413dbcea1b7e80d47979 -->
The chat's `threadStore.ts` is a good pure reducer, but thread identity, branch tracking (`branchRef`), socket attachment, hydration and pending sends live in ~37 `useState` + 32 `useRef` BESIDE it across four modules, written by 25 independent async callbacks with no ordering invariant. The render is a pure function of a tuple whose members update independently, so every 'chat flicker/jump' bug has been one moment where that tuple disagreed with itself.

This is why five passes 'fixed' the new-chat jump and it returned: each tuned one member of the tuple.

Measured 2026-08-25 (commit 2277cc60): three such defects at once — `branchRef` not re-seeded on a cache-MISS thread change (so a new thread looked like a branch CHANGE, firing on every new-chat send); a `reset` dispatched without its fill, leaving the store empty for a round trip (and the reducer's reset drops the pending send); and `turnPhase` deriving 'detached' from `!attached`, which is also true in the 60-140ms window before the socket opens.

PROBING LESSON: an rAF probe samples once per frame and AVERAGES THESE AWAY — six runs showed nothing. A MutationObserver on `document.body` caught all of them, including a state lasting 17ms. Use MutationObserver for any 'it flashes' report.

Still live, not a bug but waste: a new-chat send issues 3-4 identical `events?after=0&tail=30` reads within ~150ms, because every caller racing the switch's reset commit computes `after = 0`. A naive single-flight fix is UNSAFE (deduping a zero-cursor read can drop a caller's own first message) and was reverted. Fix is structural; see the audit doc.