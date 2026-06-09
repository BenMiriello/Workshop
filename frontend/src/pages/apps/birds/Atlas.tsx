import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Waypoints, RefreshCw, Tag as TagIcon, Volume2, Info, Map } from "lucide-react";
import { Button, Segmented, StateBlock, Skeleton, StatusPill, catFor, type CatHue } from "../../../ui";
import BirdsHeader, { InlineSelect } from "./BirdsHeader";
import { ClipPlayButton } from "./recordings";
import { jget, apost, API, conf } from "./ui";

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
  call_type: string | null; has_clip: 0 | 1; has_rec: 0 | 1;
};
type Cluster = { id: number; species: string | null; label: string | null; size: number };
type AtlasData = { scope: string; species: string | null; points: Point[]; clusters: Cluster[]; call_types: string[] };
type ClusterClips = {
  cluster: { id: number; label: string | null; size: number } | null;
  atlas_clips: { id: number; det_id: number; ts: string; confidence: number; common: string | null }[];
  recordings: { id: number; det_id: number; ts: string; confidence: number; common: string | null }[];
};

const CALL_TYPES = ["song", "contact", "alarm", "flight", "begging", "other", "unknown"];

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
  const [building, setBuilding] = useState(false);

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

  // Poll while a rebuild is running (version bumps when it lands).
  useEffect(() => {
    if (!building) return;
    const t = setInterval(loadStatus, 2500);
    return () => clearInterval(t);
  }, [building, loadStatus]);
  const lastVersion = useRef(status?.version);
  useEffect(() => {
    if (building && status && status.version !== lastVersion.current) {
      setBuilding(false); lastVersion.current = status.version; load();
    } else if (status) lastVersion.current = status.version;
  }, [status, building, load]);

  const rebuild = () => {
    setBuilding(true);
    apost("/atlas/rebuild", {}).catch(() => setBuilding(false));
  };

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

      <StatusRow status={status} building={building} onRebuild={rebuild} />

      <div style={{ display: "flex", gap: ".6rem", alignItems: "center", flexWrap: "wrap", margin: ".4rem 0 1rem" }}>
        <Segmented
          value={scope}
          onChange={(v) => setScope(v as "global" | "species")}
          options={[{ value: "global", label: "Overview" }, { value: "species", label: "By species" }]}
        />
        {scope === "species" && speciesList && speciesList.length > 0 && (
          <InlineSelect
            icon={<Map size={14} />} ariaLabel="Species" value={species}
            onChange={setSpecies}
            options={speciesList.map((s) => ({
              value: s.scientific_name,
              label: `${s.common_name || s.scientific_name} · ${s.n}${s.labeled ? "" : " · unlabelled"}`,
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
        <EmptyAtlas status={status} scope={scope} building={building} onRebuild={rebuild} />
      ) : (
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
            {sel && <PointCard p={sel} color={scope === "species" && sel.cluster_id ? clusterColors[sel.cluster_id] : colorForSpecies(sel.sci)} />}
          </aside>
        </div>
      )}
    </div>
  );
}

function StatusRow({ status, building, onRebuild }: { status: AtlasStatus | null; building: boolean; onRebuild: () => void }) {
  if (!status) return null;
  return (
    <div style={{ display: "flex", gap: ".8rem", alignItems: "center", flexWrap: "wrap", fontSize: ".85rem" }}>
      <span className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".35rem" }}>
        <Waypoints size={15} />
        {status.embedded.toLocaleString()} embedded · {status.clusters} clusters · {status.labeled_clusters} labelled
      </span>
      {!status.clip_save && (
        <Link to="/apps/birds/settings" className="muted" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
          <Info size={14} /> Turn on Atlas clips to label by ear
        </Link>
      )}
      <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: ".6rem" }}>
        {status.last_build && <span className="muted">built {fmtTime(status.last_build)}</span>}
        <Button onClick={onRebuild} disabled={building}>
          <RefreshCw size={15} className={building ? "spin" : undefined} /> {building ? "Building…" : "Rebuild"}
        </Button>
      </span>
    </div>
  );
}

function EmptyAtlas({ status, scope, building, onRebuild }: { status: AtlasStatus | null; scope: string; building: boolean; onRebuild: () => void }) {
  const embedded = status?.embedded ?? 0;
  const hint = scope === "species"
    ? "This species has no call-type map yet — it needs ~30 embedded detections before its calls can be split into clusters."
    : embedded === 0
      ? "No embeddings yet. Embeddings are captured as detections are stored; once some accumulate, Rebuild to cluster them."
      : `${embedded} detections embedded. Rebuild to project and cluster them into the Atlas.`;
  return (
    <StateBlock title="No Atlas to show yet" hint={hint}
      action={<Button onClick={onRebuild} disabled={building}><RefreshCw size={15} className={building ? "spin" : undefined} /> {building ? "Building…" : "Build the Atlas"}</Button>} />
  );
}

// ---- Canvas scatter --------------------------------------------------------

