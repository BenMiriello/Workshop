import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  Button,
  NumCell,
  PageHeader,
  Skeleton,
  StateBlock,
  StatusDot,
  Table,
  TableScroll,
  TimeCell,
  type StatusKind,
} from "../ui";

type Run = {
  ts?: string;
  task_picked?: string;
  task?: string;
  exit_code?: number;
  duration_s?: number;
  agent_id?: string;
  notes?: string;
  [k: string]: unknown;
};

type RunsResp = {
  runs: Run[];
  count: number;
  log_path: string;
  note?: string;
};

const statusFor = (r: Run): { label: string; kind: StatusKind } => {
  if (r.exit_code === 0) return { label: "ok", kind: "ok" };
  if (r.exit_code != null) return { label: `exit ${r.exit_code}`, kind: "danger" };
  return { label: "—", kind: "stopped" };
};

export default function Runs() {
  const [data, setData] = useState<RunsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/agents/runs?limit=50");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  const head = (
    <thead>
      <tr>
        <th>Time</th>
        <th>Agent</th>
        <th>Task</th>
        <th>Status</th>
        <th style={{ textAlign: "right" }}>Duration</th>
      </tr>
    </thead>
  );

  let body;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load runs"
        hint={
          <>
            <code>/api/agents/runs</code> — {err}
          </>
        }
        action={
          <Button size="sm" style={{ marginTop: "var(--space-2)" }} onClick={refresh}>
            Retry
          </Button>
        }
      />
    );
  } else if (!data) {
    body = (
      <TableScroll>
        <Table>
          {head}
          <tbody>
            {[0, 1, 2, 3].map((i) => (
              <tr key={i}>
                {["70%", "55%", "40%", "30%", "20%"].map((w, j) => (
                  <td key={j}>
                    <Skeleton width={w} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      </TableScroll>
    );
  } else if (data.runs.length === 0) {
    body = (
      <StateBlock
        title="No agent runs logged yet"
        hint={
          <>
            The cron loop appends here once installed. Trigger one with <code>cron_agent.sh</code>.
          </>
        }
      />
    );
  } else {
    body = (
      <TableScroll>
        <Table>
          {head}
          <tbody>
            {data.runs.map((r, i) => {
              const status = statusFor(r);
              return (
                <tr key={`${r.ts ?? "?"}-${i}`}>
                  <TimeCell>{r.ts ?? "—"}</TimeCell>
                  <td className="ws-mono">{r.agent_id ?? "—"}</td>
                  <td>{r.task_picked ?? r.task ?? r.notes ?? "—"}</td>
                  <td>
                    <StatusDot status={status.kind}>{status.label}</StatusDot>
                  </td>
                  <NumCell>{r.duration_s != null ? `${r.duration_s}s` : "—"}</NumCell>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </TableScroll>
    );
  }

  return (
    <div>
      <PageHeader
        title="Runs"
        sub={
          data
            ? `${data.count} ${data.count === 1 ? "run" : "runs"}${data.note ? ` · ${data.note}` : ""} · log: ${data.log_path}`
            : undefined
        }
        actions={
          <Button size="sm" onClick={refresh} loading={loading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        }
      />
      {body}
    </div>
  );
}
