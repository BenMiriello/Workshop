import { useCallback, useEffect, useState } from "react";
import { usePoll } from "../lib/usePoll";
import { Power, Cpu, Play, Square } from "lucide-react";
import { Button, ConfirmDialog, Notice, Select, StatusDot, Toggle } from "@workshop/ui";
import { describeAutonomy } from "../lib/autonomy";
import { formatMiB, formatVramBytes } from "../lib/bytes";
import { SettingsSection, SettingsRow as Row } from "./AppSettings";

// Autonomy & GPU controls. The global cron-autonomy pause (AUTONOMY_DISABLED)
// and Ollama/GPU broker controls. Moved out of the platform Settings page
// (Phase 5) onto the Agents surface, where the fleet it governs lives. The
// AUTONOMY_DISABLED switch is operationally load-bearing and must stay
// reachable — this component is that home.
//
// Three independent controls, and keeping them independent is the point:
//   - The BRAKE: whether unattended work is permitted at all. A policy, so a
//     Toggle. Instant and reversible, so no dialog.
//   - The RUNNER: a resident worker process in one named tree. A process, so a
//     Button plus the tree to run it in. Stopping it gets a dialog.
//   - Ollama GPU residency (unload resident models to free VRAM; Ollama keeps
//     running, this only evicts the resident model).
// Plus a single "Stop all" that does all three at once, which is the one place
// they are allowed to move together because the dialog says so.

type AutonomyStatus = {
  cron_autonomy: {
    disabled: boolean;
    disabled_at: string | null;
    // Optional because the server STOPPED SENDING IT. `autonomy_status` reads
    // only `autonomy_override.read()` now; there is no second flag file to
    // report a path for. It was declared required here, so the type asserted a
    // field that never arrives and TypeScript could not see the lie.
    flag_path?: string;
  };
  // 2026-07-18 canonical off-switch: JSON overlay at .runtime/autonomy-override.json.
  // `disabled` above IS that overlay, and nothing else: the legacy
  // AUTONOMY_DISABLED flag file is gone and the endpoint no longer unions it in.
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
    dir?: string;
  };
};

