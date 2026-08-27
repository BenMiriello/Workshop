import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Waypoints, Tag as TagIcon, Volume2, Info, Map as MapIcon, ZoomIn, ZoomOut, Maximize2, X, ScatterChart, Grid3x3, Sparkles } from "lucide-react";
import { Button, Notice, Segmented, StateBlock, Skeleton, StatusPill, catFor, type CatHue, timeAbsoluteShort } from "@workshop/ui";
import BirdsHeader, { InlineSelect } from "./BirdsHeader";
import { ClipPlayButton } from "./recordings";
import { API, conf } from "./ui";
import {
  birdsKeys, fetchAtlas, fetchAtlasClusterClips, fetchAtlasInsights,
  fetchAtlasSpecies, fetchAtlasStatus, saveClusterLabel,
} from "./queries";
import { scatterSVG, heatSVG, noveltySVG } from "./atlasInsightCharts";

// The Soundscape Atlas: every stored detection is a point in a learned acoustic
// map (BirdNET embedding -> UMAP). The Overview colours the whole yard by species
// (a new blob = a new sound). Per species, the points fall into call-type clusters
// you label once (song / contact / alarm / ...); the label then auto-tags new
// detections by nearest cluster. This page is the labelling surface.

type AtlasStatus = {
  built: boolean; version: number; embedded: number; clustered: number;
  clusters: number; labeled_clusters: number; last_build: string | null;
  build_started: string | null; clip_save: boolean; clip_count: number;
};
type SpeciesRow = { scientific_name: string; common_name: string | null; n: number; clusters: number; labeled: number };
type Point = {
  det_id: number; x: number; y: number; cluster_id: number | null;
  sci: string; common: string | null; ts: string; confidence: number;
  call_type: string | null; rec_id: number | null; has_clip: 0 | 1; has_rec: 0 | 1;
};
type Cluster = { id: number; species: string | null; label: string | null; size: number };
type AtlasData = { scope: string; species: string | null; has_species_map?: boolean; points: Point[]; clusters: Cluster[]; call_types: string[] };
type ClusterClips = {
  cluster: { id: number; label: string | null; size: number } | null;
  atlas_clips: { id: number; det_id: number; ts: string; confidence: number; common: string | null }[];
  recordings: { id: number; det_id: number; ts: string; confidence: number; common: string | null }[];
};

const CALL_TYPES = ["song", "contact", "alarm", "flight", "begging", "other"];
// The build is nightly, so the only thing this cadence has to catch is a rebuild
// that lands while the page is open. It pauses while the tab is hidden (the
// client's default) because nobody is reading a map they cannot see.
const STATUS_MS = 60_000;
// The insight views' window. It is in the URL, so it is in the key.
const INSIGHT_DAYS = 30;
// Mirrors the backend MIN_SPECIES_N: a species needs this many embedded detections
// before its calls are split into call-type clusters.
const SPECIES_SPLIT_MIN = 20;

// Resolve the design-system categorical hues to concrete colours once, so the
// canvas can paint with them (a canvas can't take a CSS var). No hex in source.
function useThemeColors() {
  return useMemo(() => {
    const cs = getComputedStyle(document.documentElement);
    const v = (n: string, fb: string) => cs.getPropertyValue(n).trim() || fb;
    const hues: CatHue[] = ["violet", "turquoise", "blue", "indigo", "orchid", "slate"];
    const cat: Record<string, string> = {};
    for (const h of hues) cat[h] = v(`--cat-${h}`, "gray");
    return {
      cat,
      muted: v("--muted", "gray"),
      text: v("--text", "black"),
      border: v("--border", "gray"),
      accent: v("--accent", "purple"),
    };
  }, []);
}

const fmtTime = timeAbsoluteShort;

