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
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

  /**
   * The prefix over every read of one bird's page, ACROSS species and filters.
   * `/bird` answers with `save_mode` and `clip_count` in the same payload as the
   * detections, so a write to a bird's recordings or to one detection's status
   * has to reach this whole family; a page that only knows the species and
   * filters it is currently showing cannot name the entries it invalidated.
   */
  birdAll: () => ["birds", "bird"] as const,

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

  /**
   * `GET /api/birds/now` -- the forecast card for ONE place. The location is in
   * the key rather than in a closure: the card used to hold the response in
   * `useState` beside a five-minute `usePoll`, and because `usePoll` reads its
   * callback through a ref, changing places did not re-read at all -- the new
   * place's name sat above the old place's forecast until the next tick, up to
   * five minutes later.
   */
  now: (p: { locationId: number }) => ["birds", "now", p.locationId] as const,

  /** `GET /api/birds/forecast/week`. `days` is the window and is in the key. */
  forecastWeek: (p: { locationId: number; days: number }) =>
    ["birds", "forecast", "week", p.locationId, p.days] as const,

  /**
   * `GET /api/birds/species-events` -- the individual detections behind one
   * species row, revealed when the row is expanded. `status` and `locationId`
   * are both on the wire and both in the key; `locationId` is `null` for the
   * unscoped read, the same explicit-slot shape `live` uses.
   */
  speciesEvents: (p: { sci: string; status: BirdStatus; locationId: number | null }) =>
    ["birds", "species-events", p.sci, p.status, p.locationId] as const,
} as const;

/**
 * `/api/environment/*` -- sunrise/sunset and hourly weather for a place and a
 * day, which the activity chart overlays on its times-of-day view.
 *
 * OUTSIDE THE `birds` PREFIX ON PURPOSE. These are platform reads that the
 * Birds app happens to be the only current caller of, and they are not
 * downstream of anything Birds writes: confirming a detection or merging a
 * location cannot change when the sun rose. Under `["birds", ...]` every
 * invalidation of the app prefix would re-read them for nothing.
 *
 * The DATE is the key, not the `start`/`end` timestamps the conditions route
 * takes. Those are a pure function of the date (its local midnight and its last
 * second), so keying by them would spell one fact two ways whenever the string
 * was built a moment apart.
 */
