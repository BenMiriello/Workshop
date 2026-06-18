import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Waypoints, Tag as TagIcon, Volume2, Info, Map as MapIcon, ZoomIn, ZoomOut, Maximize2, X, ScatterChart, Grid3x3, Sparkles } from "lucide-react";
import { Button, Segmented, StateBlock, Skeleton, StatusPill, catFor, CHART_TARGET, type CatHue } from "../../../ui";
import BirdsHeader, { InlineSelect } from "./BirdsHeader";
import { ClipPlayButton } from "./recordings";
import { jget, apost, API, conf, hh } from "./ui";

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

const fmtTime = (ts: string) => {
  try { return new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  catch { return ts; }
};

export default function Atlas() {
  const colors = useThemeColors();
  const [status, setStatus] = useState<AtlasStatus | null>(null);
  const [scope, setScope] = useState<"global" | "species">("global");
  const [speciesList, setSpeciesList] = useState<SpeciesRow[] | null>(null);
  const [species, setSpecies] = useState<string>("");
  const [data, setData] = useState<AtlasData | null>(null);
  const [err, setErr] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<Point | null>(null);
  const [activeCluster, setActiveCluster] = useState<number | null>(null);

  const loadStatus = useCallback(() => {
    jget<AtlasStatus>("/atlas/status").then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => {
    jget<{ species: SpeciesRow[] }>("/atlas/species")
      .then((r) => { setSpeciesList(r.species); if (r.species[0] && !species) setSpecies(r.species[0].scientific_name); })
      .catch(() => setSpeciesList([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.version]);

  const load = useCallback(() => {
    setLoading(true); setErr(false); setSel(null); setActiveCluster(null);
    const q = scope === "species"
      ? `/atlas?scope=species&species=${encodeURIComponent(species)}`
      : "/atlas?scope=global";
    jget<AtlasData>(q).then((d) => { setData(d); setLoading(false); })
      .catch(() => { setErr(true); setLoading(false); });
  }, [scope, species]);
  useEffect(() => {
    if (scope === "species" && !species) return;
    load();
  }, [load, scope, species]);

  // The Atlas rebuilds itself nightly (and Settings has a manual trigger). Poll
  // status slowly so a rebuild that lands while this page is open refreshes the
  // view — when the version bumps, reload the points.
  useEffect(() => {
    const t = setInterval(loadStatus, 60000);
    return () => clearInterval(t);
  }, [loadStatus]);
  const lastVersion = useRef(status?.version);
  useEffect(() => {
    if (status && lastVersion.current != null && status.version !== lastVersion.current) load();
    if (status) lastVersion.current = status.version;
  }, [status, load]);

  const colorForSpecies = (sci: string) => colors.cat[catFor(sci)];
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
            icon={<MapIcon size={14} />} ariaLabel="Species" value={species}
            onChange={setSpecies}
            options={speciesList.map((s) => ({
              value: s.scientific_name,
              label: `${s.common_name || s.scientific_name} · ${s.n} call${s.n === 1 ? "" : "s"}${s.clusters > 0 ? ` · ${s.clusters} group${s.clusters === 1 ? "" : "s"}` : ""}`,
            }))}
          />
        )}
      </div>

      {loading ? (
        <Skeleton style={{ height: 420 }} />
      ) : err ? (
        <StateBlock variant="error" title="Couldn't load the Atlas"
          hint={`Fetch failed: GET ${API}/atlas. Retry once detections + a build exist.`}
          action={<Button onClick={load}>Retry</Button>} />
      ) : !data || data.points.length === 0 ? (
        <EmptyAtlas status={status} scope={scope} />
      ) : (
        <>
          <div className="atlas-layout">
            <Scatter
              data={data} scope={scope} colors={colors}
              colorForSpecies={colorForSpecies} clusterColors={clusterColors}
              sel={sel} setSel={(p) => { setSel(p); if (scope === "species" && p?.cluster_id) setActiveCluster(p.cluster_id); }}
              activeCluster={activeCluster}
            />
            <aside className="atlas-side">
              {scope === "species" ? (
                <ClusterPanel
                  data={data} clusterColors={clusterColors} activeCluster={activeCluster}
                  setActiveCluster={setActiveCluster} onLabeled={() => { loadStatus(); load(); }}
                />
              ) : (
                <OverviewLegend data={data} colorForSpecies={colorForSpecies}
                  onPick={(sci) => { setScope("species"); setSpecies(sci); }} />
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
  const [d, setD] = useState<InsightsData | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(`${API}/atlas/insights?days=30`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((x) => { if (live) setD(x); })
      .catch(() => { if (live) setErr(true); });
    return () => { live = false; };
  }, []);
  if (err) return null;
  if (!d) return <Skeleton style={{ height: 200, marginTop: "1rem" }} />;
  return (
    <div style={{ marginTop: "1.25rem", display: "grid", gap: "1rem" }}>
      <PresenceChatter rows={d.scatter} />
      <CallTypeHeatmap heatmap={d.heatmap} />
      <NoveltyTimeline items={d.novelty} days={d.days} />
    </div>
  );
}

// 5.1 — presence vs chatter scatter. x=presence (15-min windows), y=raw
// detections; the dashed diagonal is the typical detections-per-presence ratio.
// Above the line = over-talkers (false signals); below = present but quiet.
function PresenceChatter({ rows }: { rows: ScatterPt[] }) {
  if (!rows.length) return null;
  const W = 320, H = 210, pad = { l: 38, r: 12, t: 10, b: 30 };
  const maxP = Math.max(1, ...rows.map((r) => r.presence));
  const maxD = Math.max(1, ...rows.map((r) => r.detections));
  const totP = rows.reduce((s, r) => s + r.presence, 0) || 1;
  const ratio = rows.reduce((s, r) => s + r.detections, 0) / totP;
  const x = (p: number) => pad.l + (p / maxP) * (W - pad.l - pad.r);
  const y = (n: number) => H - pad.b - (n / maxD) * (H - pad.t - pad.b);
  const diagX = maxP, diagY = Math.min(maxD, ratio * maxP);
  return (
    <section className="card" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><ScatterChart size={16} /> Presence ↔ chatter</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: ".2rem 0 .6rem" }}>
        Each dot is a species: how present (15-min windows) against how chatty (raw detections). Dots above the line over-talk; below are present but quiet.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxHeight: 230 }} role="img" aria-label="Presence versus detections by species">
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={H - pad.b} stroke="var(--border)" />
        <line x1={pad.l} y1={H - pad.b} x2={W - pad.r} y2={H - pad.b} stroke="var(--border)" />
        <line x1={x(0)} y1={y(0)} x2={x(diagX)} y2={y(diagY)} stroke={CHART_TARGET} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
        {rows.map((r) => (
          <circle key={r.sci} cx={x(r.presence)} cy={y(r.detections)} r={3.2}
            fill="var(--cat-violet)" fillOpacity={0.82}>
            <title>{r.common}: {r.presence} window{r.presence === 1 ? "" : "s"}, {r.detections} detection{r.detections === 1 ? "" : "s"}</title>
          </circle>
        ))}
        <text x={(pad.l + W - pad.r) / 2} y={H - 6} textAnchor="middle" fontSize="9" fill="var(--muted)">presence (15-min windows)</text>
        <text x={10} y={(pad.t + H - pad.b) / 2} fontSize="9" fill="var(--muted)" transform={`rotate(-90 10 ${(pad.t + H - pad.b) / 2})`} textAnchor="middle">raw detections</text>
      </svg>
    </section>
  );
}

// 5.2 — call-type x hour-of-day heatmap (intensity = frequency).
function CallTypeHeatmap({ heatmap }: { heatmap: { call_types: string[]; rows: HeatRow[] } }) {
  const max = Math.max(1, ...heatmap.rows.flatMap((r) => r.hours));
  const has = heatmap.rows.some((r) => r.hours.some((h) => h > 0));
  if (!has) return null;
  return (
    <section className="card" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><Grid3x3 size={16} /> Daily rhythm by call type</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: ".2rem 0 .6rem" }}>
        When each kind of call happens across the day — the dawn chorus, the all-day contact hum, the begging spike.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "5rem 1fr", gap: ".25rem .5rem", alignItems: "center" }}>
        {heatmap.rows.map((r) => (
          <Fragment key={r.call_type}>
            <span className="muted" style={{ fontSize: ".78rem", textTransform: "capitalize" }}>{r.call_type}</span>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(24, 1fr)", gap: 1 }}>
              {r.hours.map((v, h) => (
                <div key={h} title={`${r.call_type} · ${hh(h)} · ${v}`}
                  style={{ height: 14, borderRadius: 2, background: v > 0 ? "var(--cat-blue)" : "var(--border)", opacity: v > 0 ? Math.max(0.18, v / max) : 0.4 }} />
              ))}
            </div>
            <span />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(24, 1fr)", fontSize: ".6rem" }} className="muted">
              {Array.from({ length: 24 }).map((_, h) => <span key={h} style={{ textAlign: "center" }}>{h % 6 === 0 ? h : ""}</span>)}
            </div>
          </Fragment>
        ))}
      </div>
    </section>
  );
}

