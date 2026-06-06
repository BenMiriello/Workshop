import { useEffect, useRef, useState } from "react";
import { Search, Crosshair, MapPin, Loader2 } from "lucide-react";
import { searchAddress, reverseGeocode, type Place } from "./geocode";

// Verified-address picker: type to autocomplete real street addresses, or use
// the device's current GPS (reverse-geocoded). Calls onPick with a real Place
// (street label + coordinates). This is the ONLY way to give a location a place;
// there is no free-text path, so a location can never be a made-up name.

export default function AddressPicker({ onPick, value }: { onPick: (p: Place) => void; value?: Place | null }) {
  const [q, setQ] = useState(value?.short || "");
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Place | null>(value || null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!q.trim() || q === picked?.short) { setResults([]); return; }
    timer.current = window.setTimeout(async () => {
      setBusy(true);
      const rs = await searchAddress(q);
      setResults(rs); setOpen(true); setBusy(false);
    }, 400);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q, picked]);

  const choose = (p: Place) => { setPicked(p); setQ(p.short); setResults([]); setOpen(false); onPick(p); };

  const useCurrent = () => {
    if (!navigator.geolocation) return;
    setBusy(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const p = await reverseGeocode(pos.coords.latitude, pos.coords.longitude);
        setBusy(false);
        if (p) choose(p);
      },
      () => setBusy(false),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  };

  return (
    <div style={{ position: "relative" }}>
      <div style={{ display: "flex", gap: ".4rem" }}>
        <div style={{ position: "relative", flex: 1 }}>
          <Search size={14} style={{ position: "absolute", left: 8, top: 9, opacity: 0.5 }} />
          <input className="num-in" placeholder="Search a street address" value={q}
            onChange={(e) => { setQ(e.target.value); setPicked(null); }}
            onFocus={() => results.length && setOpen(true)}
            style={{ paddingLeft: 26 }} />
        </div>
        <button className="pill" onClick={useCurrent}
          style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem", flexShrink: 0 }}>
          {busy ? <Loader2 size={14} className="spin" /> : <Crosshair size={14} />} Current
        </button>
      </div>

      {picked && (
        <div className="muted" style={{ fontSize: ".75rem", marginTop: ".3rem", display: "flex", alignItems: "center", gap: ".25rem" }}>
          <MapPin size={12} /> {picked.label || picked.short} ({picked.lat.toFixed(5)}, {picked.lon.toFixed(5)})
        </div>
      )}

      {open && results.length > 0 && (
        <div className="card" style={{ position: "absolute", zIndex: 40, top: "100%", left: 0, right: 0,
          marginTop: 4, maxHeight: 260, overflowY: "auto", padding: ".3rem" }}>
          {results.map((p, i) => (
            <button key={i} onClick={() => choose(p)}
              style={{ display: "block", width: "100%", textAlign: "left", background: "none",
                border: "none", color: "inherit", cursor: "pointer", padding: ".4rem .5rem", borderRadius: 6 }}>
              <div style={{ fontSize: ".88rem" }}>{p.short}</div>
              <div className="muted" style={{ fontSize: ".72rem" }}>{p.label}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