export const envKeys = {
  astro: (p: { locationId: number; date: string }) =>
    ["environment", "astro", p.locationId, p.date] as const,
  conditions: (p: { locationId: number; date: string }) =>
    ["environment", "conditions", p.locationId, p.date] as const,
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

export const fetchNow = <T>(p: { locationId: number }): Promise<T> =>
  jget<T>(`/now${qs({ location_id: p.locationId })}`);

export const fetchForecastWeek = <T>(p: { locationId: number; days: number }): Promise<T> =>
  jget<T>(`/forecast/week${qs({ location_id: p.locationId, days: p.days })}`);

export const fetchSpeciesEvents = <T>(
  p: { sci: string; status: BirdStatus; locationId: number | null },
): Promise<T> =>
  jget<T>(`/species-events${qs({ sci: p.sci, status: p.status, location_id: p.locationId })}`);

/**
 * The environment reads do not go through `jget`, which prefixes `/api/birds`.
 * Same contract though: a non-2xx THROWS. The chart used to read `r.ok ? json :
 * null` and merge the null, so a failed overlay was indistinguishable from a day
 * with no weather recorded.
 */
async function envGet<T>(path: string): Promise<T> {
  const r = await fetch(`/api/environment${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

export const fetchAstro = <T>(p: { locationId: number; date: string }): Promise<T> =>
  envGet<T>(`/astro${qs({ location_id: p.locationId, date: p.date })}`);

/** The day's hourly conditions. `start`/`end` are derived here, from the date. */
export const fetchConditions = <T>(p: { locationId: number; date: string }): Promise<T> =>
  envGet<T>(`/conditions${qs({
    location_id: p.locationId,
    start: new Date(`${p.date}T00:00:00`).toISOString(),
    end: new Date(`${p.date}T23:59:59`).toISOString(),
  })}`);

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

// ---------------------------------------------------------------------------
// THE SESSION, AND WHY IT IS NOT UNDER THE `birds` PREFIX.
//
// `GET /api/auth/me` is a platform route, not an app one, and nothing Birds
// writes can change who is signed in -- so an invalidation of `birdsKeys.all()`
// after a merge or a confirm must not re-read it. Same reasoning as `envKeys`
// above, and the same shape.
//
// ONE FACT, ONE ENTRY, and that is the point of it being here at all.
// `BirdSettings.tsx` read this endpoint TWICE in one file: once in the account
// card and once in `useIsAdmin`, each with its own `useState` and its own
// `useEffect`. Two requests for one answer, which could disagree -- and they
// disagreed in the direction that matters, because a failure meant "no account
// card" to the first reader and "you are the admin" to the second.

/**
 * The signed-in user, as the two deployments spell it.
 *
 * The standalone app answers `{user: {...}}` (`apps/birds/springer/app.py`).
 * The Workshop backend answers the user object bare, so `user` is absent there
 * and every caller reads that as "no session", which is the behaviour this app
 * wants on the tailnet: no account card, and the operator is the admin.
 */
export type BirdsSession = { user?: { email: string; name?: string | null; role?: string } | null };

/**
 * Resolve the session. A non-2xx is an ANSWER (`401`/`404` both mean "no
 * session here"), so it resolves to `{}` rather than throwing; only a network
 * failure rejects, and the call site turns that into "treat me as the admin",
 * exactly as the two hand-rolled reads did.
 */
export const fetchSession = async (): Promise<BirdsSession> => {
  const r = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (!r.ok) return {};
  return (await r.json()) as BirdsSession;
};

export const authKeys = {
  session: () => ["auth", "session"] as const,
} as const;

// ---------------------------------------------------------------------------
// THE SETTINGS WRITES.
//
// All four routes take a FORM body and answer with the WHOLE settings object
// they just wrote (`backend/routes/birds.py`), so a call site never has to
// guess what the server settled on -- it writes the answer straight into the
// cache entry the read uses. Each was previously a bare `fetch(...).then(r =>
// r.json()).catch(() => {})` inside the component that owned the control, so a
// REJECTED save left the toggle sitting in the position the operator had just
// moved it to.

/** `GET /api/birds/atlas/settings` and `/recordings/settings` answer shape. */
export type SettingsRead<T> = { settings: T };

export const saveRecordingsSettings = <T>(p: { recSave: boolean }): Promise<SettingsRead<T>> =>
  apost<SettingsRead<T>>("/recordings/settings", { rec_save: p.recSave });

export const saveAtlasSettings = <T>(p: { clipSave: boolean }): Promise<SettingsRead<T>> =>
  apost<SettingsRead<T>>("/atlas/settings", { atlas_clip_save: p.clipSave });

export const saveAnalyzeSettings = <T>(p: {
  minConf: number; sfThresh: number; newSpeciesHits: number;
}): Promise<SettingsRead<T>> =>
  apost<SettingsRead<T>>("/analyze/settings", {
    min_conf: p.minConf, sf_thresh: p.sfThresh, new_species_hits: p.newSpeciesHits,
  });

/**
 * Kick off an Atlas rebuild. Returns as soon as the batch job is DETACHED, not
 * when it finishes: progress is observable only through `atlas/status`, whose
 * `version` bumps when the new projection lands. The call site watches for that
 * bump; nothing here can wait for it.
 */
export const rebuildAtlas = (): Promise<{ started: boolean; error?: string }> =>
  apost<{ started: boolean; error?: string }>("/atlas/rebuild", {});

/** Re-apply the range filter to already-stored detections. Reversible. */
export const recomputeDetections = (): Promise<{ hidden?: number; species_hidden?: unknown[] }> =>
  apost<{ hidden?: number; species_hidden?: unknown[] }>("/analyze/recompute", {});

/**
 * Name (or, with `""`, un-name) an Atlas call cluster. An empty label sends NO
 * `label` field at all, because the route's `Form(None)` reads an absent field
 * as "clear it" and an empty string as a label of `""`.
 */
export const saveClusterLabel = (p: { clusterId: number; label: string }): Promise<unknown> =>
  apost(`/atlas/cluster/${p.clusterId}/label`, p.label ? { label: p.label } : {});

// ---------------------------------------------------------------------------
// RECORDINGS: ONE SHAPE, AND THE WRITES THAT USED TO BE A WINDOW EVENT.
//
// Every write below was a bare `fetch(...).then(() => { load(); window
// .dispatchEvent(new CustomEvent("bird-settings-change")) })` in whichever page
// owned the button. The `load()` re-read that page and nothing else; the event
// re-read whoever happened to be listening. That is `invalidateQueries` spelled
// by hand, minus the dedup, minus the in-flight cancellation, and minus every
// reader that had not personally subscribed. Two pages spelled the pin and the
// delete separately, byte for byte.
//
// None of them checked `r.ok`, so a rejected write closed the dialog and moved
// the control exactly as an accepted one did. `apost` and `adel` both throw, so
// the mutation's `isError` is now what the page reads to say a write failed.

/**
 * The recording budget, as `GET /recordings/settings` returns it
 * (`birds_analyze.rec_settings()`).
 *
 * ONE SHAPE FOR THREE READERS. The recordings page declared
 * `{rec_storage_mb, rec_when_full}`, the bird page declared `{rec_save}`, and
 * the settings page declares its own again -- three partial hand-written types
 * for one route, kept in step by a window event. They share a cache entry now,
 * so they have to share a type or the entry means a different thing to each
 * reader that touches it.
 */
export type RecordingsSettings = {
  rec_storage_mb: number;
  rec_when_full: "evict_old" | "stop";
  rec_save: boolean;
  rec_warn_ratio: number;
};

/** A per-species recording rule: follow the app default, keep everything, keep nothing. */
export type SpeciesSaveMode = "default" | "keep" | "never";

/** A detection's trust state, as the re-classify buttons set it. */
export type DetectionStatus = "confirmed" | "suggested" | "rejected";

/**
 * `POST /recordings/settings` with the STORAGE BUDGET only.
 *
 * Deliberately separate from `saveRecordingsSettings` above, which writes
 * `rec_save`. The route is a PATCH: `Form(None)` means an absent field is left
 * alone, so a writer must send only the fields it owns. One helper sending all
 * three would let the recordings page's Save quietly write back whatever
 * `rec_save` its cached copy happened to hold, undoing a toggle made on the
 * settings page.
 */
export const saveRecordingsBudget = (p: {
  storageMb: number; whenFull: "evict_old" | "stop";
}): Promise<SettingsRead<RecordingsSettings>> =>
  apost<SettingsRead<RecordingsSettings>>("/recordings/settings", {
    rec_storage_mb: p.storageMb, rec_when_full: p.whenFull,
  });

/**
 * Save the storage budget. The POST answers with the EFFECTIVE settings, so
 * success needs no confirming read -- the response is the new value and is
 * written straight into the entry the three readers share. The status IS
 * computed against the cap, so that one entry is invalidated.
 */
export function useSaveRecordingsBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: saveRecordingsBudget,
    onSuccess: (data) => {
      qc.setQueryData<SettingsRead<RecordingsSettings>>(birdsKeys.recordingsSettings(), data);
      void qc.invalidateQueries({ queryKey: birdsKeys.recordingsStatus() });
    },
  });
}

/**
 * Pin or unpin one clip. A pinned clip is protected from eviction, so this moves
 * `protected_bytes` in `/recordings/status` as well as the row it was clicked
 * on: the whole recordings family is invalidated, not the one list the button
 * happened to be in.
 */
export function usePinRecording() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { id: number; pinned: boolean }) =>
      apost(`/recordings/${p.id}/pin`, { pinned: p.pinned }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() }); },
  });
}

