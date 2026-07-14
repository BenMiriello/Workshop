import { useEffect, useRef, useState } from "react";
import maplibregl, { type StyleSpecification } from "maplibre-gl";
import { Protocol } from "pmtiles";
import layers from "protomaps-themes-base";
import "maplibre-gl/dist/maplibre-gl.css";

// Map primitive — MapLibre GL with a self-hosted Protomaps PMTiles basemap.
// Privacy: the basemap is one local file (no per-tile callouts to a third
// party); only generic glyph fonts are fetched from the Protomaps CDN.
//
// Degrades gracefully: if the basemap file isn't present yet, the map still
// renders the track + marker on a plain background and surfaces a hint, so the
// feature is useful before the (operator-installed) .pmtiles is in place.
//
// Reusable: pass a `track` (one or more breadcrumb segments) and/or a `marker`
// (current position). Each segment is drawn as its own polyline (MultiLineString)
// so callers can break the line across time/teleport gaps instead of letting the
// map connect distant fixes with a long straight line. Colors are read from
// design tokens at runtime so it matches theme.

const BASEMAP_URL = "/basemap/bayarea.pmtiles";
const GLYPHS = "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf";

let protocolRegistered = false;
function ensureProtocol() {
  if (protocolRegistered) return;
  maplibregl.addProtocol("pmtiles", new Protocol().tile);
  protocolRegistered = true;
}

function resolveTheme(): "light" | "dark" {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light") return "light";
  if (attr === "dark") return "dark";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function token(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function buildStyle(theme: "light" | "dark", hasBasemap: boolean): StyleSpecification {
  const bg = token("--bg", theme === "dark" ? "#12121a" : "#f4f4f7");
  if (!hasBasemap) {
    return { version: 8, glyphs: GLYPHS, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": bg } }] };
  }
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: { protomaps: { type: "vector", url: `pmtiles://${BASEMAP_URL}` } },
    layers: layers("protomaps", theme, "en"),
  };
}

export type LngLat = [number, number]; // [lng, lat]

export function Map({
  track,
  marker,
  height = 360,
  className,
}: {
  track?: LngLat[][]; // breadcrumb broken into gap-free segments
  marker?: LngLat | null;
  height?: number | string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  // Camera is only positioned automatically ONCE — focus on the current location
  // (or the track if there's no marker yet). After that we leave it alone so live
  // polling (new track/marker refs every few seconds) can't keep yanking the view
  // back to a wide fit-all while the operator is panning/zooming.
  const didFocusRef = useRef(false);
  const didInitRef = useRef(false);
  const [hasBasemap, setHasBasemap] = useState<boolean | null>(null);
  const [mapReady, setMapReady] = useState(false);

  // Probe for the basemap file once.
  useEffect(() => {
    let alive = true;
    fetch(BASEMAP_URL, { method: "HEAD" })
      .then((r) => {
        // The dev server's SPA fallback returns index.html (200, text/html) for
        // a missing file, so a bare r.ok would be a false positive. A real
        // .pmtiles is served as a binary type — reject the HTML fallback.
        const ct = (r.headers.get("content-type") || "").toLowerCase();
        if (alive) setHasBasemap(r.ok && !ct.includes("text/html"));
      })
      .catch(() => { if (alive) setHasBasemap(false); });
    return () => { alive = false; };
  }, []);

  // Init the map once we know whether the basemap exists.
  useEffect(() => {
    if (hasBasemap === null || !containerRef.current || mapRef.current) return;
    ensureProtocol();
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(resolveTheme(), hasBasemap),
      center: marker ?? track?.flat().at(-1) ?? [-122.08, 37.39],
      zoom: 11,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    // Fresh map => re-arm the one-time auto-focus.
    didFocusRef.current = false;
    didInitRef.current = false;
    setMapReady(true);
    return () => { map.remove(); mapRef.current = null; setMapReady(false); };
  }, [hasBasemap]); // eslint-disable-line react-hooks/exhaustive-deps

  // Sync the track polyline + marker + bounds whenever inputs change.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const accent = token("--accent", "#7a57e0");

    // Marker is a DOM overlay — add it IMMEDIATELY, independent of the style.
    // (Parsing a large basemap takes seconds; the pin shouldn't wait on it.)
    if (marker) {
      if (markerRef.current) markerRef.current.setLngLat(marker);
      else markerRef.current = new maplibregl.Marker({ color: accent }).setLngLat(marker).addTo(map);
    } else if (markerRef.current) {
      markerRef.current.remove();
      markerRef.current = null;
    }

    // The breadcrumb line + camera fit touch the style (sources/layers), so they
    // do need to wait for it.
    const applyStyleBits = () => {
      // MultiLineString: each segment is its own polyline, so the map never
      // joins two segments separated by a gap with a straight line.
      const segs = (track ?? []).filter((s) => s.length > 1);
      const line: GeoJSON.Feature = {
        type: "Feature", properties: {},
        geometry: { type: "MultiLineString", coordinates: segs },
      };
      const src = map.getSource("track") as maplibregl.GeoJSONSource | undefined;
      if (src) {
        src.setData(line);
      } else if (segs.length) {
        map.addSource("track", { type: "geojson", data: line });
        map.addLayer({ id: "track", type: "line", source: "track",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": accent, "line-width": 3, "line-opacity": 0.85 } });
      }
      // Position the camera at most once. Prefer the current location (marker);
      // if it isn't available yet, fit the track so the view isn't empty, but
      // don't lock focus — switch to the marker as soon as it arrives. After
      // focusing, never move the camera again (operator stays in control).
      if (marker && !didFocusRef.current) {
        map.easeTo({ center: marker, zoom: 14, duration: 0 });
        didFocusRef.current = true;
        didInitRef.current = true;
      } else if (!marker && !didInitRef.current) {
        const pts = [...(track ?? []).flat()];
        if (pts.length === 1) {
          map.easeTo({ center: pts[0], zoom: 14, duration: 0 });
          didInitRef.current = true;
        } else if (pts.length > 1) {
          const b = pts.reduce((acc, p) => acc.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
          map.fitBounds(b, { padding: 48, maxZoom: 15, duration: 0 });
          didInitRef.current = true;
        }
      }
    };
    if (map.isStyleLoaded()) applyStyleBits();
    else map.once("load", applyStyleBits);
  }, [track, marker, mapReady]);

  return (
    <div className={className} style={{ position: "relative" }}>
      <div ref={containerRef} style={{ height, width: "100%", borderRadius: "var(--radius-md)", overflow: "hidden" }} />
      {hasBasemap === false && (
        <p className="ws-meta" style={{ marginTop: "var(--space-2)", color: "var(--muted)", fontSize: "var(--fs-xs)" }}>
          Showing points only. Street basemap not installed yet (drop a Protomaps <code>.pmtiles</code> at <code>public/basemap/bayarea.pmtiles</code>).
        </p>
      )}
    </div>
  );
}