export default function Atlas() {
  const qc = useQueryClient();
  const colors = useThemeColors();
  const [scope, setScope] = useState<"global" | "species">("global");
  const [species, setSpecies] = useState<string>("");

  // THE SELECTION IS AN ID, NOT A POINT, and that is the whole fix for the
  // defect this page had. A rebuild re-projects every detection, so the point
  // objects are replaced wholesale and their x/y move. Holding the object meant
  // the only safe thing to do on a rebuild was throw the selection away, which
  // is what `load()` did -- so a nightly build landing while the operator had a
  // cluster open closed it and cleared the dot, with no explanation.
  //
  // Holding the ID instead makes the honest rule expressible: KEEP THE
  // SELECTION IF IT STILL RESOLVES, DROP IT IF IT DOES NOT. A detection that
  // survived the rebuild is still that detection, and re-resolving it against
  // the new points picks up its new coordinates, so the popover follows the dot
  // instead of pointing at where it used to be. A detection that is gone (or a
  // scope the operator switched away from) resolves to nothing and the
  // selection clears -- not because we discarded it, but because there is
  // nothing left to select. No effect, no reset, nothing to keep in step.
  const [selId, setSelId] = useState<number | null>(null);
  const [clusterId, setClusterId] = useState<number | null>(null);

  const statusQ = useQuery({
    queryKey: birdsKeys.atlasStatus(),
    queryFn: () => fetchAtlasStatus<AtlasStatus>(),
    // This read owns its own cadence, so it neither goes stale nor retries: a
    // failed tick is followed by the next tick, which is what the poll it
    // replaces did.
    refetchInterval: STATUS_MS,
    staleTime: 0,
    retry: false,
  });
  const status = statusQ.data ?? null;

  const speciesQ = useQuery({
    queryKey: birdsKeys.atlasSpecies(),
    queryFn: () => fetchAtlasSpecies<{ species: SpeciesRow[] }>(),
  });
  const speciesList = speciesQ.data?.species ?? null;
  // The species picker DEFAULTS to the first row rather than being seeded into
  // state by an effect. An effect that writes `species` on first response is a
  // second owner of the same value, and it fought the operator's own choice
  // every time the list re-read.
  const sci = species || speciesList?.[0]?.scientific_name || "";
  // In species scope with nothing to pick, the points query is `enabled: false`
  // and therefore permanently pending. That must read as "nothing here yet",
  // not as a skeleton that never resolves.
  const noSpecies = scope === "species" && speciesList != null && speciesList.length === 0;

  // A REBUILD IS A NEW VERSION, and everything derived from the projection is
  // downstream of it: the points, the species menu, each cluster's clips, the
  // insight views. One invalidation reaches all of them, and the queries that
  // are actually mounted re-read while the rest are simply marked stale. This
  // replaces two hand-rolled reactions (a re-fetch of the species list keyed on
  // `status?.version`, and a `lastVersion` ref that called `load()`), and it is
  // also the ONLY reaction to a version bump: a cluster label write bumps the
  // version server-side, so its handler re-reads status and lets this fire.
  //
  // `atlasAll()` covers `atlasStatus()` too. That costs one extra status read
  // per rebuild and buys one prefix instead of four; the refetch it triggers
  // carries the same version, so this does not re-enter.
  const version = status?.version ?? null;
  const seenVersion = useRef<number | null>(null);
  useEffect(() => {
    if (version == null) return;
    const prev = seenVersion.current;
    seenVersion.current = version;
    if (prev != null && prev !== version) void qc.invalidateQueries({ queryKey: birdsKeys.atlasAll() });
  }, [version, qc]);

  // The points. Both URL parameters are in the key, so switching scope or
  // species resolves as its own cache entry and a slow answer for the place you
  // just left has nowhere to land. There is no companion effect: a key change
  // IS the refetch.
  const atlasParams = { scope, species: scope === "species" ? sci : null } as const;
  const dataQ = useQuery({
    queryKey: birdsKeys.atlas(atlasParams),
    queryFn: () => fetchAtlas<AtlasData>(atlasParams),
    // In species scope there is nothing to ask for until the menu has loaded.
    enabled: scope === "global" || !!sci,
    // Keep the map on screen while the next one loads instead of blanking it.
    placeholderData: keepPreviousData,
  });
  const data = dataQ.data ?? null;
  const dataErr = dataQ.isError ? String(dataQ.error) : null;

  // Both resolved against the points currently on screen. See `selId` above.
  const sel = useMemo(
    () => (selId == null ? null : data?.points.find((p) => p.det_id === selId) ?? null),
    [selId, data],
  );
  const activeCluster = clusterId != null && data?.clusters.some((c) => c.id === clusterId)
    ? clusterId : null;

  // Stable, so the canvas redraws when a colour or a selection changes and not
  // once per render. See the draw effect in `Scatter`.
  const colorForSpecies = useCallback((s: string) => colors.cat[catFor(s)], [colors]);
  const clusterColors = useMemo(() => {
    const m: Record<number, string> = {};
    const hues = Object.keys(colors.cat);
    (data?.clusters || []).forEach((c, i) => { m[c.id] = colors.cat[hues[i % hues.length]]; });
    return m;
  }, [data, colors]);

  return (
    <div>
      <BirdsHeader title="Soundscape Atlas" />

      <StatusLine status={status} />

      <div style={{ display: "flex", gap: ".6rem", alignItems: "center", flexWrap: "wrap", margin: ".4rem 0 1rem" }}>
        <Segmented
          value={scope}
          onChange={(v) => setScope(v as "global" | "species")}
          options={[{ value: "global", label: "Overview" }, { value: "species", label: "By species" }]}
        />
        {scope === "species" && speciesList && speciesList.length > 0 && (
          <InlineSelect
            icon={<MapIcon size={14} />} ariaLabel="Species" value={sci}
            onChange={setSpecies}
            options={speciesList.map((s) => ({
              value: s.scientific_name,
              label: `${s.common_name || s.scientific_name} · ${s.n} call${s.n === 1 ? "" : "s"}${s.clusters > 0 ? ` · ${s.clusters} group${s.clusters === 1 ? "" : "s"}` : ""}`,
            }))}
          />
        )}
      </div>

      {/* A failed read with the map still on screen is a LINE beside it; a
          failed read with nothing to show is the REGION below. */}
      {dataErr && data && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void dataQ.refetch()}>Retry</Button>}>
            This map is not refreshing.
          </Notice>
        </div>
      )}

      {dataErr && !data ? (
        <StateBlock variant="error" title="Couldn't load the Atlas"
          hint={`GET ${API}/atlas: ${dataErr}. Retry once detections and a build exist.`}
          action={<Button onClick={() => void dataQ.refetch()}>Retry</Button>} />
      ) : noSpecies ? (
        <EmptyAtlas status={status} scope={scope} />
      ) : dataQ.isPending ? (
        <Skeleton style={{ height: 420 }} />
      ) : !data || data.points.length === 0 ? (
        <EmptyAtlas status={status} scope={scope} />
      ) : (
        <>
          <div className="atlas-layout">
            <Scatter
              data={data} scope={scope} colors={colors}
              colorForSpecies={colorForSpecies} clusterColors={clusterColors}
              sel={sel} setSel={(p) => { setSelId(p?.det_id ?? null); if (scope === "species" && p?.cluster_id) setClusterId(p.cluster_id); }}
              activeCluster={activeCluster}
            />
            <aside className="atlas-side">
              {scope === "species" ? (
                <ClusterPanel
                  data={data} clusterColors={clusterColors} activeCluster={activeCluster}
                  setActiveCluster={setClusterId}
                  // Naming a cluster bumps the Atlas version on the server, so
                  // re-reading STATUS is the whole reaction: the version watcher
                  // above pulls the points, the menu and the insights forward.
                  // One path for "the projection changed", however it changed.
                  onLabeled={() => { void qc.invalidateQueries({ queryKey: birdsKeys.atlasStatus() }); }}
                />
              ) : (
                <OverviewLegend data={data} colorForSpecies={colorForSpecies}
                  onPick={(s) => { setScope("species"); setSpecies(s); }} />
              )}
            </aside>
          </div>
          {/* Reading the soundscape: presence-aware insight views the embedding map
              can't show — built so the loudest birds don't dominate. */}
          <Insights />
        </>
      )}
    </div>
  );
}