/** Delete one clip. Changes the list, the storage totals, and the owning bird's `clip_count`. */
export function useDeleteRecording() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => adel(`/recordings/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() });
      void qc.invalidateQueries({ queryKey: birdsKeys.birdAll() });
    },
  });
}

/** Delete every clip of one species, pinned ones included. Same reach as a single delete. */
export function useClearSpeciesRecordings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sci: string) => adel(`/recordings?sci=${encodeURIComponent(sci)}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() });
      void qc.invalidateQueries({ queryKey: birdsKeys.birdAll() });
    },
  });
}

/**
 * Set one species' recording rule. `save_mode` is part of the BIRD payload, and
 * "keep" makes that bird's clips protected, so it reaches both families.
 */
export function useSetSpeciesSaveMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { sci: string; mode: SpeciesSaveMode }) =>
      apost<{ save_mode: SpeciesSaveMode }>("/species/recording", { sci: p.sci, save_mode: p.mode }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.birdAll() });
      void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() });
    },
  });
}

/**
 * Re-classify one detection between confirmed / suggested / rejected.
 *
 * INVALIDATES THE WHOLE APP, and that is not laziness. A detection's status is
 * what every aggregate in Birds counts: the species lists, the location
 * summaries, the activity bars and the dashboard totals are all derived from
 * these rows, and a rejected one drops out of all of them. The button used to
 * re-read only the page it was on, so every other open view kept the old number
 * until its own poll happened to land. Only MOUNTED queries actually refetch;
 * the rest are marked stale and re-read when something looks at them.
 */