// A tree the runner may be pointed at. ADR 0014: a run target is NAMED, never
// defaulted, so this list is what the operator names one from. The platform
// repo is in it only when developer mode is on, because the gate would refuse
// it otherwise and an option that always fails is not an option.
type RunTarget = { key: string; label: string; dir: string };

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
  const [confirmStopRunner, setConfirmStopRunner] = useState(false);
  const [confirmUnload, setConfirmUnload] = useState(false);
  const [confirmReserve, setConfirmReserve] = useState(false);
  const [targets, setTargets] = useState<RunTarget[] | null>(null);
  const [targetsFailed, setTargetsFailed] = useState(false);
  const [scope, setScope] = useState("");
  const [runnerError, setRunnerError] = useState<string | null>(null);
  // The runner's flock is per TREE, so "is one resident" is only answerable once
  // a tree is named. Null until then, and the row says so rather than reporting
  // the platform repo's lock as if it were the answer for every scope.
  const [scopeRunning, setScopeRunning] = useState<boolean | null>(null);
  // A FAILED PROBE IS NOT "NOT RUNNING". Both used to collapse into
  // `scopeRunning = null`, which renders the Start button, so a resident worker
  // whose probe merely failed offered no way to stop it, the dot painted
  // `stopped`, and the word beside it said `unknown`. Two answers to one
  // question, in one element, and no error on screen.
  const [scopeProbeFailed, setScopeProbeFailed] = useState(false);

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
    try {
      const r = await fetch("/api/queue/runner/targets");
      if (!r.ok) throw new Error(String(r.status));
      const j = await r.json();
      setTargets((j.targets || []) as RunTarget[]);
      setTargetsFailed(false);
    } catch {
      setTargetsFailed(true);
    }
  }, []);

  // Whether a worker is resident IN THE SELECTED TREE. Separate from `load`
  // because it depends on the picker, and because probing every registered
  // tree on a 15s poll to answer a question about one of them is waste.
  const loadScope = useCallback(async () => {
    if (!scope) {
      setScopeRunning(null);
      setScopeProbeFailed(false);
      return;
    }
    try {
      const r = await fetch(`/api/queue/runner?dir=${encodeURIComponent(scope)}`);
      if (!r.ok) throw new Error(String(r.status));
      setScopeRunning(!!(await r.json()).running);
      setScopeProbeFailed(false);
    } catch {
      setScopeRunning(null);
      setScopeProbeFailed(true);
    }
  }, [scope]);

  usePoll(load, 15_000);
  usePoll(loadScope, 15_000);
  // usePoll reads its callback through a ref, so it does not re-run when the
  // picker moves. Picking a tree has to answer "is one running there" now, not
  // in fifteen seconds.
  useEffect(() => { void loadScope(); }, [loadScope]);

  const cronDisabled = !!auto?.cron_autonomy.disabled;
  const runnerRunning = !!auto?.queue_runner?.running;
  // The pair, never collapsed. `enabled` is the override flag (work is
  // permitted); `running` is a flock probe (a worker is resident). The summary
  // used to be `allStopped = cronDisabled`, which reported "running" whenever
  // the flag was merely clear.
  //
  // The flock is per TREE, so `running` is only ever about one tree: the picked
  // one when there is one, the platform repo otherwise. There is no single
  // system-wide "is a worker resident" to report and inventing one would be the
  // same collapse in a new place.
  const pair = { running: scopeRunning ?? runnerRunning, enabled: !cronDisabled };
  const view = describeAutonomy(pair);

  // "Stop all" is the panic button and the ONLY control that moves both things
  // at once, because that is what the operator asked for and the dialog names
  // both. Everywhere else the brake and the runner are separate: starting a
  // worker and permitting unattended work are two decisions, and one button
  // doing both is how the global brake got cleared by someone starting a runner
  // (autonomy-implementation-2026-08-13 section 2.2).
  const onStopAll = async () => {
    setBusy("stop-all");
    try {
      // Stop the loop FIRST so nothing reloads a model right after we unload it.
      await fetch("/api/agents/autonomy/override", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: "Stop all, from Settings" }),
      });
      // ONLY A NAMED TREE. Runners are per tree, and an unnamed stop used to
      // resolve server-side to the platform repo: start a runner on
      // `projects/birds`, reload the page (which clears the picker), hit Stop
      // all, and the brake engaged, the GPU unloaded, the birds runner kept
      // working, and the panel reported it stopped. A control that lies,
      // shipped by the change whose whole purpose is deleting controls that lie.
      //
      // The brake still goes on, which is what actually stops the NEXT sweep
      // anywhere. The dialog says which half happened.
      if (scope) {
        await fetch(`/api/queue/runner/stop?dir=${encodeURIComponent(scope)}`,
          { method: "POST" });
      }
      await fetch("/api/ollama/unload", { method: "POST" });
      await load();
      await loadScope();
    } finally {
      setBusy(null);
      setConfirmStopAll(false);
    }
  };

  const onStartRunner = async () => {
    if (!scope) return;
    setBusy("start-runner");
    setRunnerError(null);
    try {
      const r = await fetch("/api/queue/runner/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ dir: scope }),
      });
      if (!r.ok) {
        // The backend hands back the run-target gate's own words, which are the
        // same words cron writes into queue-runner.log. Show them verbatim.
        const j = await r.json().catch(() => null);
        // `detail` is either a plain string, or (ADR 0023) `{code, message, hint}`.
        const d = j?.detail;
        const detailMsg = typeof d === "string" ? d : typeof d?.message === "string" ? d.message : null;
        setRunnerError(detailMsg || `start failed (${r.status})`);
      }
      await load();
      await loadScope();
    } finally {
      setBusy(null);
    }
  };

  const onStopRunner = async () => {
    setBusy("stop-runner");
    setRunnerError(null);
    try {
      await fetch(`/api/queue/runner/stop?dir=${encodeURIComponent(scope)}`, { method: "POST" });
      await load();
      await loadScope();
    } finally {
      setBusy(null);
      setConfirmStopRunner(false);
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

  const onReserve = async () => {
    setBusy("reserve");
    try {
      await fetch("/api/gpu/broker/reserve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duration_s: 1800, reason: "operator hold via UI" }),
      });
      await load();
    } finally {
      setBusy(null);
      setConfirmReserve(false);
    }
  };

  return (
    <SettingsSection icon={Power} title="Autonomy & GPU">
      <Row label="all autonomy">
        {auto == null ? "loading…" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            {/* The same rule the queue runner card uses (lib/autonomy), so this
                card can no longer contradict itself between two of its rows. */}
            <StatusDot status={view.kind}>{view.hint}</StatusDot>
            {/* Offered whenever there is something to stop: work permitted, a
                resident worker, or both. `live` alone (running AND permitted)
                hid it in the state where the brake is off and a worker is still
                holding a tree, which is exactly when it is wanted. */}
            {(!cronDisabled || pair.running) && (
              <Button size="sm" variant="danger-ghost" disabled={busy != null} onClick={() => setConfirmStopAll(true)}>
                <Power aria-hidden /> Stop all
              </Button>
            )}
          </span>
        )}
      </Row>

      {/* A POLICY, so it is a Toggle: it says what is permitted, it starts
          nothing, and it is instant and reversible so it gets no dialog. ONE
          fact lives here, whether work is PERMITTED. Whether anything is doing
          it is the runner row below, and this row must never speak to it. */}
      <Row label="unattended work">
        {auto == null ? "–" : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <Toggle
              checked={!cronDisabled}
              disabled={busy != null}
              label="Allow unattended work"
              onChange={async (next) => {
                setBusy(next ? "enable" : "disable");
                try {
                  if (next) {
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
            />
            <StatusDot status={cronDisabled ? "stopped" : "ok"}>
              {cronDisabled ? "switched off" : "permitted"}
            </StatusDot>
          </span>
        )}
      </Row>

      {/* A PROCESS, so it is a Button plus the tree it runs in. The tree is
          required: ADR 0014 says a run target is named, and the watchdog exits 2
          on an empty one. This control used to send no tree at all and also
          cleared the brake, so it moved a global policy and started nothing. */}
      <Row label="queue runner">
        <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
          {/* `warn` when the probe failed: the dot must not claim `stopped`
              while the word beside it says the answer is unknown. */}
          <StatusDot status={scopeRunning ? "running" : scopeProbeFailed ? "warn" : "stopped"}>
            {scope === "" ? "pick a tree"
              : scopeProbeFailed ? "unknown"
              : scopeRunning ? "resident" : "not running"}
          </StatusDot>
          <Select
            aria-label="Tree to run in"
            value={scope}
            disabled={busy != null || targets == null || targets.length === 0}
            onChange={(e) => { setScope(e.target.value); setRunnerError(null); }}
          >
            <option value="">
              {targetsFailed ? "could not load"
                : targets == null ? "loading…"
                : targets.length === 0 ? "no trees registered" : "pick a tree"}
            </option>
            {(targets || []).map((t) => (
              <option key={t.key} value={t.dir}>{t.label}</option>
            ))}
          </Select>
          {/* Stop is offered when a runner IS resident, and also when we could
              not find out. Showing Start for a tree that may well have a live
              worker leaves no way to stop it. */}
          {(scopeRunning || (scopeProbeFailed && scope !== "")) ? (
            <Button
              size="sm"
              variant="danger-ghost"
              disabled={busy != null}
              onClick={() => setConfirmStopRunner(true)}
            >
              <Square aria-hidden /> Stop runner
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              loading={busy === "start-runner"}
              // Dims, and keeps the pointer cursor. The design system allows
              // only default / pointer / text, and check_design.sh enforces it.
              disabled={busy != null || scope === ""}
              onClick={onStartRunner}
            >
              <Play aria-hidden /> Start runner
            </Button>
          )}
          {scope === "" && (
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
              Pick the tree to work in first.
            </span>
          )}
        </span>
      </Row>

      {/* Lines, not regions: the controls are still on screen and one thing
          beside them went wrong. Siblings of the Row rather than children of it
          because a SettingsRow wraps its children in a span and Notice is a
          div. */}
      {targetsFailed && (
        <Notice
          tone="warn"
          where="/api/queue/runner/targets"
          action={<Button size="sm" variant="ghost" onClick={load}>Retry</Button>}
        >
          Could not load the list of trees.
        </Notice>
      )}
      {scopeProbeFailed && (
        <Notice
          tone="warn"
          where="/api/queue/runner"
          action={<Button size="sm" variant="ghost" onClick={loadScope}>Retry</Button>}
        >
          Could not tell whether a runner is resident in that tree.
        </Notice>
      )}
      {runnerError && <Notice tone="error">{runnerError}</Notice>}

      <Row label="gpu memory">
        {oll == null || !oll.gpu.available ? (
          oll?.gpu.error ? <span className="ws-muted">no GPU detected</span> : "loading…"
        ) : (
          <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
              {formatMiB(oll.gpu.memory_used_mib)} / {formatMiB(oll.gpu.memory_total_mib)}
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
              mode={bro.mode} · committed {formatMiB(bro.committed_mib)}/{formatMiB(bro.total_vram_mib)}
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
        body={scope
          ? "Stops the runner in the selected tree, switches unattended work off, and frees the GPU. A ticket half-worked when the runner stops goes back on the queue. Starting it again reloads the models, which takes about 20 seconds."
          : "Switches unattended work off and frees the GPU. No tree is selected, so no resident runner is stopped: pick one first if a worker is running. Starting the models again takes about 20 seconds."}
        confirmLabel="Stop all"
        busy={busy === "stop-all"}
      />
      <ConfirmDialog
        open={confirmStopRunner}
        onCancel={() => setConfirmStopRunner(false)}
        onConfirm={onStopRunner}
        title="Stop the queue runner?"
        body="Stops the resident worker and any sweep in flight. A ticket half-worked when it stops goes back on the queue. Unattended work stays permitted; this only stops the worker."
        confirmLabel="Stop runner"
        busy={busy === "stop-runner"}
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
      <ConfirmDialog
        open={confirmReserve}
        onCancel={() => setConfirmReserve(false)}
        onConfirm={onReserve}
        danger={false}
        title="Reserve the GPU for yourself?"
        body="Holds the GPU for you for 30 minutes. Workshop and other apps lose it until the hold expires."
        confirmLabel="Reserve for 30m"
        busy={busy === "reserve"}
      />

      {bro?.available && (
        <Row label="my leases">
          {bro.my_leases && bro.my_leases.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
              {bro.my_leases.map((l) => (
                <span key={l.id} style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem" }}>
                  {l.app} · {formatMiB(l.vram_budget_mib)} budget · {formatMiB(l.vram_actual_mib)} actual · pri {l.priority}
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
            onClick={() => setConfirmReserve(true)}
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
                {m.name} · {formatVramBytes(m.size_vram_bytes)}
              </span>
            ))}
          </div>
        </Row>
      )}
    </SettingsSection>
  );
}
