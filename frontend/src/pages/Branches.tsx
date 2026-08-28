import { useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, GitBranch, RefreshCw,
} from "lucide-react";
import {
  Button, Card, Notice, PageHeader, Row, SectionLabel, Segmented, Skeleton,
  Stack, StateBlock, StatusDot, StatusPill, Table, TableScroll,
} from "@workshop/ui";

import HolderControls from "../components/holders/HolderControls";
import {
  useBranchesView, useYieldFlags,
} from "../components/holders/useBranchesView";
import {
  attentionCount, blockingFiles, branchState, driftHeadline, driftKind,
  driftLabel, heartbeatNote, orderRows, shortSeconds,
} from "../lib/branchesView";
import type { BranchRow } from "../lib/branchesView";

// WHO IS WORKING WHERE, AND WHAT IS STUCK.
//
// The question this page exists for is not "what branches are there". It is
// "is anything wedged, and is anyone's work stranded" -- so the ordering is by
// ATTENTION rather than by time, the dirty-tree column is as prominent as the
// holder, and the drift banner sits above everything because a stale origin
// makes every other answer on the screen a description of code the operator
// cannot see.
//
// WHY THE PILL WORDS AND THE ORDER ARE NOT IN THIS FILE. `lib/branchesView.ts`
// owns the state ladder and is tested; this file renders what it decided. That
// split is the repo's rule (the target of a test is the pure rule behind a
// behaviour, and there is no DOM harness here), and it is what stops a status
// word from being invented at a call site.
//
// TAKE OVER IS NOT OFFERED FROM THIS PAGE, and that is a real limit rather than
// an omission. `POST /control/{id}/preempt` requires `new_holder_id`: somebody
// has to receive the lease, and a page is not a session. `offerableVerbs`
// already encodes this and renders the reason as text rather than a dead
// button, which is the honest shape. Queue and steer are fully live here.

// A hundred live trees would be a different problem than this page is for. The
// count says the rest rather than the table growing without end.
const PAGE = 100;

type Filter = "attention" | "all";

const FILTERS = [
  { value: "attention" as const, label: "Needs a look" },
  { value: "all" as const, label: "All branches" },
];