export function useSetDetectionStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (p: { id: number; status: DetectionStatus }) =>
      apost("/detection/status", { id: p.id, status: p.status }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: birdsKeys.all() }); },
  });
}

// ---------------------------------------------------------------------------
// THE LIVE READS, AND WHY THEIR CADENCE IS DECLARED HERE.
//
// A KEY AND A FETCHER ARE ONLY TWO THIRDS OF A SHARED READ. The third is the
// CADENCE. Two readers that spell the interval differently are two polls again,
// on one cache entry this time, which is better but still not one read. So the
// two genuinely live reads of this app are exported as HOOKS, the same shape
// `location/queries.ts` uses for `/latest`, and the numbers live here.
//
// WHAT WAS THERE BEFORE. `/live` had two readers: the live-feed viewer polled
// `/live` every 1.2s through `usePoll` into its own `useState`, and the aviary
// scene ran a raw 5s `setInterval` whose callback awaited `/live` AND
// `/detections?limit=80` in sequence and pushed the result into a ref. Two
// timers, two copies of one payload, and the aviary's own copy could be five
// seconds behind the spectrogram's for the same instant of the same yard.
//
// A NOTE ON ORDERING, because it was a real defect rather than a tidiness
// point. The 1.2s poll had NO ordering guarantee: one response slower than
// 1200ms and two requests were in flight at once, and whichever landed last
// won. The spectrogram diffs each payload against the previous one to find the
// newly-arrived columns, so an out-of-order window has no overlap with what it
// is diffed against, and the canvas re-enqueues the whole window. Query removes
// the case: `refetchInterval` calls the observer's fetch with no
// `cancelRefetch`, so a tick that arrives while a fetch is in flight is handed
// the IN-FLIGHT promise instead of starting a second request
// (`query-core/query.js`, `fetch()`). At most one request per key at a time, so
// responses cannot cross. The canvas still guards the no-overlap case, because
// a poll that PAUSED while the tab was hidden legitimately comes back to a
// window that has moved on entirely; see `LiveFeed.tsx`.

/** One species a device is hearing right now, as `/live` reports it. */
export type LiveHearing = {
  common_name: string;
  scientific_name: string;
  confidence: number;
  is_bird: boolean;
};

/** One broadcasting device's rolling window. `cols` is the spectrogram. */
export type LiveFeedRow = {
  device_token: string;
  device: string | null;
  location: string | null;
  owner: string | null;
  cols: number[][];
  hearing: LiveHearing[];
};

/** `GET /api/birds/live`. Empty `feeds` is the normal state: nobody broadcasting. */
export type LiveRead = { feeds: LiveFeedRow[] };

/**
 * WHAT A READER NEEDS FROM `/live`, which is what sets its cadence. Named by
 * the need rather than by a number so a call site declares why it wants the
 * rate it wants, and so the two numbers stay in one place.
 */
export type LiveNeed = "spectrogram" | "presence";

const LIVE_MS: Record<LiveNeed, number> = {
  // A scrolling spectrogram consumes a rolling window of columns and animates
  // between payloads. It is the tightest cadence in the app and it is the one
  // that justifies itself: slower and the canvas runs out of columns to scroll.
  spectrogram: 1200,
  // Presence only has to answer "was this species heard in the last fifteen
  // minutes". Five seconds is already three hundred times finer than the window
  // it feeds; the value is kept as it was rather than tightened for free.
  presence: 5000,
};

/**
 * The live feeds, on ONE cache entry per location.
 *
 * The two readers ask for different cadences and that is not a contradiction:
 * `refetchInterval` is per-observer and the cache entry is shared, so while both
 * are mounted the entry re-reads at the tighter of the two and BOTH render the
 * same payload. That is strictly fewer requests than the two timers it replaces
 * (1.2s + 5s), and it removes the case where the scene and the spectrogram
 * disagreed about the same moment.
 *
 * `staleTime: 0` because this read owns its cadence: the app-wide 30s staleness
 * default is twenty-five times the spectrogram's interval, so leaving it in
 * place would serve nearly every tick from cache and stop the feed.
 *
 * `retry: false` for the same reason `usePoll` never retried: a failed tick is
 * followed by the next tick, and the app's transient-retry default would put
 * extra requests in front of a poll that is about to re-read anyway.
 */
