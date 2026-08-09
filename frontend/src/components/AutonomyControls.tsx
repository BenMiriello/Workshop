import { useCallback, useEffect, useState } from "react";
import { Power, Cpu } from "lucide-react";
import { Button, ConfirmDialog, StatusDot } from "../ui";
import { SettingsSection, SettingsRow as Row } from "./AppSettings";

// Autonomy & GPU controls. The global cron-autonomy pause (AUTONOMY_DISABLED)
// and Ollama/GPU broker controls. Moved out of the platform Settings page
// (Phase 5) onto the Agents surface, where the fleet it governs lives. The
// AUTONOMY_DISABLED switch is operationally load-bearing and must stay
// reachable — this component is that home.
//
// Two independent off-switches:
//   - Cron autonomy (the Ralph loop, via the AUTONOMY_DISABLED flag file)
//   - Ollama GPU residency (unload resident models to free VRAM; Ollama keeps
//     running, this only evicts the resident model)
// Plus a single "Stop all" that disables the cron loop and frees the GPU.

type AutonomyStatus = {
  cron_autonomy: {
    disabled: boolean;
    disabled_at: string | null;
    flag_path: string;
  };
  // 2026-07-18 canonical off-switch: JSON overlay at .runtime/autonomy-override.json.
  // The `disabled` boolean above is truthy if EITHER the legacy AUTONOMY_DISABLED
  // flag file OR the overlay says engaged, so the row below stays a single truth
  // for the operator; the toggle only ever writes to the overlay endpoint.
  override?: {
    engaged: boolean;
    reason?: string | null;
    engaged_at?: string | null;
    engaged_by?: string | null;
  };
  queue_runner?: {
    running: boolean;
    pid: number | null;
    enabled: boolean;
  };
};

type LoadedModel = {
  name: string;
  size_vram_bytes: number | null;
  expires_at: string | null;
};

type OllamaStatus = {
  ollama_up: boolean;
  loaded_models: LoadedModel[];
  gpu: {
    available: boolean;
    memory_used_mib?: number;
    memory_free_mib?: number;
    memory_total_mib?: number;
    utilization_pct?: number;
    error?: string;
  };
};

const fmtMiB = (m: number | undefined) =>
  m == null ? "–" : m >= 1024 ? `${(m / 1024).toFixed(1)} GiB` : `${m} MiB`;