export default function Branches() {
  const [filter, setFilter] = useState<Filter>("attention");
  const [selected, setSelected] = useState<string | null>(null);

  const view = useBranchesView();
  const yielding = useYieldFlags(view.rows);

  const withYield: BranchRow[] | null = view.rows === null ? null : view.rows.map((r) => ({
    ...r,
    yieldRequested: !!r.lease && yielding.has(r.lease.holder_id),
  }));

  const ordered = withYield === null ? null : orderRows(withYield, view.windows);
  const attention = withYield === null ? 0 : attentionCount(withYield, view.windows);
  const filtered = ordered === null ? null : (
    filter === "attention"
      ? ordered.filter((r) => branchState(r, view.windows).needsAttention)
      : ordered
  );
  const shown = filtered === null ? null : filtered.slice(0, PAGE);

  const selectedRow = (withYield ?? []).find((r) => r.record.id === selected) ?? null;

  const headline = driftHeadline(view.drift);
  const blocking = blockingFiles(view.drift);

  return (
    <div>
      <PageHeader
        title="Branches"
        sub="Every tree an agent can be working in, who holds it, and whether anything is stuck."
        meta={
          withYield === null ? null
            : attention > 0
              ? `${attention} of ${withYield.length} need a look`
              : `${withYield.length} branches, nothing stuck`
        }
        actions={
          <Button size="sm" variant="ghost" onClick={() => { view.reload(); }}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        }
      />

      {/* THE NINE-HOUR ITEM. A stale origin is said FIRST, because every branch
          state below it describes code the operator may not be looking at. */}
      {headline && (
        <Notice
          tone={view.drift?.blocked ? "error" : "warn"}
          icon={<AlertTriangle aria-hidden />}
          detail={
            blocking.length > 0
              ? blocking.join(", ")
              : view.drift?.apply_command
          }
        >
          {headline}
        </Notice>
      )}

      {view.drift && (
        <Card compact>
          <SectionLabel icon={<GitBranch aria-hidden />}>What is being served</SectionLabel>
          <Row wrap>
            {view.drift.surfaces.map((s) => (
              <StatusPill key={s.name} status={driftKind(s.state)}>
                {s.name}: {driftLabel(s.state)}
              </StatusPill>
            ))}
          </Row>
        </Card>
      )}

      {!view.leasesAvailable && (
        <Notice tone="info">
          This installation keeps no lease records, so nothing below can say who
          holds a tree. The branches themselves are still real.
        </Notice>
      )}

      <Row wrap>
        <Segmented
          ariaLabel="Which branches to list"
          options={FILTERS}
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
        />
      </Row>

      {/* A failed poll with rows still on screen is a LINE, not a region
          (frontend/CLAUDE.md: would this space be empty if the failure had not
          happened?). */}
      {view.err && shown !== null && shown.length > 0 && (
        <Notice
          tone="warn"
          detail={view.err}
          action={<Button size="sm" onClick={() => { view.reload(); }}>Retry</Button>}
        >
          The last read of who is working where did not land. These rows may be old.
        </Notice>
      )}

      {view.loading ? (
        <Card>
          <Stack gap="sm">
            <Skeleton width="62%" />
            <Skeleton width="48%" />
            <Skeleton width="55%" />
          </Stack>
        </Card>
      ) : view.err && (shown === null || shown.length === 0) ? (
        <StateBlock
          variant="error"
          title="Couldn't load branches"
          hint={<code>GET /api/branches ({view.err})</code>}
          action={<Button size="sm" onClick={() => { view.reload(); }}>Retry</Button>}
        />
      ) : shown === null || shown.length === 0 ? (
        <StateBlock
          icon={<GitBranch aria-hidden />}
          title={filter === "attention" ? "Nothing is stuck" : "No branches are allocated"}
          hint={filter === "attention"
            ? "No tree is idle, expired or holding uncommitted work nobody owns. Switch to All branches to see the ones that are simply being worked."
            : "A branch is allocated when work starts on a ticket, or by POST /api/worktrees/<project>. Nothing has one open right now."}
          action={filter === "attention"
            ? <Button size="sm" onClick={() => setFilter("all")}>Show all branches</Button>
            : undefined}
        />
      ) : (
        <Stack gap="sm">
          <TableScroll>
            <Table>
              <thead>
                <tr>
                  <th>Branch</th>
                  <th>State</th>
                  <th>Holder</th>
                  <th>Held</th>
                  <th>Beat</th>
                  <th>Tree</th>
                  <th>Vs base</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const state = branchState(r, view.windows);
                  const lease = r.lease;
                  return (
                    <tr key={r.record.id}>
                      <td>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setSelected(
                            selected === r.record.id ? null : r.record.id)}
                        >
                          {r.record.branch_slug}
                        </Button>
                        <div className="ws-muted ws-mono">{r.record.project_slug}</div>
                      </td>
                      <td><StatusDot status={state.kind}>{state.label}</StatusDot></td>
                      <td className="ws-mono">
                        {lease
                          ? `${lease.holder_kind} ${lease.holder_id}`
                          : <span className="ws-muted">nobody</span>}
                      </td>
                      <td className="ws-muted ws-mono">
                        {shortSeconds(lease?.held_for_sec)}
                      </td>
                      <td className="ws-muted ws-mono">
                        {lease?.state === "held" ? shortSeconds(lease.since_heartbeat_sec) : ""}
                      </td>
                      <td>
                        {r.tree === null
                          ? <span className="ws-muted">reading</span>
                          : r.tree.clean
                            ? <span className="ws-muted">clean</span>
                            : <StatusDot status="warn">
                                {r.tree.entries} uncommitted
                              </StatusDot>}
                      </td>
                      <td className="ws-muted ws-mono">
                        {r.ahead === null && r.behind === null
                          ? ""
                          : `+${r.ahead ?? 0} / -${r.behind ?? 0}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </TableScroll>

          {filtered !== null && filtered.length > PAGE && (
            <p className="ws-muted">
              {filtered.length} branches match. This list stops at the first {PAGE},
              ordered by what needs a look first.
            </p>
          )}
        </Stack>
      )}

      {selectedRow && (
        <Card>
          <SectionLabel icon={<GitBranch aria-hidden />}>
            {selectedRow.record.branch_slug}
          </SectionLabel>
          <Stack gap="sm">
            <p>{branchState(selectedRow, view.windows).detail}</p>

            {heartbeatNote(selectedRow.lease, view.windows) && (
              <p className="ws-muted">{heartbeatNote(selectedRow.lease, view.windows)}</p>
            )}

            <p className="ws-muted ws-mono">{selectedRow.record.path}</p>

            <p className="ws-muted">
              Cut from {selectedRow.record.base_ref}
              {selectedRow.record.ticket_id
                ? <> for ticket <Link
                    to={`/work/${encodeURIComponent(selectedRow.record.scope)}/${encodeURIComponent(selectedRow.record.ticket_id)}`}
                  >{selectedRow.record.ticket_id}</Link></>
                : null}
              .
            </p>

            {/* The three verbs, against the LIVE session that holds this tree.
                With no live session there is nobody to queue to, and saying so
                is better than an empty panel of dead controls. */}
            {selectedRow.holder ? (
              <HolderControls
                entry={selectedRow.holder}
                selfSessionId={null}
                worktreeId={selectedRow.record.id}
                onChanged={() => { view.reload(); }}
              />
            ) : (
              <Notice tone="info">
                No live session is here, so there is nothing to queue a request
                to. If the tree has uncommitted work, it has to be committed or
                thrown away by hand.
              </Notice>
            )}
          </Stack>
        </Card>
      )}
    </div>
  );
}