// ----- Soundscape insight views (presence over chatter) ----------------------
type ScatterPt = { sci: string; common: string; detections: number; presence: number };
type HeatRow = { call_type: string; hours: number[] };
type NoveltyPt = { date: string; sci: string; common: string; kind: string };
type InsightsData = {
  days: number; scatter: ScatterPt[];
  heatmap: { call_types: string[]; rows: HeatRow[] }; novelty: NoveltyPt[];
};

function Insights() {
  // No `let live = true` teardown guard, and it is not an oversight: that flag
  // existed to stop a response arriving after unmount from writing into a dead
  // component's `useState`. There is no component-local copy to write into any
  // more -- the answer lands in the cache, keyed by the window it was asked
  // for, and an unmounted reader simply is not there to read it.
  const q = useQuery({
    queryKey: birdsKeys.atlasInsights({ days: INSIGHT_DAYS }),
    queryFn: () => fetchAtlasInsights<InsightsData>({ days: INSIGHT_DAYS }),
  });
  if (q.isPending) return <Skeleton style={{ height: 200, marginTop: "1rem" }} />;
  // These views used to VANISH on a failed read, which is indistinguishable
  // from a yard with nothing to say about it. The space would be empty if the
  // failure had not happened, so it is a region.
  if (!q.data) {
    return (
      <div style={{ marginTop: "1.25rem" }}>
        <StateBlock variant="error" title="Couldn't load the soundscape readings"
          hint={`GET ${API}/atlas/insights: ${String(q.error)}`}
          action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>} />
      </div>
    );
  }
  const d = q.data;
  return (
    <div style={{ marginTop: "1.25rem", display: "grid", gap: "1rem" }}>
      <PresenceChatter rows={d.scatter} />
      <CallTypeHeatmap heatmap={d.heatmap} />
      <NoveltyTimeline items={d.novelty} days={d.days} />
    </div>
  );
}