// 5.3 — acoustic-novelty timeline: first appearance of each species over the
// window; recent arrivals (<=7d) are ringed and labeled.
function NoveltyTimeline({ items, days }: { items: NoveltyPt[]; days: number }) {
  if (!items.length) return null;
  const W = 320, H = 64, pad = { l: 10, r: 10, t: 10, b: 18 };
  const now = Date.now();
  const start = now - days * 86_400_000;
  const span = now - start || 1;
  const px = (date: string) => {
    const t = new Date(`${date}T12:00:00`).getTime();
    return pad.l + Math.max(0, Math.min(1, (t - start) / span)) * (W - pad.l - pad.r);
  };
  const recent = items.filter((it) => new Date(`${it.date}T12:00:00`).getTime() >= now - 7 * 86_400_000);
  const baseY = H - pad.b;
  return (
    <section className="card" style={{ padding: "1rem" }}>
      <h3 className="atlas-h3"><Sparkles size={16} /> Acoustic novelty</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: ".2rem 0 .6rem" }}>
        First time each species was heard in the last {days} days — recent arrivals ringed.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="Species first-appearance timeline">
        <line x1={pad.l} y1={baseY} x2={W - pad.r} y2={baseY} stroke="var(--border)" />
        {items.map((it, i) => {
          const isRecent = new Date(`${it.date}T12:00:00`).getTime() >= now - 7 * 86_400_000;
          return (
            <circle key={`${it.sci}${i}`} cx={px(it.date)} cy={baseY} r={isRecent ? 4 : 2.6}
              fill={isRecent ? "var(--accent)" : "var(--cat-slate)"}
              stroke={isRecent ? "var(--accent)" : "none"} strokeOpacity={0.4} strokeWidth={isRecent ? 4 : 0}>
              <title>{it.common} · first heard {it.date}</title>
            </circle>
          );
        })}
        <text x={pad.l} y={H - 4} fontSize="9" fill="var(--muted)">{days}d ago</text>
        <text x={W - pad.r} y={H - 4} fontSize="9" fill="var(--muted)" textAnchor="end">today</text>
      </svg>
      {recent.length > 0 && (
        <div style={{ marginTop: ".5rem", fontSize: ".82rem" }}>
          <span className="muted">Just arrived: </span>
          {recent.slice(0, 6).map((it, i) => (
            <span key={it.sci}>
              {i > 0 ? ", " : ""}
              <Link to={`/apps/birds/bird?sci=${encodeURIComponent(it.sci)}`} style={{ textDecoration: "none", color: "inherit", fontWeight: 500 }}>{it.common}</Link>
            </span>
          ))}
        </div>
      )}
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
  const [, force] = useState(0);

  const popColor = (p: Point) =>
    scope === "species" && p.cluster_id != null
      ? (clusterColors[p.cluster_id] || colors.muted)
      : colorForSpecies(p.sci);

  // World bounds -> fit into the canvas with padding, recomputed when data changes.
  const bounds = useMemo(() => {
    const xs = data.points.map((p) => p.x), ys = data.points.map((p) => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    return { minX, maxX, minY, maxY };
  }, [data]);

  const fit = useCallback(() => {
    const cv = canvasRef.current; if (!cv) return;
    const w = cv.clientWidth, h = cv.clientHeight, pad = 28;
    const bw = bounds.maxX - bounds.minX || 1, bh = bounds.maxY - bounds.minY || 1;
    const scale = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh);
    view.current = {
      scale,
      ox: pad - bounds.minX * scale + (w - pad * 2 - bw * scale) / 2,
      oy: pad - bounds.minY * scale + (h - pad * 2 - bh * scale) / 2,
    };
    force((n) => n + 1);
  }, [bounds]);
  useEffect(() => { fit(); }, [fit]);

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
  useEffect(() => { draw(); });

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
            <>Still listening. {n} call{n === 1 ? "" : "s"} so far. Once there are about {SPECIES_SPLIT_MIN},
            the Atlas starts grouping ones that sound alike. For now, tap any point on the map to hear it.</>
          ) : (
            <>These {n} call{n === 1 ? "" : "s"} all sound alike so far, so there's just one group. As the bird's
            calls vary, the Atlas splits them into groups here. Tap any point on the map to hear it.</>
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
  const [clips, setClips] = useState<ClusterClips | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setLabel(cluster.label || ""); }, [cluster.label]);
  useEffect(() => {
    if (active && !clips) jget<ClusterClips>(`/atlas/cluster/${cluster.id}/clips`).then(setClips).catch(() => setClips(null));
  }, [active, cluster.id, clips]);

  const save = (next: string) => {
    setLabel(next); setSaving(true);
    apost(`/atlas/cluster/${cluster.id}/label`, next ? { label: next } : {})
      .then(() => { setSaving(false); onLabeled(); })
      .catch(() => setSaving(false));
  };

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
          {clips === null ? (
            <span className="muted" style={{ fontSize: ".8rem", display: "block" }}>Loading…</span>
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
                disabled={saving}
                onClick={() => save(label === t ? "" : t)}>{cap(t)}</button>
            ))}
          </div>
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
