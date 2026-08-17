// The Store policy tier, and how it reads on screen.
//
// This existed three times and disagreed with the backend in all three. The two
// pages each carried their own `tierKind` mapper (Stores.tsx, StoreDetail.tsx)
// and Stores.tsx additionally hardcoded the filter options. All of them listed
// `open | mediated | closed`, while `backend/services/policy.py:62` has always
// declared `open | gated | mediated`. So `gated` was a real tier no filter could
// select, and `closed` was a phantom that matched nothing. Measured 2026-08-17.
//
// The vocabulary itself is NOT restated here: `GET /stores` now returns the
// backend's own `TIERS` alongside `types`, and the filter builds itself from
// that list exactly the way the type filter already did. What lives here is only
// the mapping from a tier to how it is drawn, which is a frontend concern.

import type { StatusKind } from "@workshop/ui";

/**
 * A tier says how a request for this store is answered, not how something
 * turned out. `open` needs nothing, `gated` needs a grant the operator minted,
 * `mediated` sends each request to an adjudicator. The last two are both
 * "restricted", differing in mechanism, so they share a kind and the label
 * beside the dot says which one it is. Nothing here is `danger`: a mediated
 * store is doing its job, not failing.
 */
export function tierKind(tier?: string | null): StatusKind {
  if (tier === "open") return "ok";
  if (tier === "gated" || tier === "mediated") return "warn";
  return "stopped";
}
