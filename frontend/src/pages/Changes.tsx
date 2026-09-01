import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { GitPullRequestArrow, RefreshCw } from "lucide-react";
import {
  Button, Card, PageHeader, Row, Segmented, Skeleton, Stack, StateBlock,
  StatusPill, Table, TableScroll, shortHttpError, timeAgo,
} from "@workshop/ui";

import { shortSha } from "../lib/sha";
import type { components } from "../api/schema.gen";
import {
  branchTicketId, changeStatusKind, changeStatusLabel, changesUnavailable,
  isOpenChange, shortBranch, type Change,
} from "../lib/changeReview";
import { ticketHref } from "../lib/tasks";

// /changes — every change, open ones first.
//
// The list exists so a finished branch is FINDABLE. Before it, a change was
// only reachable from the ticket that happened to carry it, which meant a
// change whose ticket you had not opened did not exist as far as the operator
// was concerned. Design: changes-and-review-2026-08-22.md sec 8, step C4.
//
// The row is deliberately thin: which branch, into what, which project, how
// long it has been waiting. Everything that decides whether the change is any
// good is the diff, and the diff belongs on the review page, not in a list.

// The list route caps at 1000 and defaults to 200. Asking for one more than we
// draw is what lets the page say it is not showing everything, instead of
// quietly ending.
const PAGE = 100;

type Filter = "open" | "all";

export default function Changes() {
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [absent, setAbsent] = useState(false);
  const [filter, setFilter] = useState<Filter>("open");

  const load = useCallback(async () => {
    setErr(null);
    setAbsent(false);
    try {
      const r = await fetch(`/api/changes?limit=${PAGE + 1}`);
      if (changesUnavailable(r.status)) { setAbsent(true); setChanges([]); return; }
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      // ChangeCollection, named from the generated client.
      const j: components["schemas"]["ChangeCollection"] = await r.json();
      setChanges(j.changes ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const all = changes ?? [];
  const filtered = useMemo(
    () => (filter === "open" ? all.filter(isOpenChange) : all),
    [all, filter],
  );
  const shown = filtered.slice(0, PAGE);
  const chips: { value: Filter; label: string }[] = [
    { value: "open", label: "Open" },
    { value: "all", label: "All" },
  ];

  return (
    <div>
      <PageHeader
        title="Changes"
        sub="A branch that has earned a review: the code behind a finished ticket, before it reaches the trunk."
        actions={
          <Button size="sm" variant="ghost" onClick={() => void load()}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        }
      />

      <Row wrap className="ws-changes__filters">
        <Segmented
          ariaLabel="Which changes to list"
          options={chips}
          value={filter}
          onChange={setFilter}
        />
      </Row>

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /><Skeleton width="52%" /></Stack></Card>
      ) : absent ? (
        <StateBlock
          icon={<GitPullRequestArrow aria-hidden />}
          title="This instance keeps no change records"
          hint="A change is a row in the relational store, and this instance has none configured, so there is nothing to list. Work still happens on branches; it is just not recorded as a reviewable artifact here."
        />
      ) : err ? (
        <StateBlock
          variant="error"
          title="Couldn't load changes"
          hint={<code>GET /api/changes ({err})</code>}
          action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
        />
      ) : shown.length === 0 ? (
        <StateBlock
          icon={<GitPullRequestArrow aria-hidden />}
          title={filter === "open" ? "Nothing waiting for review" : "No changes yet"}
          hint={filter === "open"
            ? "A change opens when a branch first has commits ahead of its base. Switch to All to see the ones already merged or closed."
            : "A supervised run on a branch opens one when it finishes, and accepting its ticket is what merges it."}
        />
      ) : (
        <Stack gap="sm">
          <TableScroll>
            <Table>
              <thead>
                <tr>
                  <th>Change</th>
                  <th>Status</th>
                  <th>Project</th>
                  <th>Branch</th>
                  <th>Head</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/changes/${encodeURIComponent(c.id)}`}>
                        {c.title || c.branch_slug}
                      </Link>
                    </td>
                    <td className="ws-changes__status">
                      <StatusPill status={changeStatusKind(c.status)}>
                        {changeStatusLabel(c.status)}
                      </StatusPill>
                    </td>
                    <td>
                      <Link to={`/projects/${encodeURIComponent(c.project_slug)}`}>
                        {c.project_slug}
                      </Link>
                    </td>
                    {/* A BRANCH ID IS AN ADDRESS, NOT A LABEL. A supervised
                        run names its branch after the ticket it is working, so
                        this column was 32 hex characters wide, was the widest
                        column on the page, and said the same thing the Change
                        column already says in words. Abbreviated like a sha
                        (the full value is on the cell), and where it IS a
                        ticket it links to that ticket rather than sitting
                        there as text nobody can read. */}
                    <td className="ws-changes__branch" title={c.branch_slug}>
                      {branchTicketId(c) ? (
                        <Link className="ws-mono" to={ticketHref(c.project_slug, branchTicketId(c)!)}>
                          {shortBranch(c.branch_slug)}
                        </Link>
                      ) : (
                        <span className="ws-mono">{shortBranch(c.branch_slug)}</span>
                      )}
                      <span className="ws-muted"> into </span>
                      <span className="ws-mono">{c.base_ref}</span>
                    </td>
                    <td className="ws-mono ws-muted">{shortSha(c.head_sha)}</td>
                    <td className="ws-muted">{c.updated_at ? timeAgo(c.updated_at) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </TableScroll>
          {all.length > PAGE && (
            <p className="ws-muted">
              More than {PAGE} changes exist. This list stops at the {PAGE} most
              recently active; older ones stay reachable from the tickets that
              carry them.
            </p>
          )}
        </Stack>
      )}
    </div>
  );
}
