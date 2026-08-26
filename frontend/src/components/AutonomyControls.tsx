import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ConfirmDialog, Notice, Select, StatusDot, Toggle, SettingsSection, SettingsRow as Row } from "@workshop/ui";
import { Power, Play, Square } from "lucide-react";
import { describeAutonomy } from "../lib/autonomy";

// Autonomy controls. The global cron-autonomy pause (AUTONOMY_DISABLED) and the
// queue runner. The AUTONOMY_DISABLED switch is operationally load-bearing and
// must stay reachable — this component is that home.
//
// Two independent controls, and keeping them independent is the point:
//   - The BRAKE: whether unattended work is permitted at all. A policy, so a
//     Toggle. Instant and reversible, so no dialog.
//   - The RUNNER: a resident worker process in one named tree. A process, so a
//     Button plus the tree to run it in. Stopping it gets a dialog.
// Plus a single "Stop all" that does both at once, which is the one place they
// are allowed to move together because the dialog says so.

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

const POLL_MS = 15_000;

// Every read here owns its own cadence and none of them retries: that is what
// the polls they replace did. Staleness would swallow the catch-up read
// `usePoll` ran on the way back from hidden, and a retry would put extra
// requests in front of a cadence that already re-reads on its own.
const POLLED = { staleTime: 0, retry: false } as const;

