// Address search + reverse geocoding for the location picker, done CLIENT-SIDE
// against Photon (komoot's OpenStreetMap geocoder: CORS-enabled, no key). A
// location must be a real verified place - a picked street address or a GPS
// point - never a free-typed string or a device name.

export type Place = { short: string; label: string; lat: number; lon: number };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function format(f: any): { short: string; label: string } {
  const p = f.properties || {};
  const city = p.city || p.town || p.village || p.county;
  const line1 = [p.housenumber, p.street || p.name].filter(Boolean).join(" ");
  const short = [line1, city].filter(Boolean).join(", ") || p.name || "Unknown place";
  const label = [short, p.state, p.postcode, p.country].filter(Boolean).join(", ");
  return { short, label };
}

export async function searchAddress(q: string): Promise<Place[]> {
  if (!q.trim()) return [];
  try {
    const r = await fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=6`);
    if (!r.ok) return [];
    const j = await r.json();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (j.features || []).map((f: any) => {
      const { short, label } = format(f);
      const [lon, lat] = f.geometry.coordinates;
      return { short, label, lat, lon };
    });
  } catch { return []; }
}

export async function reverseGeocode(lat: number, lon: number): Promise<Place | null> {
  try {
    const r = await fetch(`https://photon.komoot.io/reverse?lat=${lat}&lon=${lon}`);
    if (!r.ok) return { short: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, label: "", lat, lon };
    const j = await r.json();
    const f = j.features?.[0];
    if (!f) return { short: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, label: "", lat, lon };
    const { short, label } = format(f);
    return { short, label, lat, lon };
  } catch { return { short: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, label: "", lat, lon }; }
}
