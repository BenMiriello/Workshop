import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Play, ArrowUpRight, HelpCircle } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill, StatusDot,
  Skeleton, Segmented, Tag, catFor, type StatusKind, type SegmentedOption,
} from "../ui";
import { scopeLabel, scopeHref } from "../lib/tasks";

// AutonomyScopes: the central "manage scope per project" list. One row per
// manageable scope (every project + every registry agent), sourced from the
// aggregate GET /api/tasks/autonomy-scopes. Each row exposes the preset as an
// inline Segmented with an explicit Save (config write: never commit-on-change,
// per the design system), the queue counts as status badges, an awaiting badge,
// a running indicator, and a project-scope Run now. The full per-scope editor
// (depth/fanout/limits/cron/triggers) lives on the scope's detail page, linked
// from each row title.

type Preset = "off" | "on-demand" | "scheduled" | "continuous";

type ScopeRow = {
  scope_key: string;
  kind: "project" | "agent";
  title: string;
  preset: Preset;
  depth: string;
  running: boolean;
  queue: { ready: number; doing: number; in_review: number; blocked: number };
  awaiting_count: number;
  last_run: { id: string; status?: string; ended_at?: string } | null;
};

const ENDPOINT = "/api/tasks/autonomy-scopes";

const PRESET_OPTIONS: SegmentedOption<Preset>[] = [
  { value: "off", label: "Off" },
  { value: "on-demand", label: "On demand" },
  { value: "scheduled", label: "Scheduled" },
  { value: "continuous", label: "Continuous" },
];

function isPreset(v: unknown): v is Preset {
  return v === "off" || v === "on-demand" || v === "scheduled" || v === "continuous";
}

