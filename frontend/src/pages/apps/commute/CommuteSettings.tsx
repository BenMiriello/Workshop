import { useCallback, useEffect, useState } from "react";
import { TrainFront } from "lucide-react";
import { StatusDot } from "@workshop/ui";
import { SettingsSection, SettingsRow as Row } from "../AppSettings";

// Commute app settings — 511 key status + home/work coordinates. Moved verbatim
// out of the platform Settings page (Phase 5 settings split). Renders inside the
// Commute page's <AppSettings> slot.

type ServiceProbe = {
  name: string;
  category?: string;
  status: string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};

export default function CommuteSettings() {
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [schedule, setSchedule] = useState<{ home?: { lat?: number | null; lon?: number | null; address?: string }; work?: { lat?: number | null; lon?: number | null; address?: string } } | null>(null);

  const loadSchedule = useCallback(async () => {
    try {
      const r = await fetch("/api/commute/schedule");
      const j = r.ok ? await r.json() : null;
      setSchedule(j);
    } catch {
      setSchedule(null);
    }
  }, []);

  useEffect(() => {
    fetch("/api/services/").then((r) => r.ok ? r.json() : { services: [] }).then((j) => setServices(j.services || []));
    loadSchedule();
  }, [loadSchedule]);

  const realtime = services?.find((s) => s.name?.includes("caltrain.gtfs_rt")) || services?.find((s) => s.name?.includes("511"));

  return (
    <SettingsSection icon={TrainFront} title="Commute">
      <Row label="config">
        <a href="/apps/commute" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Open Commute · Config tab ↗
        </a>
      </Row>
      <Row label="511 API key">
        {realtime?.status === "ok" ? (
          <StatusDot status="ok">configured · transit realtime is on</StatusDot>
        ) : (
          <span style={{ color: "var(--danger)" }}>
            missing · get a free key at <a href="https://511.org/open-data/token" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>511.org</a>{" "}
            to turn on transit realtime
          </span>
        )}
      </Row>
      <CoordsRow label="home coords" name="home" entry={schedule?.home} onSaved={loadSchedule} />
      <CoordsRow label="work coords" name="work" entry={schedule?.work} onSaved={loadSchedule} />
    </SettingsSection>
  );
}

function CoordsRow({
  label,
  name,
  entry,
  onSaved,
}: {
  label: string;
  name: "home" | "work";
  entry: { lat?: number | null; lon?: number | null; address?: string } | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isSet = entry?.lat != null && entry?.lon != null;

  const openEdit = () => {
    setLat(entry?.lat != null ? String(entry.lat) : "");
    setLon(entry?.lon != null ? String(entry.lon) : "");
    setErr(null);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const r = await fetch("/api/commute/coords", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, lat: parseFloat(lat), lon: parseFloat(lon) }),
      });
      if (!r.ok) throw new Error(await r.text());
      setEditing(false);
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Row label={label}>
      {editing ? (
        <span style={{ display: "inline-flex", gap: "0.3rem", flexWrap: "wrap", alignItems: "center" }}>
          <input
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            placeholder="lat"
            style={{ width: "9ch", padding: "0.2rem 0.4rem", fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}
          />
          <input
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            placeholder="lon"
            style={{ width: "10ch", padding: "0.2rem 0.4rem", fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}
          />
          <button onClick={save} disabled={saving} style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}>
            {saving ? "…" : "save"}
          </button>
          <button onClick={() => setEditing(false)} type="button" style={{ fontSize: "0.8rem", padding: "0.2rem 0.5rem" }}>
            cancel
          </button>
          {err && <span style={{ color: "var(--err)", fontSize: "0.78rem" }}>{err}</span>}
        </span>
      ) : (
        <span>
          {isSet ? (
            <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
              {Number(entry!.lat).toFixed(5)}, {Number(entry!.lon).toFixed(5)}
            </span>
          ) : (
            <span style={{ color: "var(--err)", fontSize: "0.85rem" }}>
              not set · location-aware commute disabled
            </span>
          )}
          <button onClick={openEdit} style={{ marginLeft: "0.5rem", fontSize: "0.78rem", padding: "0.2rem 0.5rem" }}>
            {isSet ? "edit" : "set"}
          </button>
          {entry?.address && (
            <div className="muted" style={{ fontSize: "0.78rem", marginTop: "0.2rem" }}>{entry.address}</div>
          )}
        </span>
      )}
    </Row>
  );
}