const fmtBytes = (b: number | null) => {
  if (b == null) return "–";
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(1)} GiB`;
  if (b >= 1024 ** 2) return `${(b / 1024 ** 2).toFixed(0)} MiB`;
  return `${b} B`;
};

type BrokerLease = {
  id: string;
  app: string;
  vram_budget_mib: number;
  vram_actual_mib: number;
  priority: number;
  state: string;
  reason: string;
  implicit: boolean;
};

type BrokerStatus = {
  available: boolean;
  mode?: string;
  total_vram_mib?: number;
  committed_mib?: number;
  free_budget_mib?: number;
  active_lease_count?: number;
  queue_depth?: number;
  my_leases?: BrokerLease[];
};

export default function AutonomyControls() {
  const [auto, setAuto] = useState<AutonomyStatus | null>(null);
  const [oll, setOll] = useState<OllamaStatus | null>(null);
  const [bro, setBro] = useState<BrokerStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStopAll, setConfirmStopAll] = useState(false);
  const [confirmUnload, setConfirmUnload] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, o, b] = await Promise.all([
        fetch("/api/agents/autonomy/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/ollama/status").then((r) => r.ok ? r.json() : null),
        fetch("/api/gpu/broker/status").then((r) => r.ok ? r.json() : { available: false }),
      ]);
      setAuto(a);
      setOll(o);
      setBro(b);
    } catch {
      // keep prior state
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  const cronDisabled = !!auto?.cron_autonomy.disabled;
  const runnerRunning = !!auto?.queue_runner?.running;
  const allStopped = cronDisabled;

  const onStopAll = async () => {
    setBusy("stop-all");
    try {
      // Engage the override FIRST so nothing reloads a model right after we
      // unload it. The override is the canonical off-switch (autonomy-model
      // 2026-07-18 §1.2); the legacy AUTONOMY_DISABLED flag is preserved but
      // not touched from the UI.
      await fetch("/api/agents/autonomy/override", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: "Stop all" }),
      });
      await fetch("/api/ollama/unload", { method: "POST" });
      await load();
    } finally {
      setBusy(null);
      setConfirmStopAll(false);
    }
  };

  const onStartAll = async () => {
    setBusy("start-all");
    try {
      await fetch("/api/agents/autonomy/override", { method: "DELETE" });
      await load();
    } finally {
      setBusy(null);
    }
  };

  const onUnload = async () => {
    setBusy("unload");
    try {
      await fetch("/api/ollama/unload", { method: "POST" });
      await load();
    } finally {
      setBusy(null);
      setConfirmUnload(false);
    }
  };

  return (
    <SettingsSection icon={Power} title="Autonomy & GPU">
      <Row label="all autonomy">
        {auto == null ? "loading…" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status={allStopped ? "stopped" : "running"}>
              {allStopped ? "stopped" : "running"}
            </StatusDot>
            {allStopped ? (
              <Button size="sm" variant="primary" loading={busy === "start-all"} disabled={busy != null} onClick={onStartAll}>
                Start all
              </Button>
            ) : (
              <Button size="sm" variant="danger-ghost" disabled={busy != null} onClick={() => setConfirmStopAll(true)}>
                <Power aria-hidden /> Stop all
              </Button>
            )}
          </span>
        )}
      </Row>

      <Row label="cron loop">
        {auto == null ? "–" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            {/* Three real states, never "enabled" on the strength of a missing
                flag file alone: work is permitted AND a worker is resident, or
                it is permitted with nothing running (tickets pile up), or it is
                switched off. The middle case is the one that used to read as
                healthy while nothing ran. */}
            <StatusDot
              status={cronDisabled ? "stopped" : runnerRunning ? "running" : "warn"}
            >
              {cronDisabled
                ? "disabled"
                : runnerRunning
                  ? "running (draining the queue)"
                  : "permitted, but nothing is running. Tickets will queue"}
            </StatusDot>
            <Button
              size="sm"
              variant={cronDisabled ? "default" : "danger-ghost"}
              disabled={busy != null}
              onClick={async () => {
                // Legacy AUTONOMY_DISABLED (if present) stays for the shell-
                // script maintenance convention; the operator surface only ever
                // writes the JSON overlay.
                setBusy(cronDisabled ? "enable" : "disable");
                try {
                  if (cronDisabled) {
                    await fetch("/api/agents/autonomy/override", { method: "DELETE" });
                  } else {
                    await fetch("/api/agents/autonomy/override", {
                      method: "POST",
                      headers: { "content-type": "application/json" },
                      body: JSON.stringify({ reason: "Disabled from Settings" }),
                    });
                  }
                  await load();
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === "enable" ? "enabling…" : busy === "disable" ? "disabling…" : cronDisabled ? "Enable" : "Disable"}
            </Button>
          </span>
        )}
      </Row>

      <Row label="gpu memory">
        {oll == null || !oll.gpu.available ? (
          oll?.gpu.error ? <span className="ws-muted">no GPU detected</span> : "loading…"
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
              {fmtMiB(oll.gpu.memory_used_mib)} / {fmtMiB(oll.gpu.memory_total_mib)}
              {" · "}{oll.gpu.utilization_pct}% util
            </span>
            <Button
              size="sm"
              variant="danger-ghost"
              disabled={busy != null || !oll.ollama_up || oll.loaded_models.length === 0}
              onClick={() => setConfirmUnload(true)}
            >
              {busy === "unload" ? "unloading…" : "Free GPU"}
            </Button>
          </span>
        )}
      </Row>

      <Row label="broker">
        {bro == null ? "–" : !bro.available ? (
          <span className="ws-muted">offline · Workshop has the GPU to itself</span>
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <StatusDot status="ok">up</StatusDot>
            <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
              mode={bro.mode} · committed {fmtMiB(bro.committed_mib)}/{fmtMiB(bro.total_vram_mib)}
              {" · "}q={bro.queue_depth} · {bro.active_lease_count} lease{bro.active_lease_count === 1 ? "" : "s"}
            </span>
          </span>
        )}
      </Row>

      <ConfirmDialog
        open={confirmStopAll}
        onCancel={() => setConfirmStopAll(false)}
        onConfirm={onStopAll}
        title="Stop all autonomy and free GPU?"
        body="Stops scheduled work and frees the GPU. Starting it again reloads the models, which takes about 20 seconds."
        confirmLabel="Stop all"
        busy={busy === "stop-all"}
      />
      <ConfirmDialog
        open={confirmUnload}
        onCancel={() => setConfirmUnload(false)}
        onConfirm={onUnload}
        title="Free GPU memory?"
        body="Frees the GPU by unloading the local models. The next run reloads them, which takes about 20 seconds."
        confirmLabel="Free GPU"
        busy={busy === "unload"}
      />

      {bro?.available && (
        <Row label="my leases">
          {bro.my_leases && bro.my_leases.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
              {bro.my_leases.map((l) => (
                <span key={l.id} style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem" }}>
                  {l.app} · {fmtMiB(l.vram_budget_mib)} budget · {fmtMiB(l.vram_actual_mib)} actual · pri {l.priority}
                  {l.implicit ? " · implicit" : ""}
                </span>
              ))}
              <span>
                <button
                  onClick={async () => {
                    setBusy("release-mine");
                    try {
                      await fetch("/api/gpu/broker/release-mine", { method: "POST" });
                      await load();
                    } finally { setBusy(null); }
                  }}
                  disabled={busy != null}
                  style={{ padding: "0.2rem 0.6rem", fontSize: "0.78rem", color: "var(--err)" }}
                >
                  {busy === "release-mine" ? "releasing…" : "release all mine"}
                </button>
              </span>
            </div>
          ) : (
            <span className="muted">none</span>
          )}
        </Row>
      )}

      {bro?.available && (
        <Row label="operator hold">
          <button
            onClick={async () => {
              if (!confirm("Reserve the GPU for yourself for 30 minutes? Workshop and other apps lose it.")) return;
              setBusy("reserve");
              try {
                await fetch("/api/gpu/broker/reserve", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ duration_s: 1800, reason: "operator hold via UI" }),
                });
                await load();
              } finally { setBusy(null); }
            }}
            disabled={busy != null}
            style={{ padding: "0.2rem 0.6rem", fontSize: "0.78rem" }}
          >
            {busy === "reserve" ? "reserving…" : "reserve for me (30m)"}
          </button>
          <button
            onClick={async () => {
              setBusy("boost");
              try {
                await fetch("/api/gpu/broker/boost", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ priority: 80, ttl_s: 1800 }),
                });
                await load();
              } finally { setBusy(null); }
            }}
            disabled={busy != null}
            style={{ marginLeft: "0.5rem", padding: "0.2rem 0.6rem", fontSize: "0.78rem" }}
          >
            {busy === "boost" ? "boosting…" : "boost workshop priority (30m)"}
          </button>
        </Row>
      )}

      {oll && oll.loaded_models.length > 0 && (
        <Row label="loaded">
          <div style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
            {oll.loaded_models.map((m) => (
              <span key={m.name} style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                <Cpu size={11} strokeWidth={1.8} style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                {m.name} · {fmtBytes(m.size_vram_bytes)}
              </span>
            ))}
          </div>
        </Row>
      )}
    </SettingsSection>
  );
}
