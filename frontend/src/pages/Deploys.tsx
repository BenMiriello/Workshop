import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Rocket, RefreshCw, ExternalLink, Ban, Power } from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, StatusDot, StatusPill,
  Badge, Tag, Skeleton, Field, Input, Modal, ConfirmDialog, type StatusKind,
} from "../ui";
import { TableScroll, Table } from "../ui";

// /deploys — cross-project index of every hosted target across the workshop.
// Each row is a Deploy: which project, which target, which branch, what's
// currently live, and when. The row-level Deploy button jumps to the per-
// project panel where the operator can pick a ref and confirm.
//
// The workspace autonomy override sits at the top of the page: engage it to
// freeze all shipping (manual and event-driven) without touching the workshop
// autonomy override.

type Deploy = {
  id: string;
  project_slug: string;
  name: string;
  target: { kind: string; project_name?: string };
  source: { kind: string; branch?: string };
  domain?: string;
  autonomy: string;
  last_run_id?: string;
  current?: { commit_sha?: string; deployed_at?: string; url?: string };
};
type Override = { engaged: boolean; reason?: string | null; engaged_at?: string | null; engaged_by?: string | null };

type Filter = "all" | "prod" | "preview" | "failed" | "autonomous";

function ago(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!t) return "";
  const s = Math.max(1, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function short(sha?: string): string { return (sha || "").slice(0, 7); }


export default function Deploys() {
  const [deploys, setDeploys] = useState<Deploy[] | null>(null);
  const [override, setOverride] = useState<Override | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [engageOpen, setEngageOpen] = useState(false);
  const [engageReason, setEngageReason] = useState("");
  const [engageSaving, setEngageSaving] = useState(false);
  const [clearConfirm, setClearConfirm] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [dr, or] = await Promise.all([
        fetch("/api/deploys"),
        fetch("/api/deploys/autonomy/override"),
      ]);
      if (!dr.ok) throw new Error(`deploys ${dr.status} ${await dr.text()}`);
      if (!or.ok) throw new Error(`override ${or.status} ${await or.text()}`);
      const dj = await dr.json();
      const oj = await or.json();
      setDeploys(dj.deploys || []);
      setOverride(oj);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const engage = useCallback(async () => {
    setEngageSaving(true);
    try {
      const r = await fetch("/api/deploys/autonomy/override", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason: engageReason || null, by: "operator" }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setOverride(await r.json());
      setEngageOpen(false);
      setEngageReason("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setEngageSaving(false);
    }
  }, [engageReason]);

  const clearOverride = useCallback(async () => {
    try {
      const r = await fetch("/api/deploys/autonomy/override", { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setOverride(await r.json());
      setClearConfirm(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const filtered = useMemo(() => {
    const list = (deploys || []).filter((d) => {
      if (filter === "prod" && d.name !== "prod") return false;
      if (filter === "preview" && d.name === "prod") return false;
      if (filter === "autonomous" && d.autonomy !== "autonomous") return false;
      if (filter === "failed") {
        // Row is "failed" if there is a current run pointer but no landed URL,
        // OR if the last known state has no current at all despite a last_run_id.
        const hasBroken = d.last_run_id && !d.current?.url;
        if (!hasBroken) return false;
      }
      if (q.trim()) {
        const needle = q.trim().toLowerCase();
        const hay = [
          d.project_slug, d.name, d.target.kind, d.target.project_name || "",
          d.domain || "", d.source.branch || "",
        ].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    return list;
  }, [deploys, filter, q]);

  const chips: { key: Filter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "prod", label: "Prod" },
    { key: "preview", label: "Preview" },
    { key: "failed", label: "Failed" },
    { key: "autonomous", label: "Autonomous" },
  ];

  return (
    <div className="ws-page">
      <PageHeader
        title={<><Rocket aria-hidden style={{ verticalAlign: "-3px", marginRight: 6 }} /> Deploys</>}
        sub="What's live where. One row per hosted target across every project."
        actions={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button size="sm" variant="ghost" onClick={load}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
            {override?.engaged ? (
              <Button size="sm" variant="danger-ghost" onClick={() => setClearConfirm(true)}>
                <Ban aria-hidden /> Clear freeze
              </Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => setEngageOpen(true)}>
                <Power aria-hidden /> Freeze all deploys
              </Button>
            )}
          </Row>
        }
      />

      {override?.engaged && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Card>
            <Row wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
              <StatusDot status="warn">frozen</StatusDot>
              <span style={{ flex: 1 }}>
                All deploys are refused (409). Reason: {override.reason || "(no reason recorded)"}.
                {override.engaged_at ? ` Engaged ${ago(override.engaged_at)}` : ""}
                {override.engaged_by ? ` by ${override.engaged_by}` : ""}.
              </span>
            </Row>
          </Card>
        </div>
      )}

      <Row wrap between style={{ marginBottom: "var(--space-3)" }}>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {chips.map((c) => (
            <button
              key={c.key}
              type="button"
              className={"ws-chip" + (filter === c.key ? " is-active" : "")}
              onClick={() => setFilter(c.key)}
              aria-pressed={filter === c.key}
            >
              {c.label}
            </button>
          ))}
        </Row>
        <Field>
          <Input
            placeholder="Filter by project / name / branch"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </Field>
      </Row>

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack></Card>
      ) : err ? (
        <StateBlock variant="error" title="Couldn't load deploys"
          hint={<code>GET /api/deploys ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : filtered.length === 0 ? (
        <StateBlock
          title={q || filter !== "all" ? "No matching deploys" : "No deploys yet"}
          hint={q || filter !== "all"
            ? "Clear filters to see everything."
            : "Add a deploy target from any project detail to publish it to a hosted provider."}
        />
      ) : (
        <TableScroll>
          <Table>
            <thead>
              <tr>
                <th>Project</th>
                <th>Target</th>
                <th>Branch</th>
                <th>Live</th>
                <th>Version</th>
                <th>Autonomy</th>
                <th>Deployed</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link to={`/projects/${encodeURIComponent(d.project_slug)}`}>
                      {d.project_slug}
                    </Link>
                  </td>
                  <td>
                    <Row wrap style={{ gap: "var(--space-1)", alignItems: "center" }}>
                      <Link to={`/deploys/${encodeURIComponent(d.id)}`} style={{ fontWeight: "var(--fw-semi)" }}>
                        {d.name}
                      </Link>
                      <Badge>{d.target.kind}</Badge>
                    </Row>
                  </td>
                  <td><Tag hue="indigo">{d.source.branch || "HEAD"}</Tag></td>
                  <td>
                    {d.current?.url ? (
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <a href={d.current.url} target="_blank" rel="noreferrer"
                           style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          {d.current.url.replace(/^https?:\/\//, "")}
                          <ExternalLink size={12} aria-hidden />
                        </a>
                      </Row>
                    ) : (
                      <StatusPill status={"stopped" as StatusKind}>never deployed</StatusPill>
                    )}
                  </td>
                  <td className="ws-mono ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {d.current?.commit_sha ? `v${short(d.current.commit_sha)}` : ""}
                  </td>
                  <td>
                    <Badge>{d.autonomy}</Badge>
                  </td>
                  <td className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {d.current?.deployed_at ? ago(d.current.deployed_at) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableScroll>
      )}

      <section style={{ marginTop: "var(--space-6)" }}>
        <SectionLabel>How this works</SectionLabel>
        <Card>
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
            Deploys are configured per-project (open a project and use the Deploys panel).
            Every attempt is a Job (kind = deploy) and appears in the Work page.
            The freeze switch above is independent of the workshop autonomy override —
            freezing agents does NOT freeze deploys, and vice versa.
          </p>
        </Card>
      </section>

      {engageOpen && (
        <Modal open onClose={() => setEngageOpen(false)} title="Freeze all deploys?">
          <Stack gap="lg">
            <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
              While engaged, every deploy and rollback request is refused with a 409.
              Autonomous (event-driven) deploys are also refused. Clear to resume.
            </p>
            <Field label="Reason" help="Optional; helps future-you understand what was going on.">
              <Input
                value={engageReason}
                onChange={(e) => setEngageReason(e.target.value)}
                placeholder="e.g. mobile release cut in progress"
              />
            </Field>
            <Row style={{ gap: "var(--space-2)", justifyContent: "flex-end" }}>
              <Button variant="ghost" onClick={() => setEngageOpen(false)} disabled={engageSaving}>Cancel</Button>
              <Button variant="primary" onClick={engage} loading={engageSaving} disabled={engageSaving}>
                Freeze
              </Button>
            </Row>
          </Stack>
        </Modal>
      )}

      {clearConfirm && (
        <ConfirmDialog
          open
          onCancel={() => setClearConfirm(false)}
          title="Clear the deploy freeze?"
          body={<>Deploys will be allowed to fire again immediately. Any autonomous triggers that fired while frozen were dropped (not queued).</>}
          confirmLabel="Clear freeze"
          onConfirm={clearOverride}
        />
      )}
    </div>
  );
}
