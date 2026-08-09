import { useCallback, useEffect, useState } from "react";
import { Play, Square, RefreshCw, CircleAlert } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill, Skeleton,
  type StatusKind,
} from "../ui";

// QueueRunner: the visible, discoverable control for the worker loop that drains
// the task queue (ticket B0).
//
// Before this existed, the only autonomy surface was a label reading
// "enabled (Ralph fires every 20m)" derived SOLELY from the absence of the
// AUTONOMY_DISABLED flag file. That label was not wired to anything: no worker
// process had to exist for it to say "enabled", and in practice none did — the
// dispatcher planned zero sweeps every tick for weeks while the label claimed
// the loop was live. Starting work meant knowing to curl an endpoint.
//
// So this reports the two things that are actually independent, and never
// collapses them into one word:
//   running — a worker process is resident (probed via its flock, not a flag)
//   enabled — the off-switch is clear, i.e. work is PERMITTED
// Work only happens when both hold, which is exactly what the pill says.

export type RunnerState = {
  running: boolean;
  pid: number | null;
  enabled: boolean;
  lock_path: string;
  log_path: string;
  last_line: string | null;
};

const ENDPOINT = "/api/queue/runner";

// The three honest states, and the one thing each says to do next.
function describe(s: RunnerState): { kind: StatusKind; label: string; hint: string } {
  if (s.running && s.enabled) {
    return {
      kind: "running",
      label: "Running",
      hint: "Tickets move from queued to running to done on their own.",
    };
  }
  if (s.running && !s.enabled) {
    return {
      kind: "warn",
      label: "Paused",
      hint: "Autonomy is switched off, so no ticket will be picked up.",
    };
  }
  return {
    kind: "stopped",
    label: "Stopped",
    hint: "Nothing is running, so filed tickets sit in the queue.",
  };
}

export default function QueueRunner({ onChange }: { onChange?: () => void }) {
  const [state, setState] = useState<RunnerState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "start" | "stop">(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(`${r.status}`);
      setState(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    }
  }, []);

  useEffect(() => {
    load();
    // The loop is a background process, so its state changes without us acting.
    // Poll while mounted so the pill can never sit stale on a lie.
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const act = useCallback(
    async (which: "start" | "stop") => {
      setBusy(which);
      try {
        const r = await fetch(`${ENDPOINT}/${which}`, { method: "POST" });
        if (!r.ok) throw new Error(`${r.status}`);
        setState(await r.json());
        setErr(null);
        onChange?.();
      } catch (e) {
        setErr(String(e instanceof Error ? e.message : e));
      } finally {
        setBusy(null);
      }
    },
    [onChange],
  );

  if (err && !state) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't read the queue runner"
        hint={<><code>{ENDPOINT}</code> ({err})</>}
        action={<Button size="sm" onClick={load}>Retry</Button>}
      />
    );
  }

  if (!state) {
    return (
      <Card>
        <Stack gap="sm">
          <Skeleton width="30%" />
          <Skeleton />
        </Stack>
      </Card>
    );
  }

  const d = describe(state);
  const live = state.running && state.enabled;

  return (
    <Card>
      <Stack>
        <Row between wrap>
          <Row>
            <SectionLabel>Queue runner</SectionLabel>
            <StatusPill status={d.kind}>{d.label}</StatusPill>
            {state.pid != null && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>pid {state.pid}</span>
            )}
          </Row>
          <Row>
            <Button size="sm" variant="ghost" onClick={load} aria-label="Refresh runner state">
              <RefreshCw size={16} aria-hidden />
            </Button>
            {live ? (
              <Button
                size="sm"
                variant="danger-ghost"
                loading={busy === "stop"}
                disabled={busy != null}
                onClick={() => act("stop")}
              >
                <Square size={16} aria-hidden /> Stop queue
              </Button>
            ) : (
              <Button
                size="sm"
                variant="primary"
                loading={busy === "start"}
                disabled={busy != null}
                onClick={() => act("start")}
              >
                <Play size={16} aria-hidden /> Start queue
              </Button>
            )}
          </Row>
        </Row>

        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{d.hint}</p>

        {state.last_line && (
          <p
            className="ws-muted"
            style={{
              fontSize: "var(--fs-xs)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={state.last_line}
          >
            Last activity: <code>{state.last_line}</code>
          </p>
        )}

        {err && (
          <Row>
            <CircleAlert size={14} aria-hidden />
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Control failed: {err}</span>
          </Row>
        )}
      </Stack>
    </Card>
  );
}
