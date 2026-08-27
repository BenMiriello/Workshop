// EVERY QUERY KEY AND FETCHER FOR THE BIRDS APP, IN ONE PLACE.
//
// WHY THIS FILE EXISTS, AND WHY IT IS NOT IN `lib/queryKeys.ts`.
// The platform's key registry is `frontend/src/lib/queryKeys.ts`, and this
// file follows its pattern exactly: a nested object of factory functions that
// return `as const` tuples, with a comment on any slot whose reason is not
// obvious. What it may NOT do is live there. A platform source file may not
// name a specific app (CLAUDE.md, gated by `scripts/check_no_app_names.py`),
// so an app declares its own keys beside its own fetchers. The registry says
// so in its own header: "APPS DO NOT APPEAR HERE."
//
// The name `queries.ts` is load-bearing. `scripts/check_frontend_state.py`
// counts `queryKey: [` literals written outside a file named `queryKeys.ts`
// or `queries.ts` and fails on a new one, so a key spelled at a call site is
// a build failure and a key spelled here is the only way to have one.
//
// WHAT IT IS FOR. An audit of this app found every endpoint below has two or
// more independent readers, each with its own `usePoll` and its own copy of
// the response in its own `useState`. `/locations` alone is read by
// `scope.tsx`, `Locations.tsx`, `Devices.tsx` and `LocationDetail.tsx`, at
// three different cadences, spelled three different ways (`/locations?_=1`,
// the same URL again, and bare `/locations`). Two readers share a cache entry,
// an in-flight request and an invalidation only if they spell the key
// identically, so the keys are constructed here, by a function per fact, and
// a misspelling becomes a missing export -- a compile error, which is the only
// kind that stops this recurring.
//
// THE RULE, AND IT IS NOT NEGOTIABLE: EVERY PARAMETER THAT APPEARS IN THE URL
// MUST APPEAR IN THE KEY. The audit found several reads whose parameters lived
// in closure state instead: a slow response for one location or one status
// lands after a newer one and overwrites it, because nothing about the two
// requests was distinguishable. Params in the key make that unrepresentable --
// a different URL is a different cache entry, resolved independently, and the
// stale one has nowhere to land. That is also why a param that CANNOT appear
// in a URL gets no slot: an unused slot splits one fact into two cache entries
// that no invalidation keeps in step, which is the same bug pointing the other
// way.
//
// `_=1` IS GONE ON PURPOSE. Four call sites carry a `_=1` query param as a
// cache-buster. It is a CONSTANT, so it busts nothing; it only made the URL
// differ from the bare spelling of the same read, which is how `/locations`
// came to have three spellings. Freshness is the query layer's job now
// (`lib/queryClient.ts` defaults), so the param does not survive the move.
//
// `nb` IS `include_nonbird`. `scope.tsx` computes `nb` from the app's
// show-non-bird display preference via `nonBirdQuery()` in `settings.ts`, and
// it is either `"&include_nonbird=true"` or the empty string. It changes what
// the backend counts, so it changes the URL, so it is in the key of every
// read that takes it -- as a BOOLEAN, since a raw suffix string would put
// URL-encoding decisions in the cache key.
//
// TYPES ARE STILL AT THE CALL SITES, and that is the backend's fault rather
// than a shortcut. `frontend/CLAUDE.md` says to prefer `api/schema.gen.ts`
// over a hand-written response type, but not one route under `/api/birds`
// declares `response_model=`, so every generated body here is `unknown` (see
// `scripts/check_response_model.py` for the debt). Rather than hand-write a
// duplicate contract, the fetchers are generic in the payload: a call site
// passes the shape it already declares. When a route gains a
// `response_model=`, replace that call site's argument with
// `components["schemas"][...]` and delete its local type.
//
// Nothing here may import `ui.tsx`. `api.ts` was split out of it precisely
// because `ui.tsx` pulls in recharts, and the Birds scope provider is loaded
// eagerly by the app registry -- importing `ui.tsx` from a module on that path
// puts the whole charting library in the main chunk for every visitor.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { API, apost, jget } from "./api";

/** A key, once built. Readonly so a caller cannot mutate a shared tuple. */
export type BirdsQueryKey = readonly unknown[];

/** The show-non-bird display filter, as it reaches a key and a URL. */
export type NonBird = { includeNonBird: boolean };