// 5.1 — presence vs chatter scatter (ported from the handoff's renderPresenceScatter).
function PresenceChatter({ rows }: { rows: ScatterPt[] }) {
  if (!rows.length) return null;
  const svg = scatterSVG(rows.map((s) => ({ name: s.common, win: s.presence, det: s.detections })));
  return (
    <section className="card birds-ins" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><ScatterChart size={16} /> Presence ↔ chatter</h3>
      <div dangerouslySetInnerHTML={{ __html: svg }} />
      <p className="cap-note">Dots above the dashed line talk more than their presence warrants. Those below are around constantly, but quiet.</p>
    </section>
  );
}

// 5.2 — call-type x hour heatmap (ported from the handoff's renderHeat). Hidden
// until call clusters are labeled (no fabricated data).
function CallTypeHeatmap({ heatmap }: { heatmap: { call_types: string[]; rows: HeatRow[] } }) {
  const heat = heatmap.rows.map((r) => r.hours);
  const has = heat.some((row) => row.some((v) => v > 0));
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  return (
    <section className="card birds-ins" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><Grid3x3 size={16} /> Daily rhythm by call type</h3>
      {has ? (
        <>
          <div dangerouslySetInnerHTML={{ __html: heatSVG(heatmap.rows.map((r) => cap(r.call_type)), heat) }} />
          <p className="cap-note">When each kind of call happens: the dawn chorus of song, the all-day hum of contact calls, the morning begging spike at the nest.</p>
        </>
      ) : (
        <p className="muted" style={{ fontSize: ".84rem", margin: ".3rem 0 0" }}>Label call groups in the map above to populate this. Call types come from your cluster labels.</p>
      )}
    </section>
  );
}

// 5.3 — acoustic-novelty timeline (ported from the handoff's renderNovelty).
function NoveltyTimeline({ items, days }: { items: NoveltyPt[]; days: number }) {
  if (!items.length) return null;
  const now = Date.now();
  const pts = items.map((n) => {
    const t = new Date(`${n.date}T12:00:00`).getTime();
    const ago = (now - t) / 86_400_000;
    return { day: Math.max(0, Math.min(days, days - ago)), label: n.common, kind: "species" as const, flag: ago <= 7, t };
  }).sort((a, b) => a.t - b.t);
  return (
    <section className="card birds-ins" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><Sparkles size={16} /> Acoustic novelty</h3>
      <div dangerouslySetInnerHTML={{ __html: noveltySVG(pts.map(({ day, label, kind, flag }) => ({ day, label, kind, flag }))) }} />
      <p className="cap-note">What's new in your yard: every point is the first time a species was heard in the last {days} days. Ringed points are recent arrivals worth a look.</p>
    </section>
  );
}