function fmtTs(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Client mirror of backend triggers.preset_to_triggers, used for the agent PUT
// (whose endpoint accepts a trigger list, not a preset). Projects send the
// preset directly and let the backend expand it.
function presetToTriggers(preset: Preset): unknown[] {
  if (preset === "continuous") {
    return [{ kind: "loop", max_iterations: 20, budget: { max_wall_s: 3000 }, completion_gate: "reviewer" }];
  }
  if (preset === "scheduled") return [{ kind: "cron", cron_expression: "" }];
  if (preset === "on-demand") return [{ kind: "manual" }];
  return [];
}

function ScopeCard({ row, onSaved }: { row: ScopeRow; onSaved: () => void }) {
  const [draft, setDraft] = useState<Preset>(row.preset);
  const [saving, setSaving] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Keep the draft in sync when a refresh brings a newer server preset (only
  // while the row is not mid-edit, so an in-progress change is never clobbered).
  useEffect(() => { setDraft(row.preset); }, [row.preset]);

  const isProject = row.kind === "project";
  const slug = row.scope_key.split(":")[1] ?? "";
  const href = scopeHref(row.scope_key);
  const dirty = draft !== row.preset;

  const putUrl = isProject
    ? `/api/projects/${encodeURIComponent(slug)}/autonomy`
    : `/api/agents/${encodeURIComponent(slug)}`;

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    setNote(null);
    try {
      const body = isProject
        ? { preset: draft, cron: draft === "scheduled" ? "" : undefined }
        : { triggers: presetToTriggers(draft) };
      const r = await fetch(putUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, isProject, putUrl, onSaved]);

  const runNow = useCallback(async () => {
    setRunBusy(true);
    setErr(null);
    setNote(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/autonomy/run`, { method: "POST" });
      if (r.status === 409) {
        const e = await r.json().catch(() => ({}));
        setNote(e.detail || "A sweep is already running for this scope.");
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNote("Run started. It appears under Runs.");
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunBusy(false);
    }
  }, [slug, onSaved]);

  const queueItems: { key: string; label: string; count: number; status: StatusKind }[] = [
    { key: "ready", label: "ready", count: row.queue.ready, status: "ok" },
    { key: "doing", label: "in progress", count: row.queue.doing, status: "running" },
    { key: "in_review", label: "in review", count: row.queue.in_review, status: "warn" },
    { key: "blocked", label: "blocked", count: row.queue.blocked, status: "danger" },
  ];
  const anyQueue = queueItems.some((i) => i.count > 0);

  return (
    <Card>
      <Stack gap="sm">
        <Row between wrap style={{ gap: "var(--space-3)", alignItems: "flex-start" }}>
          <Stack gap="sm" style={{ minWidth: 0 }}>
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
              {href ? (
                <Link to={href} className="ws-bc__link" style={{ fontWeight: 600 }}>
                  {row.title} <ArrowUpRight size={14} aria-hidden />
                </Link>
              ) : (
                <span style={{ fontWeight: 600 }}>{row.title}</span>
              )}
              <Tag hue={catFor(row.kind)}>{row.kind}</Tag>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{scopeLabel(row.scope_key)}</span>
            </Row>
            <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
              <StatusDot status={row.running ? "running" : "stopped"}>
                {row.running ? "running" : "idle"}
              </StatusDot>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>depth: <strong>{row.depth}</strong></span>
              {row.last_run && (
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  last run {row.last_run.status}{row.last_run.ended_at ? ` · ${fmtTs(row.last_run.ended_at)}` : ""}
                </span>
              )}
            </Row>
          </Stack>
          {isProject && (
            <Button
              size="sm"
              variant="primary"
              loading={runBusy}
              disabled={runBusy || row.running}
              onClick={runNow}
            >
              <Play aria-hidden /> Run now
            </Button>
          )}
        </Row>

        <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
          <Segmented<Preset>
            ariaLabel={`Autonomy preset for ${row.title}`}
            options={PRESET_OPTIONS}
            value={draft}
            onChange={setDraft}
          />
          {dirty && (
            <>
              <Button size="sm" variant="primary" loading={saving} onClick={save}>Save</Button>
              <Button size="sm" variant="ghost" onClick={() => { setDraft(row.preset); setErr(null); }}>Cancel</Button>
            </>
          )}
        </Row>
        {dirty && draft === "scheduled" && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            Scheduled needs a cadence. Set it on the {href ? "detail page" : "scope's detail page"}.
          </span>
        )}

        {anyQueue ? (
          <Row wrap style={{ gap: "var(--space-2)" }}>
            {queueItems.filter((i) => i.count > 0).map((i) => (
              <StatusPill key={i.key} status={i.status}>{i.count} {i.label}</StatusPill>
            ))}
            {row.awaiting_count > 0 && (
              <StatusPill status="warn">
                <HelpCircle size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                {row.awaiting_count} awaiting
              </StatusPill>
            )}
          </Row>
        ) : (
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Queue clear.</span>
            {row.awaiting_count > 0 && (
              <StatusPill status="warn">
                <HelpCircle size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                {row.awaiting_count} awaiting
              </StatusPill>
            )}
          </Row>
        )}

        {note && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{note}</span>}
        {err && <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{err}</p>}
      </Stack>
    </Card>
  );
}

export default function AutonomyScopes() {
  const [rows, setRows] = useState<ScopeRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      const list: ScopeRow[] = (Array.isArray(j.scopes) ? j.scopes : []).map((s: ScopeRow) => ({
        ...s,
        preset: isPreset(s.preset) ? s.preset : "off",
      }));
      setRows(list);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const OVERFLOW_AT = 60;

  return (
    <div>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel>Scopes</SectionLabel>
        <Button size="sm" variant="ghost" onClick={load} loading={loading}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      </Row>

      {err ? (
        <StateBlock
          variant="error"
          title="Couldn't load autonomy scopes"
          hint={<><code>{ENDPOINT}</code> ({err})</>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : rows === null ? (
        <Stack gap="sm">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <Stack gap="sm">
                <Skeleton width="45%" />
                <Skeleton width="30%" />
                <Skeleton width="60%" />
              </Stack>
            </Card>
          ))}
        </Stack>
      ) : rows.length === 0 ? (
        <StateBlock
          title="No manageable scopes yet"
          hint="Autonomy scopes are your projects and agents. Create one, then set how it works its own queue."
        />
      ) : (
        <Stack gap="sm">
          {rows.length > OVERFLOW_AT && (
            <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
              Showing the first {OVERFLOW_AT} of {rows.length} scopes.
            </p>
          )}
          {rows.slice(0, OVERFLOW_AT).map((row) => (
            <ScopeCard key={row.scope_key} row={row} onSaved={load} />
          ))}
        </Stack>
      )}
    </div>
  );
}
