import { useCallback, useEffect, useState } from "react";
import { Pin, PinOff, Trash2, Save, MapPin } from "lucide-react";
import { useCrumbTrail, type Crumb } from "../../../components/appNav";
import { Segmented, Stepper, ConfirmDialog } from "@workshop/ui";
import { API, jget, pct, SpeciesLink, BirdThumb } from "./ui";
import { useRecStatus, fmtBytes, PlayButton } from "./recordings";
import BirdsHeader from "./BirdsHeader";

// Recordings management: the storage meter, the budget settings (cap + what to do
// when full), and the full list of saved clips with per-clip pin / delete. This
// is where the homepage banner links to.

type Rec = {
  id: number; scientific_name: string; common_name: string;
  location_id: number | null; location: string | null; at: string;
  bytes: number; confidence: number; pinned: boolean; protected: boolean;
};
type RecSettings = { rec_storage_mb: number; rec_when_full: "evict_old" | "stop" };

export default function Recordings() {
  useCrumbTrail([
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
    { label: "Recordings" },
  ] as Crumb[]);

  const status = useRecStatus(8000);
  const [items, setItems] = useState<Rec[] | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Rec | null>(null);

  const load = useCallback(() => {
    jget<{ items: Rec[]; total: number }>("/recordings?limit=300")
      .then((d) => { setItems(d.items); setTotal(d.total); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 12000); return () => clearInterval(t); }, [load]);

  const pin = (r: Rec) => {
    const fd = new FormData(); fd.append("pinned", String(!r.pinned));
    fetch(`${API}/recordings/${r.id}/pin`, { method: "POST", body: fd })
      .then(() => { load(); window.dispatchEvent(new CustomEvent("bird-settings-change")); });
  };
  const del = (r: Rec) => {
    fetch(`${API}/recordings/${r.id}`, { method: "DELETE" })
      .then(() => { setConfirm(null); load(); window.dispatchEvent(new CustomEvent("bird-settings-change")); });
  };

  return (
    <div>
      <BirdsHeader title="Recordings" />

      {status && (
        <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
          <UsageMeter status={status} />
        </section>
      )}

      <RecordingSettings onSaved={() => window.dispatchEvent(new CustomEvent("bird-settings-change"))} />

      <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: "0 0 .6rem", fontSize: "1rem" }}>
          Saved clips {total > 0 && <span className="muted" style={{ fontWeight: 400 }}>({total})</span>}
        </h3>

        {err && !items && <p className="error">Could not load: {err}</p>}
        {!items && !err && <p className="muted">Loading…</p>}
        {items && items.length === 0 && (
          <p className="muted" style={{ fontSize: ".88rem" }}>
            No recordings yet. Turn on recording for a bird from its page to start saving the
            audio of its detections.
          </p>
        )}

        {items && items.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {items.map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".5rem 0", borderTop: "1px solid var(--border)" }}>
                <PlayButton recId={r.id} />
                <SpeciesLink sci={r.scientific_name} locationId={r.location_id} style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: ".6rem" }}>
                    <BirdThumb sci={r.scientific_name} common={r.common_name} size={36} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: ".9rem" }}>{r.common_name}</span>
                      <span className="muted" style={{ fontSize: ".75rem", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
                        {r.location && <><MapPin size={10} /> {r.location} · </>}{r.at}
                      </span>
                    </span>
                  </div>
                </SpeciesLink>
                <span className="pill neutral" style={{ flexShrink: 0 }}>{pct(r.confidence)}</span>
                <span className="muted" style={{ fontSize: ".75rem", minWidth: 56, textAlign: "right", flexShrink: 0 }}>{fmtBytes(r.bytes)}</span>
                <button type="button" className={`bird-act${r.pinned ? " ok" : ""}`} onClick={() => pin(r)}
                  title={r.pinned ? "Pinned. Protected from auto-cleanup. Click to unpin." : "Pin to keep this recording (deleted last)"}>
                  {r.pinned ? <Pin size={15} /> : <PinOff size={15} />}
                </button>
                <button type="button" className="bird-act danger" onClick={() => setConfirm(r)} title="Delete this recording">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={!!confirm}
        title="Delete recording?"
        body={confirm ? `Permanently delete this ${confirm.common_name} clip from ${confirm.at}. This cannot be undone.` : ""}
        confirmLabel="Delete"
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && del(confirm)}
      />
    </div>
  );
}

