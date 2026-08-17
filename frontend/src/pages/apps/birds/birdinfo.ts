// Client-side bird reference info: a photo and a short description for a species,
// pulled straight from the BROWSER (which has normal internet) so the private
// backend never makes outbound calls. Sources:
//   - iNaturalist taxa API: photo + canonical common name + Wikipedia link
//   - Wikipedia REST summary: the description paragraph
// Both are CORS-enabled and need no key. Keyed only by public species name, so
// nothing private leaves the device. Results are cached in localStorage with a
// TTL; misses are negatively cached so we do not refetch every render.
import { useEffect, useState } from "react";
import { userStorage } from "../../../lib/storage";

export type BirdInfo = {
  scientific_name: string;
  common_name: string | null;
  photo: string | null;       // medium photo
  thumb: string | null;       // square thumbnail
  attribution: string | null;
  summary: string | null;     // description paragraph
  wikiUrl: string | null;
  found: boolean;
};

const CACHE_PREFIX = "birdinfo:";
const TTL_MS = 30 * 24 * 3600 * 1000; // 30 days
const MISS_TTL_MS = 3 * 24 * 3600 * 1000;

type Cached = { at: number; info: BirdInfo };

function readCache(sci: string): BirdInfo | null {
  try {
    const raw = userStorage.local.getItem(CACHE_PREFIX + sci);
    if (!raw) return null;
    const c = JSON.parse(raw) as Cached;
    const ttl = c.info.found ? TTL_MS : MISS_TTL_MS;
    if (Date.now() - c.at > ttl) return null;
    return c.info;
  } catch { return null; }
}

function writeCache(sci: string, info: BirdInfo) {
  userStorage.local.setItem(CACHE_PREFIX + sci, JSON.stringify({ at: Date.now(), info }));
}

function wikiTitle(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return decodeURIComponent(u.pathname.split("/wiki/")[1] || "");
  } catch { return null; }
}

async function fetchSummary(title: string): Promise<{ summary: string | null; thumb: string | null; url: string | null }> {
  try {
    const r = await fetch(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`);
    if (!r.ok) return { summary: null, thumb: null, url: null };
    const j = await r.json();
    return {
      summary: j.extract || null,
      thumb: j.thumbnail?.source || null,
      url: j.content_urls?.desktop?.page || null,
    };
  } catch { return { summary: null, thumb: null, url: null }; }
}

async function fetchInfo(sci: string, common: string | null): Promise<BirdInfo> {
  const base: BirdInfo = {
    scientific_name: sci, common_name: common, photo: null, thumb: null,
    attribution: null, summary: null, wikiUrl: null, found: false,
  };
  try {
    const q = encodeURIComponent(sci);
    const r = await fetch(`https://api.inaturalist.org/v1/taxa?q=${q}&rank=species&per_page=1`);
    if (!r.ok) return base;
    const j = await r.json();
    const t = j.results?.[0];
    if (!t) return base;
    const photo = t.default_photo || {};
    const info: BirdInfo = {
      scientific_name: sci,
      common_name: t.preferred_common_name || common,
      photo: photo.medium_url || null,
      thumb: photo.square_url || photo.medium_url || null,
      attribution: photo.attribution || null,
      summary: null,
      wikiUrl: t.wikipedia_url || null,
      found: true,
    };
    const title = wikiTitle(t.wikipedia_url) || (t.preferred_common_name || "").replace(/ /g, "_");
    if (title) {
      const s = await fetchSummary(title);
      info.summary = s.summary;
      if (!info.thumb) info.thumb = s.thumb;
      if (s.url) info.wikiUrl = s.url;
    }
    return info;
  } catch { return base; }
}

export async function getBirdInfo(sci: string, common: string | null): Promise<BirdInfo> {
  const cached = readCache(sci);
  if (cached) return cached;
  const info = await fetchInfo(sci, common);
  writeCache(sci, info);
  return info;
}

// React hook for one species. Non-birds (no real taxon) just resolve to not-found.
export function useBirdInfo(sci: string | null, common: string | null): BirdInfo | null {
  const [info, setInfo] = useState<BirdInfo | null>(sci ? readCache(sci) : null);
  useEffect(() => {
    let live = true;
    if (!sci) { setInfo(null); return; }
    const c = readCache(sci);
    if (c) { setInfo(c); return; }
    getBirdInfo(sci, common).then((i) => { if (live) setInfo(i); });
    return () => { live = false; };
  }, [sci, common]);
  return info;
}