/**
 * A detection's trust state (`confirmed` / `suggested` / `rejected` / `all`,
 * plus whatever the review page adds). Typed as a string because the backend
 * takes it as a free `status` query param, not an enum.
 */
export type BirdStatus = string;

type Param = string | number | boolean | null | undefined;

/**
 * Query string from params, skipping the ones that must not appear. `false`
 * and `""` are dropped because that is exactly what the call sites do: an
 * absent `include_nonbird` and an absent `device` are how "off" and "no device
 * filter" are spelled on the wire. `0` is kept -- it is a real limit.
 */
function qs(params: Record<string, Param>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const birdsKeys = {
  /** Everything under the app. The prefix an invalidation uses to reach it all. */
  all: () => ["birds"] as const,

  /**
   * `GET /api/birds/locations` -- the LIVE list. `"live"` is a discriminant,
   * not decoration: the same route also serves the archive (`?archived=1`),
   * which is a DIFFERENT list rather than a wider one, so it gets its own
   * entry. Both sit under the `["birds", "locations"]` prefix, so one
   * invalidation after a create/merge/delete reaches every copy of either.
   * Same shape `lib/queryKeys.ts` uses for chat threads live-vs-archived.
   */
  locations: (p: NonBird) => ["birds", "locations", "live", p.includeNonBird] as const,
  /** `GET /api/birds/locations?archived=1`. Its own list; see above. */
  locationsArchived: (p: NonBird) => ["birds", "locations", "archived", p.includeNonBird] as const,
  /** The prefix over BOTH lists and both filters, for invalidating after a write. */
  locationsAll: () => ["birds", "locations"] as const,

  /** `GET /api/birds/devices` -- the LIVE list. Takes no `include_nonbird`. */
  devices: () => ["birds", "devices", "live"] as const,
  /** `GET /api/birds/devices?archived=1`. Its own list, same reason as locations. */
  devicesArchived: () => ["birds", "devices", "archived"] as const,
  /** The prefix over both device lists. */
  devicesAll: () => ["birds", "devices"] as const,

  /**
   * `GET /api/birds/summary`. The scope provider reads it with the filter off
   * (it only asks "is there a yard feed at all"); the dashboard reads it with
   * the operator's filter applied. Two URLs, two facts, two keys.
   */
  summary: (p: NonBird) => ["birds", "summary", p.includeNonBird] as const,

  /**
   * `GET /api/birds/species` -- the unscoped species list.
   *
   * NO `status` SLOT, deliberately. The status-carrying read is
   * `locationSpecies` below (`/location/species?...&status=`); this route
   * accepts only `include_nonbird`, so a status slot here would be a key
   * distinction with no URL behind it, splitting one fact across cache
   * entries nothing keeps in step.
   */
  species: (p: NonBird) => ["birds", "species", p.includeNonBird] as const,

  /**
   * `GET /api/birds/detections`. The limit is IN the key: the dashboard asks
   * for 1000 and the aviary for 80, and a cache entry that answered one must
   * not be handed to the other.
   */
  detections: (p: NonBird & { limit: number }) =>
    ["birds", "detections", p.limit, p.includeNonBird] as const,

  /**
   * `GET /api/birds/live`. `locationId` is `null` for the unscoped feed, which
   * is an explicit slot rather than an absent one so "everywhere" and "one
   * place" are the same shape and cannot collide.
   */
  live: (p: { locationId: number | null }) => ["birds", "live", p.locationId] as const,

  /** `GET /api/birds/home` -- which location is home. */
  home: () => ["birds", "home"] as const,

  /**
   * `GET /api/birds/location/summary`. Every one of these params changes what
   * the numbers count, so every one is in the key. This is the read the audit
   * caught racing: the location id was closure state, so switching places
   * twice quickly could leave the first place's totals on screen.
   */
  locationSummary: (p: NonBird & { locationId: number; device: string }) =>
    ["birds", "location", p.locationId, "summary", p.includeNonBird, p.device] as const,

  /**
   * `GET /api/birds/location/species`. Same params plus `status`, which two
   * callers set differently (the dashboard pins `confirmed`, the review page
   * moves it between confirmed/suggested/rejected). Status in the key is what
   * stops a slow `suggested` response from painting over a fresh `confirmed`.
   */
  locationSpecies: (p: NonBird & { locationId: number; device: string; status: BirdStatus }) =>
    ["birds", "location", p.locationId, "species", p.status, p.includeNonBird, p.device] as const,

  /** `GET /api/birds/location/activity` -- the hourly/daily bars for one place. */
  locationActivity: (p: NonBird & { locationId: number; device: string }) =>
    ["birds", "location", p.locationId, "activity", p.includeNonBird, p.device] as const,

  /** The prefix over every read scoped to one location, for invalidating after a write. */
  locationAll: (locationId: number) => ["birds", "location", locationId] as const,

  /** `GET /api/birds/activity/hourly`. `days` is in the key; it is the window. */
  activityHourly: (p: NonBird & { days: number }) =>
    ["birds", "activity", "hourly", p.days, p.includeNonBird] as const,
  /** `GET /api/birds/activity/daily`. */
  activityDaily: (p: NonBird & { days: number }) =>
    ["birds", "activity", "daily", p.days, p.includeNonBird] as const,

  /**
   * `GET /api/birds/bird` -- one species, under the full scope. ALL FIVE
   * params are in the key. They are the scope bar (location, device, time
   * window) plus the page-local status filter, and every one of them changes
   * the response; the page previously held them in a `useCallback` closure and
   * re-fetched on change, which is the exact shape that lets an older window's
   * answer land last.
   */
  birdDetail: (p: {
    sci: string;
    status: BirdStatus;
    locationId: number | null;
    device: string;
    days: number;
  }) => ["birds", "bird", p.sci, p.status, p.locationId, p.device, p.days] as const,

  /** `GET /api/birds/recordings/settings` -- read by three pages, written by two. */
  recordingsSettings: () => ["birds", "recordings", "settings"] as const,
  /**
   * `GET /api/birds/recordings`. `sci` is `null` for the whole list and a name
   * for one species' clips; `limit` differs per caller (300 vs 200). Both in
   * the key.
   */
  recordings: (p: { sci: string | null; limit: number }) =>
    ["birds", "recordings", "list", p.sci, p.limit] as const,
  /** `GET /api/birds/recordings/status` -- the recorder's capture state. */
  recordingsStatus: () => ["birds", "recordings", "status"] as const,
  /** The prefix over every recordings read, for invalidating after a pin/delete. */
  recordingsAll: () => ["birds", "recordings"] as const,

  /**
   * `GET /api/birds/atlas`. `species` is `null` in the global scope. The scope
   * discriminant stays even though the species slot implies it: `scope` is
   * what the URL carries, and a key that has to be inferred from a null is a
   * key someone will spell the other way.
   */
  atlas: (p: { scope: "global" | "species"; species: string | null }) =>
    ["birds", "atlas", "points", p.scope, p.species] as const,
  /** `GET /api/birds/atlas/status`. Read by the Atlas page and by Settings. */
  atlasStatus: () => ["birds", "atlas", "status"] as const,
  /** `GET /api/birds/atlas/species` -- the species menu the Atlas scope picker offers. */
  atlasSpecies: () => ["birds", "atlas", "species"] as const,
  /** `GET /api/birds/atlas/insights`. `days` is the window and is in the key. */
  atlasInsights: (p: { days: number }) => ["birds", "atlas", "insights", p.days] as const,
  /** `GET /api/birds/atlas/settings`. */
  atlasSettings: () => ["birds", "atlas", "settings"] as const,
  /** `GET /api/birds/atlas/cluster/{id}/clips` -- expanded cluster's example clips. */
  atlasClusterClips: (clusterId: number) => ["birds", "atlas", "cluster", clusterId, "clips"] as const,
  /** The prefix over everything Atlas, for invalidating after a rebuild. */
  atlasAll: () => ["birds", "atlas"] as const,

  /** `GET /api/birds/analyze/health` -- the recorder's analyzer heartbeat. */
  analyzeHealth: () => ["birds", "analyze", "health"] as const,
  /** `GET /api/birds/analyze/settings`. */
  analyzeSettings: () => ["birds", "analyze", "settings"] as const,
} as const;

// The fetchers. One per fact, taking the SAME params object as its key, so a
// call site cannot key a request by one set of params and send another. Each
// is generic in the payload because no `/api/birds` route declares a
// `response_model=` yet (see the header); pass the shape the call site already
// declares.

export const fetchLocations = <T>(p: NonBird): Promise<T> =>
  jget<T>(`/locations${qs({ include_nonbird: p.includeNonBird })}`);

export const fetchLocationsArchived = <T>(p: NonBird): Promise<T> =>
  jget<T>(`/locations${qs({ archived: 1, include_nonbird: p.includeNonBird })}`);

export const fetchDevices = <T>(): Promise<T> => jget<T>("/devices");

export const fetchDevicesArchived = <T>(): Promise<T> => jget<T>(`/devices${qs({ archived: 1 })}`);

export const fetchSummary = <T>(p: NonBird): Promise<T> =>
  jget<T>(`/summary${qs({ include_nonbird: p.includeNonBird })}`);

export const fetchSpecies = <T>(p: NonBird): Promise<T> =>
  jget<T>(`/species${qs({ include_nonbird: p.includeNonBird })}`);

export const fetchDetections = <T>(p: NonBird & { limit: number }): Promise<T> =>
  jget<T>(`/detections${qs({ limit: p.limit, include_nonbird: p.includeNonBird })}`);

export const fetchLive = <T>(p: { locationId: number | null }): Promise<T> =>
  jget<T>(`/live${qs({ location_id: p.locationId })}`);

export const fetchHome = <T>(): Promise<T> => jget<T>("/home");

export const fetchLocationSummary = <T>(
  p: NonBird & { locationId: number; device: string },
): Promise<T> =>
  jget<T>(`/location/summary${qs({
    location_id: p.locationId,
    include_nonbird: p.includeNonBird,
    device: p.device,
  })}`);

export const fetchLocationSpecies = <T>(
  p: NonBird & { locationId: number; device: string; status: BirdStatus },
): Promise<T> =>
  jget<T>(`/location/species${qs({
    location_id: p.locationId,
    include_nonbird: p.includeNonBird,
    device: p.device,
    status: p.status,
  })}`);

export const fetchLocationActivity = <T>(
  p: NonBird & { locationId: number; device: string },
): Promise<T> =>
  jget<T>(`/location/activity${qs({
    location_id: p.locationId,
    include_nonbird: p.includeNonBird,
    device: p.device,
  })}`);

export const fetchActivityHourly = <T>(p: NonBird & { days: number }): Promise<T> =>
  jget<T>(`/activity/hourly${qs({ days: p.days, include_nonbird: p.includeNonBird })}`);

export const fetchActivityDaily = <T>(p: NonBird & { days: number }): Promise<T> =>
  jget<T>(`/activity/daily${qs({ days: p.days, include_nonbird: p.includeNonBird })}`);

export const fetchBirdDetail = <T>(p: {
  sci: string;
  status: BirdStatus;
  locationId: number | null;
  device: string;
  days: number;
}): Promise<T> =>
  jget<T>(`/bird${qs({
    sci: p.sci,
    status: p.status,
    location_id: p.locationId,
    device: p.device,
    days: p.days,
  })}`);

export const fetchRecordingsSettings = <T>(): Promise<T> => jget<T>("/recordings/settings");

export const fetchRecordings = <T>(p: { sci: string | null; limit: number }): Promise<T> =>
  jget<T>(`/recordings${qs({ sci: p.sci, limit: p.limit })}`);

export const fetchRecordingsStatus = <T>(): Promise<T> => jget<T>("/recordings/status");

export const fetchAtlas = <T>(p: { scope: "global" | "species"; species: string | null }): Promise<T> =>
  jget<T>(`/atlas${qs({ scope: p.scope, species: p.species })}`);

export const fetchAtlasStatus = <T>(): Promise<T> => jget<T>("/atlas/status");

export const fetchAtlasSpecies = <T>(): Promise<T> => jget<T>("/atlas/species");

export const fetchAtlasInsights = <T>(p: { days: number }): Promise<T> =>
  jget<T>(`/atlas/insights${qs({ days: p.days })}`);

export const fetchAtlasSettings = <T>(): Promise<T> => jget<T>("/atlas/settings");

export const fetchAtlasClusterClips = <T>(clusterId: number): Promise<T> =>
  jget<T>(`/atlas/cluster/${clusterId}/clips`);

export const fetchAnalyzeHealth = <T>(): Promise<T> => jget<T>("/analyze/health");

export const fetchAnalyzeSettings = <T>(): Promise<T> => jget<T>("/analyze/settings");

// ---------------------------------------------------------------------------
// THE WRITES, BESIDE THE KEYS THEY INVALIDATE.
//
// A write belongs here for the same reason a key does. `POST /location` was
// spelled twice, byte for byte, in the locations list and in the location
// detail page; `POST /device` and both restores were spelled once each in the
// page that happened to own the button. Each was followed by
// `setTimeout(reload, 150)` -- a guess at how long the server needs, standing
// in for an invalidation, which is now what the call sites do instead.
//
// EVERY ONE THROWS ON A NON-2xx (`apost` and `adel` both check `r.ok`). The
// call sites used `.catch(() => {})` or `.finally(...)`, so a rejected save
// closed the editor exactly as a successful one did. A write that throws is
// what lets the page say a save failed.

/** DELETE, with the same "throw on a non-2xx" contract `jget`/`apost` have. */
const adel = async (path: string): Promise<void> => {
  const r = await fetch(`${API}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(String(r.status));
};

export const saveLocation = (p: {
  id: number; name: string; lat: number; lon: number; address: string;
}): Promise<unknown> =>
  apost("/location", {
    location_id: p.id, name: p.name, lat: p.lat, lon: p.lon, address: p.address,
  });

export const removeLocation = (id: number): Promise<void> =>
  adel(`/location?location_id=${id}`);

export const restoreLocation = (id: number): Promise<unknown> =>
  apost("/location/restore", { location_id: id });

export const mergeLocations = (p: { src: number; dst: number }): Promise<unknown> =>
  apost("/location/merge", { src: p.src, dst: p.dst });

/**
 * `POST /api/birds/device`. `fixedLocationId` is sent ONLY for a fixed device,
 * because the route reads an absent `fixed_location_id` as "leave the pin
 * alone" and an empty one as "clear it" -- sending it unconditionally would
 * silently unpin an adaptive device's remembered location.
 */
export const saveDevice = (p: {
  token: string; name: string; mode: string; fixedLocationId: string | null; shared: boolean;
}): Promise<unknown> =>
  apost("/device", {
    token: p.token,
    name: p.name,
    mode: p.mode,
    shared: p.shared,
    ...(p.fixedLocationId === null ? {} : { fixed_location_id: p.fixedLocationId || "0" }),
  });

export const removeDevice = (token: string): Promise<void> =>
  adel(`/device?token=${encodeURIComponent(token)}`);

export const restoreDevice = (token: string): Promise<unknown> =>
  apost("/device/restore", { token });

/** `GET /api/birds/home`, and what `POST /api/birds/home` answers with. */
export type HomeRead = { home: string | null };

/**
 * Set (or clear, with `""`) the home location.
 *
 * OPTIMISTIC, AND THE OPTIMISM HAS TO BE CANCELLED-IN, not just painted. The
 * star used to be a `setHome(...)` beside a fire-and-forget POST while a
 * separate 8s/12s poll re-read the same fact. A read already in flight when
 * the star was tapped resolved AFTERWARDS carrying the PRE-write value, so the
 * star reverted and then flipped back on the following tick. `cancelQueries`
 * is the half that fixes it: the in-flight read is dropped before the cache is
 * patched, so there is nothing left to land on top of the new value.
 *
 * One fact, one entry: `birdsKeys.home()`. `GET /locations` also carries a
 * `home` field, and reading the star off THAT payload is how a list refetch
 * could revert it too. No page reads it from there any more; the lists are
 * invalidated on the way out so anything that still derives from them catches
 * up.
 */
export function useSetHome() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (source: string) => apost<HomeRead>("/home", { source }),
    onMutate: async (source: string) => {
      await qc.cancelQueries({ queryKey: birdsKeys.home() });
      const prev = qc.getQueryData<HomeRead>(birdsKeys.home());
      qc.setQueryData<HomeRead>(birdsKeys.home(), { home: source });
      return { prev };
    },
    // The POST answers with the value the server settled on, so success needs
    // no confirming read.
    onSuccess: (data) => { qc.setQueryData<HomeRead>(birdsKeys.home(), { home: data.home }); },
    onError: (_err, _source, ctx) => {
      qc.setQueryData<HomeRead>(birdsKeys.home(), ctx?.prev);
      void qc.invalidateQueries({ queryKey: birdsKeys.home() });
    },
    onSettled: () => { void qc.invalidateQueries({ queryKey: birdsKeys.locationsAll() }); },
  });
}