function Scatter({ data, scope, colors, colorForSpecies, clusterColors, sel, setSel, activeCluster }: {
  data: AtlasData; scope: string; colors: ReturnType<typeof useThemeColors>;
  colorForSpecies: (s: string) => string; clusterColors: Record<number, string>;
  sel: Point | null; setSel: (p: Point | null) => void; activeCluster: number | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef({ scale: 1, ox: 0, oy: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [, force] = useState(0);

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

  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY }; };
  const onMove = (e: React.MouseEvent) => {
    if (!drag.current) return;
    view.current.ox += e.clientX - drag.current.x;
    view.current.oy += e.clientY - drag.current.y;
    drag.current = { x: e.clientX, y: e.clientY };
    draw();
  };
  const onUp = (e: React.MouseEvent) => {
    const moved = drag.current && (Math.abs(e.clientX - (drag.current.x)) > 3);
    drag.current = null;
    if (moved) return;
    const r = canvasRef.current!.getBoundingClientRect();
    setSel(hit(e.clientX - r.left, e.clientY - r.top));
  };
  const onWheel = (e: React.WheelEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    const { scale, ox, oy } = view.current;
    view.current = { scale: scale * f, ox: mx - (mx - ox) * f, oy: my - (my - oy) * f };
    draw();
  };

  return (
    <div className="atlas-canvas-wrap">
      <canvas ref={canvasRef} className="atlas-canvas"
        onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => (drag.current = null)}
        onWheel={onWheel} />
      <button type="button" className="btn sm atlas-fit" onClick={fit} title="Fit to view">Fit</button>
    </div>
  );
}

// ---- Per-species cluster labelling panel -----------------------------------

function ClusterPanel({ data, clusterColors, activeCluster, setActiveCluster, onLabeled }: {
  data: AtlasData; clusterColors: Record<number, string>;
  activeCluster: number | null; setActiveCluster: (id: number | null) => void; onLabeled: () => void;
}) {
  if (data.clusters.length === 0) {
    return <p className="muted" style={{ fontSize: ".88rem" }}>No clusters for this species yet.</p>;
  }
  return (
    <div>
      <h3 className="atlas-h3"><TagIcon size={16} /> Call-type clusters</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 .7rem" }}>
        Label each cluster once. The label auto-tags new detections by nearest cluster.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: ".5rem" }}>
        {data.clusters.map((c) => (
          <ClusterRow key={c.id} cluster={c} color={clusterColors[c.id]}
            active={activeCluster === c.id}
            onToggle={() => setActiveCluster(activeCluster === c.id ? null : c.id)}
            onLabeled={onLabeled} />
        ))}
      </div>
    </div>
  );
}

function ClusterRow({ cluster, color, active, onToggle, onLabeled }: {
  cluster: Cluster; color: string; active: boolean; onToggle: () => void; onLabeled: () => void;
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

  const exemplars = clips ? [...clips.atlas_clips.map((c) => ({ ...c, src: `/atlas/clip/${c.id}/audio` })),
    ...clips.recordings.map((c) => ({ ...c, src: `/recordings/${c.id}/audio` }))] : [];

  return (
    <div className="atlas-cluster" data-active={active ? "1" : undefined}>
      <button type="button" className="atlas-cluster__head" onClick={onToggle}>
        <span className="atlas-swatch" style={{ background: color }} />
        <span style={{ fontWeight: 500 }}>{cluster.label ? cap(cluster.label) : "Unlabelled"}</span>
        <span className="muted" style={{ fontSize: ".8rem", marginLeft: "auto" }}>{cluster.size}</span>
      </button>
      {active && (
        <div className="atlas-cluster__body">
          <label className="muted" style={{ fontSize: ".78rem", display: "block", marginBottom: ".3rem" }}>Call type</label>
          <div className="atlas-chips">
            {CALL_TYPES.map((t) => (
              <button key={t} type="button"
                className={`atlas-chip${label === t ? " is-on" : ""}`}
                disabled={saving}
                onClick={() => save(label === t ? "" : t)}>{cap(t)}</button>
            ))}
          </div>
          <div style={{ marginTop: ".7rem" }}>
            <span className="muted" style={{ fontSize: ".78rem", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
              <Volume2 size={13} /> Exemplars
            </span>
            {clips === null ? (
              <span className="muted" style={{ fontSize: ".8rem", display: "block", marginTop: ".3rem" }}>Loading…</span>
            ) : exemplars.length === 0 ? (
              <span className="muted" style={{ fontSize: ".8rem", display: "block", marginTop: ".3rem" }}>
                No saved clips for this cluster. Turn on Atlas clips in Settings to collect exemplars.
              </span>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem", marginTop: ".4rem" }}>
                {exemplars.slice(0, 12).map((c) => (
                  <span key={c.src} style={{ display: "inline-flex", alignItems: "center", gap: ".25rem" }}>
                    <ClipPlayButton src={c.src} title={`Play ${fmtTime(c.ts)}`} />
                  </span>
                ))}
              </div>
            )}
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
      <h3 className="atlas-h3"><Map size={16} /> Species</h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 .7rem" }}>
        Each point is one detection. Pick a species to label its call types.
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

function PointCard({ p, color }: { p: Point; color: string }) {
  return (
    <div className="card atlas-point-card">
      <div style={{ display: "flex", alignItems: "center", gap: ".4rem" }}>
        <span className="atlas-swatch" style={{ background: color }} />
        <Link to={`/apps/birds/bird?sci=${encodeURIComponent(p.sci)}`} style={{ fontWeight: 600, textDecoration: "none", color: "inherit" }}>
          {p.common || p.sci}
        </Link>
        {p.call_type && <StatusPill status="stopped">{cap(p.call_type)}</StatusPill>}
      </div>
      <div className="muted" style={{ fontSize: ".82rem", marginTop: ".3rem" }}>
        {fmtTime(p.ts)} · {conf(p.confidence)}
      </div>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