// A slim, button-less status line. The build is self-maintaining (nightly auto-
// rebuild) and a manual "Rebuild now" lives in Birds Settings — never here, so the
// map surface stays about the map.
function StatusLine({ status }: { status: AtlasStatus | null }) {
  if (!status) return null;
  return (
    <div style={{ display: "flex", gap: ".8rem", alignItems: "center", flexWrap: "wrap", fontSize: ".85rem" }}>
      <span className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".35rem" }}>
        <Waypoints size={15} />
        {status.embedded.toLocaleString()} calls · {status.clusters} group{status.clusters === 1 ? "" : "s"} · {status.labeled_clusters} named
      </span>
      {!status.clip_save && (
        <Link to="/apps/birds/settings" className="muted" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
          <Info size={14} /> Turn on Atlas clips to label by ear
        </Link>
      )}
      {status.last_build && <span className="muted" style={{ marginLeft: "auto" }}>updated {fmtTime(status.last_build)}</span>}
    </div>
  );
}

function EmptyAtlas({ status, scope }: { status: AtlasStatus | null; scope: string }) {
  const embedded = status?.embedded ?? 0;
  const hint = scope === "species"
    ? "No points for this species yet. Its calls will appear here as they're detected."
    : embedded === 0
      ? "No embeddings yet. Each stored detection is fingerprinted as it's heard; the map fills in and rebuilds itself automatically as they accumulate."
      : "The map is being assembled, and rebuilds automatically as detections accumulate. Check back shortly.";
  return <StateBlock title="No Atlas to show yet" hint={hint} />;
}

// ---- Canvas scatter --------------------------------------------------------