export function useLiveFeeds(p: { locationId: number | null; need: LiveNeed }) {
  const key = { locationId: p.locationId };
  return useQuery({
    queryKey: birdsKeys.live(key),
    queryFn: () => fetchLive<LiveRead>(key),
    refetchInterval: LIVE_MS[p.need],
    staleTime: 0,
    retry: false,
    // Changing location must not blank the feed while the new one loads.
    placeholderData: keepPreviousData,
  });
}

/**
 * The window of the yard feed the home page already reads.
 *
 * ONE NUMBER, TWO CALLERS, ON PURPOSE. `limit` is in the key, so `limit=80` and
 * `limit=1000` are different cache entries that no invalidation keeps in step
 * and that cost two requests wherever both are mounted -- which on the Birds
 * home is always, because the recent card and the aviary scene sit on the same
 * screen. They collapse onto the LARGER window because it is a superset in
 * content: both routes answer "the most recent N detections", so the 1000-row
 * answer already contains every row the 80-row answer would have had.
 *
 * `Birds.tsx` spells the same number as `RECENT_LIMIT`; it is the same fact and
 * the two must stay equal, or the sharing silently stops (two entries again, no
 * incorrect data). Read it from here when that file is next touched.
 */
export const YARD_FEED_LIMIT = 1000;

/** A row of the yard feed, as both of its readers use it. */
export type YardDetection = {
  date?: string;
  time: string;
  common_name: string;
  scientific_name: string;
};

/**
 * The yard feed, WITHOUT a cadence of its own.
 *
 * This is deliberate and it is the whole reason the collapse is a win. A second
 * observer with its own interval would make the shared entry re-read at the
 * tighter of the two intervals, and a 1000-row payload fetched more often is
 * not fresher data, it is more bytes for the same answer. The entry already has
 * an owner that polls it (the home page's recent card), this reader is always
 * mounted beside that owner, and it re-reads on mount and on focus like every
 * other query. Where the owner is NOT mounted there is no yard feed to read:
 * the card is gated on the yard having any detections at all.
 *
 * No `staleTime: 0` either: that is a rule for a read that owns its cadence, and
 * the app default is what lets a mount beside a card that read two seconds ago
 * cost nothing. Staleness IS per-observer, so the owner keeping its own zero and
 * this reader taking the default is not a disagreement.
 *
 * `retry` is NOT per-observer, and that is why it is restated here. A query's
 * retry policy comes from whichever observer triggered the fetch, so two
 * observers of one entry that disagree about it make the entry's behaviour
 * depend on who happened to tick first. The owner declared `retry: false`
 * (a failed tick is followed by the next tick); this matches it, so the answer
 * is the same either way.
 */
export function useYardFeed(p: NonBird) {
  const q = { ...p, limit: YARD_FEED_LIMIT };
  return useQuery({
    queryKey: birdsKeys.detections(q),
    queryFn: () => fetchDetections<{ detections: YardDetection[] }>(q),
    retry: false,
    placeholderData: keepPreviousData,
  });
}

/**
 * `GET /api/birds/summary`, in full.
 *
 * The scope provider declares a two-field view of this same entry (it only asks
 * "is there a yard feed at all"). This is the whole shape, for the reader that
 * needs the counts: the app launcher's status line, which used to fetch the URL
 * itself and parse an untyped body.
 *
 * `available` is unconditionally `true` from this route (`routes/birds.py`), so
 * a caller wanting "is there anything here" must read the COUNTS, not the flag.
 */
export type YardSummary = {
  available: boolean;
  total_detections: number;
  species_count: number;
  first_date: string | null;
  last_date: string | null;
  active_days: number;
  today: { detections: number; species: number; date: string };
};

/**
 * The UNFILTERED summary: the entry the scope provider already reads, and the
 * one the app launcher's status line wants.
 *
 * Exported as a params constant plus a concrete fetcher rather than as a type
 * the caller applies, so the launcher probe needs no type argument and no
 * second spelling of `{ includeNonBird: false }`. A probe that has to restate
 * the params is a probe that can key one request and send another, which is the
 * defect this whole module exists to make unrepresentable.
 */
export const YARD_SUMMARY_PARAMS: NonBird = { includeNonBird: false };

export const fetchYardSummary = (): Promise<YardSummary> =>
  fetchSummary<YardSummary>(YARD_SUMMARY_PARAMS);
