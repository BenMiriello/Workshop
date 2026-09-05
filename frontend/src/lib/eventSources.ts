// Which event sources a trigger may subscribe to, asked of the deployment.
//
// This used to be two hardcoded arrays -- one in `AutonomyPanel.tsx`, one in
// `agentTriggers.ts` -- that had already drifted from each other and from the
// backend. Both listed specific apps' integrations, which made two platform
// files name specific apps: the exact violation `check_no_app_names.py` exists
// to stop, in a shape it could not see because the names were an integration's
// rather than an app id's.
//
// The backend already unions its own sources with `AppSpec.event_sources` over
// the INSTALLED apps, so the honest list is one fetch away and changes when an
// app is installed. `GET /api/events/sources` is that fetch.
import { useQuery } from "@tanstack/react-query";

import { httpFail } from "./http";
import { queryKeys } from "./queryKeys";
import type { components } from "../api/schema.gen";

/**
 * What to offer before the answer arrives, and if it never does.
 *
 * "custom" alone: it is the one source that is always accepted, it names no
 * app and no vendor, and a checkbox list that briefly shows a stale app's
 * integration would be worse than one that briefly shows less.
 */
export const FALLBACK_EVENT_SOURCES: readonly string[] = ["custom"];

export function useEventSources(): readonly string[] {
  const q = useQuery({
    queryKey: queryKeys.events.sources(),
    queryFn: async (): Promise<string[]> => {
      const r = await fetch("/api/events/sources");
      if (!r.ok) throw await httpFail(r);
      return ((await r.json()) as components["schemas"]["EventSources"]).sources ?? [];
    },
    staleTime: 5 * 60 * 1000,
  });
  return q.data && q.data.length ? q.data : FALLBACK_EVENT_SOURCES;
}