function Scatter({ data, scope, colors, colorForSpecies, clusterColors, sel, setSel, activeCluster }: {
  data: AtlasData; scope: string; colors: ReturnType<typeof useThemeColors>;
  colorForSpecies: (s: string) => string; clusterColors: Record<number, string>;
  sel: Point | null; setSel: (p: Point | null) => void; activeCluster: number | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const view = useRef({ scale: 1, ox: 0, oy: 0 });

  const popColor = (p: Point) =>
    scope === "species" && p.cluster_id != null
      ? (clusterColors[p.cluster_id] || colors.muted)
      : colorForSpecies(p.sci);

  // The world extent, as FOUR NUMBERS rather than an object, because `fit`
  // depends on it and a refetch that returns the same extent hands back a new
  // object. Keyed on the object, every re-read of the points -- including the
  // one the client runs when the tab comes back -- snapped the operator's pan
  // and zoom back to the default. Keyed on the numbers, only the extent
  // actually MOVING refits, which is what a rebuild does and a refresh does
  // not.
  const { minX, maxX, minY, maxY } = useMemo(() => {
    const xs = data.points.map((p) => p.x), ys = data.points.map((p) => p.y);
    return {
      minX: Math.min(...xs), maxX: Math.max(...xs),
      minY: Math.min(...ys), maxY: Math.max(...ys),
    };
  }, [data.points]);

  const draw = useCallback(() => {
    const cv = canvasRef.current; if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
    const ctx = cv.getContext("2d"); if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const { scale, ox, oy } = view.current;
    for (const p of data.points) {
      const sx = p.x * scale + ox, sy = p.y * scale + oy;
      const isCluster = scope === "species" && p.cluster_id != null;
      let col = isCluster ? (clusterColors[p.cluster_id as number] || colors.muted) : colorForSpecies(p.sci);
      const dim = activeCluster != null && scope === "species" && p.cluster_id !== activeCluster;
      ctx.globalAlpha = dim ? 0.12 : 0.8;
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.arc(sx, sy, sel?.det_id === p.det_id ? 6 : 3, 0, Math.PI * 2);
      ctx.fill();
      if (sel?.det_id === p.det_id) {
        ctx.globalAlpha = 1; ctx.strokeStyle = colors.text; ctx.lineWidth = 1.5; ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    // Glue the info popover to the selected dot — imperative, so it tracks pan/zoom
    // every frame with no React re-render. Clamp horizontally so it stays on-canvas,
    // and flip below the dot when there's no room above (mobile top edge).
    const pop = popRef.current;
    if (pop) {
      if (sel) {
        const sx = sel.x * scale + ox, sy = sel.y * scale + oy;
        pop.style.display = "block";
        pop.style.left = `${Math.max(118, Math.min(sx, w - 118))}px`;
        pop.style.top = `${sy}px`;
        pop.style.transform = sy < 96 ? "translate(-50%, 14px)" : "translate(-50%, calc(-100% - 14px))";
      } else {
        pop.style.display = "none";
      }
    }
  }, [data, scope, clusterColors, colorForSpecies, colors, sel, activeCluster]);

  // Fit the extent into the canvas. It calls `draw` through a REF on purpose:
  // fitting must be triggered by the extent moving and by nothing else, so
  // `draw` may not be a dependency -- selecting a point changes `draw`, and a
  // refit there would throw away the operator's pan and zoom mid-interaction.
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const fit = useCallback(() => {
    const cv = canvasRef.current; if (!cv) return;
    const w = cv.clientWidth, h = cv.clientHeight, pad = 28;
    const bw = maxX - minX || 1, bh = maxY - minY || 1;
    const scale = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh);
    view.current = {
      scale,
      ox: pad - minX * scale + (w - pad * 2 - bw * scale) / 2,
      oy: pad - minY * scale + (h - pad * 2 - bh * scale) / 2,
    };
    drawRef.current();
  }, [minX, maxX, minY, maxY]);
  // FIT BEFORE DRAW, and the order of these two is the order they run in: a
  // draw that lands before the first fit paints the whole map at scale 1 in the
  // top-left corner for one frame.
  useEffect(() => { fit(); }, [fit]);

  // THIS EFFECT USED TO HAVE NO DEPENDENCY ARRAY, so the whole map repainted on
  // every render of this component -- including once a minute, for a status
  // tick that changes nothing on the canvas. It has one now, and the two things
  // that made a dependency array useless here are fixed with it: `draw` is a
  // `useCallback` whose inputs are all memoized (`colorForSpecies` is a
  // `useCallback` in the parent, not a fresh lambda per render), and the fit
  // pass no longer bumps a counter to provoke a repaint.
  useEffect(() => { draw(); }, [draw]);

  const hit = (mx: number, my: number): Point | null => {
    const { scale, ox, oy } = view.current;
    let best: Point | null = null, bestD = 100;
    for (const p of data.points) {
      const sx = p.x * scale + ox, sy = p.y * scale + oy;
      const d = (sx - mx) ** 2 + (sy - my) ** 2;
      if (d < bestD) { bestD = d; best = p; }
    }
    return best;
  };

  // Zoom by `f` keeping screen point (cx,cy) fixed — used by wheel, pinch, buttons.
  const zoomAround = useCallback((f: number, cx: number, cy: number) => {
    if (!f || !isFinite(f)) return;
    const { scale, ox, oy } = view.current;
    view.current = { scale: scale * f, ox: cx - (cx - ox) * f, oy: cy - (cy - oy) * f };
    draw();
  }, [draw]);

  // Pointer events unify mouse, touch and pen: single pointer pans (or taps to
  // select if it didn't move), two pointers pinch-zoom around their midpoint.
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchDist = useRef<number | null>(null);
  const moved = useRef(false);

  const onPointerDown = (e: React.PointerEvent) => {
    try { canvasRef.current?.setPointerCapture?.(e.pointerId); } catch { /* synthetic/edge pointers */ }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    moved.current = false;
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinchDist.current = Math.hypot(a.x - b.x, a.y - b.y);
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const r = canvasRef.current!.getBoundingClientRect();
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const cx = (a.x + b.x) / 2 - r.left, cy = (a.y + b.y) / 2 - r.top;
      if (pinchDist.current) zoomAround(dist / pinchDist.current, cx, cy);
      pinchDist.current = dist;
      moved.current = true;
      return;
    }
    const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved.current = true;
    view.current.ox += dx; view.current.oy += dy;
    draw();
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const had = pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchDist.current = null;
    if (had && pointers.current.size === 0 && !moved.current) {
      const r = canvasRef.current!.getBoundingClientRect();
      setSel(hit(e.clientX - r.left, e.clientY - r.top));
    }
  };
  const onWheel = (e: React.WheelEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    zoomAround(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
  };
  const zoomBtn = (f: number) => () => {
    const cv = canvasRef.current; if (cv) zoomAround(f, cv.clientWidth / 2, cv.clientHeight / 2);
  };

  return (
    <div className="atlas-canvas-wrap">
      <canvas ref={canvasRef} className="atlas-canvas"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onPointerLeave={onPointerUp}
        onWheel={onWheel} />
      <div className="atlas-zoom">
        <button type="button" className="btn sm" onClick={zoomBtn(1.25)} aria-label="Zoom in" title="Zoom in"><ZoomIn size={15} /></button>
        <button type="button" className="btn sm" onClick={zoomBtn(1 / 1.25)} aria-label="Zoom out" title="Zoom out"><ZoomOut size={15} /></button>
        <button type="button" className="btn sm" onClick={fit} aria-label="Fit to view" title="Fit to view"><Maximize2 size={15} /></button>
      </div>
      {/* Info popover anchored to the selected dot (positioned imperatively in draw). */}
      {sel && (
        <div ref={popRef} className="atlas-popover" style={{ display: "none" }}>
          <div className="atlas-popover__row">
            <span className="atlas-swatch" style={{ background: popColor(sel) }} />
            <Link to={`/apps/birds/bird?sci=${encodeURIComponent(sel.sci)}`} className="atlas-popover__name">
              {sel.common || sel.sci}
            </Link>
            {sel.call_type && <StatusPill status="stopped">{cap(sel.call_type)}</StatusPill>}
            <button type="button" className="atlas-popover__close" onClick={() => setSel(null)} aria-label="Close">
              <X size={13} />
            </button>
          </div>
          <div className="atlas-popover__meta muted">{fmtTime(sel.ts)} · {conf(sel.confidence)}</div>
          {sel.rec_id != null && (
            <div className="atlas-popover__play">
              <ClipPlayButton src={`/recordings/${sel.rec_id}/audio`} title="Play this call" size={26} />
              <span className="muted" style={{ fontSize: ".8rem" }}>Play call</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Per-species cluster labelling panel -----------------------------------

function ClusterPanel({ data, clusterColors, activeCluster, setActiveCluster, onLabeled }: {
  data: AtlasData; clusterColors: Record<number, string>;
  activeCluster: number | null; setActiveCluster: (id: number | null) => void; onLabeled: () => void;
}) {
  if (data.clusters.length === 0) {
    const n = data.points.length;
    return (
      <div>
        <h3 className="atlas-h3"><TagIcon size={16} /> Call groups</h3>
        <p className="muted" style={{ fontSize: ".85rem", margin: 0 }}>
          {data.has_species_map === false ? (
            <>Still listening. {n} call{n === 1 ? "" : "s"} so far, and grouping starts at about {SPECIES_SPLIT_MIN}.
            Tap any point on the map to hear it.</>
          ) : (
            <>These {n} call{n === 1 ? "" : "s"} all sound alike so far, so there's just one group.
            Tap any point on the map to hear it.</>
          )}
        </p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="atlas-h3"><TagIcon size={16} /> Call groups</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 .7rem" }}>
        The Atlas grouped calls that sound alike. Open a group to listen, then name it if you recognise the
        call, and new calls join the nearest group automatically.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: ".5rem" }}>
        {data.clusters.map((c, i) => (
          <ClusterRow key={c.id} cluster={c} groupNo={i + 1} color={clusterColors[c.id]}
            active={activeCluster === c.id}
            onToggle={() => setActiveCluster(activeCluster === c.id ? null : c.id)}
            onLabeled={onLabeled} />
        ))}
      </div>
    </div>
  );
}

function ClusterRow({ cluster, groupNo, color, active, onToggle, onLabeled }: {
  cluster: Cluster; groupNo: number; color: string; active: boolean; onToggle: () => void; onLabeled: () => void;
}) {
  const [label, setLabel] = useState(cluster.label || "");
  useEffect(() => { setLabel(cluster.label || ""); }, [cluster.label]);

  // Fetched when the group is OPENED and cached under the cluster's own key, so
  // closing and reopening it is free. The hand-rolled version guarded on `!clips`
  // and set `clips` back to null on a failure, which meant a group whose clips
  // failed once never tried again and sat on "Loading…" for as long as the page
  // stayed open.
  const clipsQ = useQuery({
    queryKey: birdsKeys.atlasClusterClips(cluster.id),
    queryFn: () => fetchAtlasClusterClips<ClusterClips>(cluster.id),
    enabled: active,
  });
  const clips = clipsQ.data ?? null;

  const saveM = useMutation({
    mutationFn: (next: string) => saveClusterLabel({ clusterId: cluster.id, label: next }),
    onSuccess: onLabeled,
  });
  // The chip moves at once, because a name is cheap and reversible. A REJECTED
  // write used to be swallowed whole (`.catch(() => setSaving(false))`), so the
  // chip stayed lit on a name the server never took; now the row says so.
  const save = (next: string) => { setLabel(next); saveM.mutate(next); };

  // Playable samples for this group: the dedicated Atlas exemplar pool first, then
  // any saved recordings of its members. Either is enough to listen + name by ear.
  const exemplars = clips ? [...clips.atlas_clips.map((c) => ({ ...c, src: `/atlas/clip/${c.id}/audio` })),
    ...clips.recordings.map((c) => ({ ...c, src: `/recordings/${c.id}/audio` }))] : [];
  // A named group shows its name; an unnamed one is "Group N" — never the bare word
  // "unlabelled", which reads like an error rather than "you can name this".
  const title = cluster.label ? cap(cluster.label) : `Group ${groupNo}`;

  return (
    <div className="atlas-cluster" data-active={active ? "1" : undefined}>
      <button type="button" className="atlas-cluster__head" onClick={onToggle}>
        <span className="atlas-swatch" style={{ background: color }} />
        <span style={{ fontWeight: 500 }}>{title}</span>
        {!cluster.label && <span className="muted" style={{ fontSize: ".76rem" }}>· unnamed</span>}
        <span className="muted" style={{ fontSize: ".8rem", marginLeft: "auto" }}>{cluster.size} call{cluster.size === 1 ? "" : "s"}</span>
      </button>
      {active && (
        <div className="atlas-cluster__body">
          <span className="muted" style={{ fontSize: ".78rem", display: "inline-flex", alignItems: "center", gap: ".3rem", marginBottom: ".4rem" }}>
            <Volume2 size={13} /> Listen to a few
          </span>
          {clipsQ.isPending ? (
            <Skeleton style={{ height: 30 }} />
          ) : clipsQ.isError ? (
            <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void clipsQ.refetch()}>Retry</Button>}>
              Couldn't load this group's audio.
            </Notice>
          ) : exemplars.length === 0 ? (
            <span className="muted" style={{ fontSize: ".8rem", display: "block" }}>
              No saved audio for this group yet. Tap its points on the map to listen, or turn on Atlas clips in Settings.
            </span>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
              {exemplars.slice(0, 12).map((c) => (
                <ClipPlayButton key={c.src} src={c.src} title={`Play ${fmtTime(c.ts)}`} />
              ))}
            </div>
          )}
          <label className="muted" style={{ fontSize: ".78rem", display: "block", margin: ".8rem 0 .3rem" }}>
            {label ? "Call type" : "Recognise it? Give it a name"}
          </label>
          <div className="atlas-chips">
            {CALL_TYPES.map((t) => (
              <button key={t} type="button"
                className={`atlas-chip${label === t ? " is-on" : ""}`}
                disabled={saveM.isPending}
                onClick={() => save(label === t ? "" : t)}>{cap(t)}</button>
            ))}
          </div>
          {saveM.isError && (
            <div style={{ marginTop: ".4rem" }}>
              <Notice tone="error" detail={String(saveM.error)}
                action={<Button size="sm" variant="ghost" onClick={() => saveM.reset()}>Dismiss</Button>}>
                That name did not go through.
              </Notice>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OverviewLegend({ data, colorForSpecies, onPick }: {
  data: AtlasData; colorForSpecies: (s: string) => string; onPick: (sci: string) => void;
}) {
  const counts = useMemo(() => {
    const m: Record<string, { common: string | null; n: number }> = {};
    for (const p of data.points) { const e = m[p.sci] || (m[p.sci] = { common: p.common, n: 0 }); e.n++; }
    return Object.entries(m).sort((a, b) => b[1].n - a[1].n);
  }, [data]);
  return (
    <div>
      <h3 className="atlas-h3"><MapIcon size={16} /> Species</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 .7rem" }}>
        Each point is one call. Pick a species to explore its call groups.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: ".15rem", maxHeight: 360, overflow: "auto" }}>
        {counts.map(([sci, e]) => (
          <button key={sci} type="button" className="atlas-legend-row" onClick={() => onPick(sci)}>
            <span className="atlas-swatch" style={{ background: colorForSpecies(sci) }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.common || sci}</span>
            <span className="muted" style={{ fontSize: ".8rem", marginLeft: "auto" }}>{e.n}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
