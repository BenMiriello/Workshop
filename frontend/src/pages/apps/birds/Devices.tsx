import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, Smartphone, Radio } from "lucide-react";
import { useRecorder, type DeviceMode } from "./recorder";
import { API, jget } from "./ui";

// Devices that listen. Each is adaptive (follows its current GPS, so a phone's
// location moves with it and merges into nearby places) or fixed (pinned to one
// location, the way the Pi will be). The device you are on is highlighted and
// editing it updates this browser too.

type Dev = {
  token: string; name: string; mode: DeviceMode;
  fixed_location_id: number | null; fixed_location: string | null; last_seen: string | null;
};
type Loc = { id: number; name: string };

export default function Devices() {
  const rec = useRecorder();
  const [devices, setDevices] = useState<Dev[]>([]);
  const [locs, setLocs] = useState<Loc[]>([]);

  const load = useCallback(() => {
    jget<{ devices: Dev[] }>(`/devices`).then((d) => setDevices(d.devices)).catch(() => {});
    jget<{ locations: Loc[] }>(`/locations`).then((d) => setLocs(d.locations)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  // Push an update for an arbitrary device; for the current device also persist
  // to this browser via the recorder context so listening uses the new setting.
  const patch = useCallback((dev: Dev, fields: { name?: string; mode?: DeviceMode; fixed_location_id?: string }) => {
    if (dev.token === rec.device.token) {
      if (fields.name !== undefined) rec.setDeviceName(fields.name);
      if (fields.mode !== undefined) rec.setMode(fields.mode);
      if (fields.fixed_location_id !== undefined) rec.setFixedLocation(fields.fixed_location_id);
    }
    const fd = new FormData();
    fd.append("token", dev.token);
    if (fields.name !== undefined) fd.append("name", fields.name);
    if (fields.mode !== undefined) fd.append("mode", fields.mode);
    if (fields.fixed_location_id !== undefined) fd.append("fixed_location_id", fields.fixed_location_id || "0");
    fetch(`${API}/device`, { method: "POST", body: fd }).then(() => setTimeout(load, 200)).catch(() => {});
  }, [rec, load]);

  return (
    <div>
      <div style={{ marginBottom: ".75rem" }}>
        <Link to="/apps/birds/locations" className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Locations
        </Link>
      </div>
      <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
        <Smartphone size={22} strokeWidth={1.8} /> Devices
      </h2>

      <div style={{ display: "grid", gap: ".7rem" }}>
        {devices.map((dev) => {
          const current = dev.token === rec.device.token;
          return (
            <section key={dev.token} className="card" style={{ padding: "1rem", border: current ? "1px solid #1f6feb55" : undefined }}>
              <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".5rem" }}>
                <input className="num-in" defaultValue={dev.name} placeholder="Device name"
                  onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== dev.name) patch(dev, { name: v }); }}
                  style={{ flex: 1, minWidth: 0, fontWeight: 600 }} />
                {current && <span className="pill" style={{ flexShrink: 0 }}><Radio size={12} style={{ verticalAlign: "-2px" }} /> this device</span>}
              </div>

              <div style={{ display: "flex", gap: ".4rem", marginBottom: ".5rem" }}>
                {(["adaptive", "fixed"] as DeviceMode[]).map((m) => (
                  <button key={m} className="pill" onClick={() => patch(dev, { mode: m })}
                    style={{ cursor: "pointer", opacity: dev.mode === m ? 1 : 0.5,
                      background: dev.mode === m ? "#1f6feb" : undefined, color: dev.mode === m ? "#fff" : undefined,
                      borderColor: dev.mode === m ? "#1f6feb" : undefined }}>
                    {m === "adaptive" ? "Adaptive (follows GPS)" : "Fixed location"}
                  </button>
                ))}
              </div>

              {dev.mode === "fixed" && (
                <label className="muted" style={{ fontSize: ".8rem", display: "flex", flexDirection: "column", gap: ".2rem" }}>
                  Pinned location
                  <select className="num-in" value={dev.fixed_location_id ?? ""}
                    onChange={(e) => patch(dev, { fixed_location_id: e.target.value })}>
                    <option value="">— choose a location —</option>
                    {locs.map((L) => <option key={L.id} value={L.id}>{L.name}</option>)}
                  </select>
                </label>
              )}
              {dev.mode === "adaptive" && (
                <p className="muted" style={{ fontSize: ".78rem", margin: 0 }}>
                  Uses this device's current location each session; nearby sessions merge into one place.
                </p>
              )}
            </section>
          );
        })}
        {devices.length === 0 && <p className="muted">No devices yet. Start listening to register this one.</p>}
      </div>
    </div>
  );
}