// Used / cap bar with the protected portion marked. Color escalates with state.
function UsageMeter({ status }: { status: ReturnType<typeof useRecStatus> }) {
  if (!status) return null;
  const usedFrac = Math.min(1, status.cap_bytes ? status.used_bytes / status.cap_bytes : 0);
  const protFrac = Math.min(usedFrac, status.cap_bytes ? status.protected_bytes / status.cap_bytes : 0);
  const barColor = status.state === "error" ? "var(--danger)" : status.state === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: ".4rem" }}>
        <span style={{ fontWeight: 500 }}>Storage used</span>
        <span className="muted" style={{ fontSize: ".85rem" }}>
          {fmtBytes(status.used_bytes)} of {fmtBytes(status.cap_bytes)} · {status.count} clip{status.count === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ position: "relative", height: 10, borderRadius: 6, background: "var(--bg-inset)", overflow: "hidden" }}>
        <div style={{ position: "absolute", inset: 0, width: `${usedFrac * 100}%`, background: barColor, opacity: 0.85 }} />
        {protFrac > 0 && (
          <div title="Protected (pinned or 'save all'). Not auto-removed"
            style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${protFrac * 100}%`, background: "var(--accent)", opacity: 0.55 }} />
        )}
      </div>
      <div className="muted" style={{ fontSize: ".75rem", marginTop: ".4rem" }}>
        {status.protected_bytes > 0 && <>Protected: {fmtBytes(status.protected_bytes)} · </>}
        When full: {status.when_full === "stop" ? "stop saving new recordings" : "remove oldest recordings"}
      </div>
      {status.state !== "ok" && status.message && (
        <p className="muted" style={{ fontSize: ".82rem", marginTop: ".5rem", marginBottom: 0, color: status.state === "error" ? "var(--danger)" : "var(--warn)" }}>
          {status.message}
        </p>
      )}
    </div>
  );
}

// Budget config — a config write, so it commits on an explicit Save (design rule 7).
function RecordingSettings({ onSaved }: { onSaved: () => void }) {
  const [base, setBase] = useState<RecSettings | null>(null);
  const [draft, setDraft] = useState<RecSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    jget<{ settings: RecSettings }>("/recordings/settings")
      .then((d) => { setBase(d.settings); setDraft(d.settings); }).catch(() => {});
  }, []);

  const dirty = !!base && !!draft && (base.rec_storage_mb !== draft.rec_storage_mb || base.rec_when_full !== draft.rec_when_full);
  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    const fd = new FormData();
    fd.append("rec_storage_mb", String(draft.rec_storage_mb));
    fd.append("rec_when_full", draft.rec_when_full);
    try {
      const r = await fetch(`${API}/recordings/settings`, { method: "POST", body: fd });
      const d = await r.json();
      setBase(d.settings); setDraft(d.settings); onSaved();
    } finally { setSaving(false); }
  }, [draft, onSaved]);

  if (!draft) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .8rem", fontSize: "1rem" }}>Storage settings</h3>
      <div style={{ display: "grid", gap: "1.1rem" }}>
        <div>
          <div style={{ fontWeight: 500, marginBottom: ".35rem" }}>Total storage limit</div>
          <Stepper value={draft.rec_storage_mb} onChange={(v) => setDraft({ ...draft, rec_storage_mb: v })}
            min={50} max={20000} step={250} suffix="MB" ariaLabel="Storage limit in MB" />
          <div className="muted" style={{ fontSize: ".78rem", marginTop: ".35rem" }}>
            How much disk saved clips may use, across all birds. About {Math.round(draft.rec_storage_mb / 0.024)} clips at this size.
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 500, marginBottom: ".35rem" }}>When full</div>
          <Segmented<"evict_old" | "stop">
            ariaLabel="What to do when storage is full"
            value={draft.rec_when_full}
            onChange={(v) => setDraft({ ...draft, rec_when_full: v })}
            options={[
              { value: "evict_old", label: "Remove oldest" },
              { value: "stop", label: "Stop saving" },
            ]}
          />
          <div className="muted" style={{ fontSize: ".78rem", marginTop: ".5rem" }}>
            {draft.rec_when_full === "evict_old"
              ? "Automatically delete the oldest recordings to make room. Pinned and 'save all' recordings are never removed."
              : "Stop saving new recordings once full, keeping everything you have. You'll see a warning when this happens."}
          </div>
        </div>
      </div>
      <div style={{ marginTop: "1rem" }}>
        <button className="btn" onClick={save} disabled={!dirty || saving}>
          <Save size={15} /> {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </section>
  );
}