export default function AutonomyControls() {
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStopAll, setConfirmStopAll] = useState(false);
  const [confirmStopRunner, setConfirmStopRunner] = useState(false);
  const [scope, setScope] = useState("");
  const [runnerError, setRunnerError] = useState<string | null>(null);

  // ONE POLL BECOMES TWO QUERIES. The tick this replaces issued both of the
  // requests below, in order, but the two endpoints have DIFFERENT failure
  // contracts -- a dropped status keeps the panel on screen and says nothing,
  // a dropped target list says so in a line and offers Retry -- and one key
  // cannot own two of those. So the request pattern changes from "one timer,
  // two requests" to two independently scheduled queries on the same interval.
  // Same endpoints, same cadence, same rendered output; what moved is that the
  // two are no longer forced to arrive together.

  // `/api/agents/autonomy/status`. A NON-OK RESPONSE IS AN ANSWER (`null`, and
  // the rows fall back to their loading text); a fetch that never got a
  // response at all keeps the last good copy, which is what the `catch { keep
  // prior state }` this replaces did. Returning null for one and throwing for
  // the other is what preserves that split.
  //
  // NOT the Agents page's `["agents", "autonomy", "status"]`, deliberately.
  // That one is written as a queryFn that cannot reject, so for it every
  // failure is `null`. Same URL, different answer to "what does a dropped read
  // mean", and a key is the read INCLUDING that. The two never mount together
  // (Settings and the roster are different routes), so nothing is read twice.
  const statusQuery = useQuery({
    queryKey: ["agents", "autonomy", "status", "controls"],
    queryFn: async (): Promise<AutonomyStatus | null> => {
      const r = await fetch("/api/agents/autonomy/status");
      return r.ok ? ((await r.json()) as AutonomyStatus) : null;
    },
    refetchInterval: POLL_MS,
    ...POLLED,
  });
  const auto = statusQuery.data ?? null;

  const targetsQuery = useQuery({
    queryKey: ["queue", "runner", "targets"],
    queryFn: async (): Promise<RunTarget[]> => {
      const r = await fetch("/api/queue/runner/targets");
      if (!r.ok) throw new Error(String(r.status));
      return ((await r.json()).targets || []) as RunTarget[];
    },
    refetchInterval: POLL_MS,
    ...POLLED,
  });
  // A failed list keeps the last good options and says so beside the picker,
  // and the next success clears the line. Unchanged in both halves.
  const targets = targetsQuery.data ?? null;
  const targetsFailed = targetsQuery.isError;

  // Whether a worker is resident IN THE SELECTED TREE. Its own query because it
  // depends on the picker, and because probing every registered tree on a 15s
  // poll to answer a question about one of them is waste.
  //
  // THE TREE IS IN THE KEY, which is also what makes picking one answer now
  // rather than in fifteen seconds: `usePoll` read its callback through a ref,
  // so the picker needed a second effect to force that read and this replaces
  // it. ONE DELIBERATE CHANGE comes with that: the moment between picking a
  // tree and its answer arriving used to show the PREVIOUS tree's verdict, and
  // keyed by tree it shows that tree's own last one instead -- "not running"
  // for a tree never probed, "unknown" for one whose last probe failed -- until
  // the new probe lands. One round trip either way.
  const runnerQuery = useQuery({
    queryKey: ["queue", "runner", scope],
    queryFn: async (): Promise<{ running: boolean }> => {
      const r = await fetch(`/api/queue/runner?dir=${encodeURIComponent(scope)}`);
      if (!r.ok) throw new Error(String(r.status));
      return { running: !!(await r.json()).running };
    },
    // The runner's flock is per TREE, so "is one resident" is only answerable
    // once a tree is named. Nothing is probed until then, and the row says so
    // rather than reporting the platform repo's lock as if it were the answer
    // for every scope.
    enabled: scope !== "",
    refetchInterval: POLL_MS,
    ...POLLED,
  });
  // A FAILED PROBE IS NOT "NOT RUNNING". Both used to collapse into
  // `scopeRunning = null`, which renders the Start button, so a resident worker
  // whose probe merely failed offered no way to stop it, the dot painted
  // `stopped`, and the word beside it said `unknown`. Two answers to one
  // question, in one element, and no error on screen.
  //
  // A failed probe DROPS the last good verdict rather than keeping it, which is
  // what `setScopeRunning(null)` in the old catch did: "a worker was resident
  // fifteen seconds ago" is not an answer to "is one resident".
  const scopeProbeFailed = runnerQuery.isError;
  const scopeRunning = runnerQuery.isError ? null : (runnerQuery.data?.running ?? null);

  const { refetch: refetchStatus } = statusQuery;
  const { refetch: refetchTargets } = targetsQuery;
  const { refetch: refetchRunner } = runnerQuery;
  // The write paths and the Retry line both re-read BOTH of the split reads,
  // exactly as the one `load` they call did.
  const load = useCallback(async () => {
    await Promise.all([refetchStatus(), refetchTargets()]);
  }, [refetchStatus, refetchTargets]);
  const loadScope = useCallback(async () => {
    // `refetch` fires even on a disabled query, and the read this replaces did
    // not probe at all without a tree.
    if (!scope) return;
    await refetchRunner();
  }, [scope, refetchRunner]);

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
      await fetch("/api/agents/autonomy/override", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: "Stop all, from Settings" }),
      });
      // ONLY A NAMED TREE. Runners are per tree, and an unnamed stop used to
      // resolve server-side to the platform repo: start a runner on
      // `projects/birds`, reload the page (which clears the picker), hit Stop
      // all, and the brake engaged while the birds runner kept working and the
      // panel reported it stopped. A control that lies.
      //
      // The brake still goes on, which is what actually stops the NEXT sweep
      // anywhere. The dialog says which half happened.
      if (scope) {
        await fetch(`/api/queue/runner/stop?dir=${encodeURIComponent(scope)}`,
          { method: "POST" });
      }
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

  return (
    <SettingsSection icon={Power} title="Autonomy">
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

      <ConfirmDialog
        open={confirmStopAll}
        onCancel={() => setConfirmStopAll(false)}
        onConfirm={onStopAll}
        title="Stop all autonomy?"
        body={scope
          ? "Stops the runner in the selected tree and switches unattended work off. A ticket half-worked when the runner stops goes back on the queue."
          : "Switches unattended work off. No tree is selected, so no resident runner is stopped: pick one first if a worker is running."}
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
    </SettingsSection>
  );
}
