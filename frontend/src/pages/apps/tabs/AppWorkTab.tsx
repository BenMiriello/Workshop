// The Work tab: every branch open on this app, one row each.
//
// threads-branches-and-pages-2026-08-29.md section 6, "Per app", verbatim: "A
// branch is a row. Collapsed it shows the name, the ticket, and the holder.
// Expanded it shows the threads on it and the state of its change. Each thread
// row carries its own controls: armed on/off, its role, and whether it holds the
// lease." Section 7 step 3 is this change.
//
// THIS FILE RENDERS AND DECIDES NOTHING. The grouping, the state word, the
// ordering and every sentence are `lib/workView.ts`, which is executed by
// workView.test.ts. That split is the repo's rule (there is no DOM harness, so
// the target of a test is the pure rule behind a behaviour) and it is what stops
// a status word being invented at a call site.
//
// FOUR READS, ONE OF THEM PRIMARY. The BRANCH RECORDS decide the four list
// states: no records is an empty list, a failed read is the error region, and
// nothing else on this page has rows without them. The other three (who is live,
// this app's chats, this app's changes) decorate those rows, so a failure in one
// of them is a LINE beside content that is still on screen and not a region over
// it (frontend/CLAUDE.md, "A region or a line").
//
// NO TABLE. It renders in a narrow column and the branch list that came before
// it shipped as an eight-column table inside a horizontal scroller, which on a
// phone clipped the state column to a single letter. `.ws-list` / `.ws-listrow`
// is the kit's answer: one row per branch at any width, with everything the row
// cannot hold on the panel that opens under it.
//
// TAKE OVER IS NOT OFFERED HERE, and that is a real limit rather than an
// omission. `POST /control/{id}/preempt` requires `new_holder_id`: somebody has
// to receive the lease, and a page is not a session. `HolderControls` already
// encodes this and renders the reason as a sentence rather than a dead button.
// Queue and steer are fully live.
import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, GitBranch, GitPullRequestArrow, MessagesSquare } from "lucide-react";
import {
  Button, Card, CopyButton, Notice, Prop, Props, Row, SectionLabel, Stack,
  StatusDot, StatusPill, Tag, catFor, shortHttpError,
} from "@workshop/ui";

import type { AppRecord } from "../../../lib/apps";
import { changesUnavailable, type Change } from "../../../lib/changeReview";
import { ticketHref } from "../../../lib/tasks";
import {
  THREAD_ARMED_NOTE, branchName, buildWorkRows, changeNote, changesAddr,
  noChangeNote, rowFacts, rowState, sortRows, threadRole, threadStatus,
  ticketLabel, workQueryKeys,
  type WorkAgent, type WorkRow, type WorkThread,
} from "../../../lib/workView";
import { useBranches, useDirectory } from "../../../components/holders/useHolders";
import HolderControls from "../../../components/holders/HolderControls";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";
import { queryKeys } from "../../../lib/queryKeys";

// The list route caps at 1000 and defaults to 200. A branch with no change in
// the first page reads as a branch with no change, so this asks for the cap the
// route will actually serve rather than a number that could silently truncate
// the join.
const CHANGES_LIMIT = 500;

type ChangesRead = { changes: Change[]; available: boolean };

function WorkBody({ app }: { app: AppRecord }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set<string>());

  const branches = useBranches({ projectSlug: app.project_slug, appId: app.id });
  const directory = useDirectory();

  const chats = useQuery({
    queryKey: queryKeys.apps.chats(app.id),
    queryFn: async (): Promise<WorkThread[]> => {
      const r = await fetch(`/api/apps/${app.id}/chats`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return ((await r.json()) as { chats?: WorkThread[] }).chats ?? [];
    },
  });

  // The roles. A thread's role is its agent's role, which is the only join on
  // the wire today; see `threadRole`.
  const agents = useQuery({
    queryKey: queryKeys.apps.agents(app.id),
    queryFn: async (): Promise<WorkAgent[]> => {
      const r = await fetch(`/api/apps/${app.id}/agents`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return ((await r.json()) as { agents?: WorkAgent[] }).agents ?? [];
    },
    enabled: !!app.project_slug,
  });

  const addr = changesAddr({ projectSlug: app.project_slug, appId: app.id });
  const changes = useQuery({
    queryKey: workQueryKeys.changes(addr),
    queryFn: async (): Promise<ChangesRead> => {
      if (!addr) return { changes: [], available: true };
      const r = await fetch(`/api/changes?${addr}&limit=${CHANGES_LIMIT}`);
      // A 503 is a store that was never configured, not a failed load, and it
      // must not render as "couldn't load, retry" with a button that cannot
      // help (changeReview.changesUnavailable).
      if (changesUnavailable(r.status)) return { changes: [], available: false };
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return { changes: ((await r.json()) as { changes?: Change[] }).changes ?? [], available: true };
    },
  });

  const changesAvailable = changes.data?.available ?? true;
  const rows = branches.records === null ? null : sortRows(buildWorkRows({
    records: branches.records,
    entries: directory.entries ?? [],
    threads: chats.data ?? [],
    changes: changes.data?.changes ?? [],
  }));

  // One line for every read that is NOT the branch list. Whichever failed, the
  // rows are still on screen and one thing about them is missing, so this is a
  // sentence beside them rather than a region over them.
  const decoration = [
    directory.err ? "who is on each branch" : null,
    chats.isError ? "the chats on each branch" : null,
    agents.isError ? "the role each chat is working under" : null,
    changes.isError ? "the state of each change" : null,
  ].filter(Boolean) as string[];

  const reloadAll = () => {
    branches.reload();
    directory.reload();
    void chats.refetch();
    void agents.refetch();
    void changes.refetch();
  };

  const toggle = (id: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (!next.delete(id)) next.add(id);
    return next;
  });

  return (
    <section>
      <SectionLabel icon={<GitBranch aria-hidden />}>Branches</SectionLabel>

      {decoration.length > 0 && (
        <Notice
          tone="warn"
          action={<Button size="sm" onClick={reloadAll}>Retry</Button>}
        >
          {`One read did not land, so the rows below are missing ${decoration.join(", ")}.`}
        </Notice>
      )}

      <CollectionBody
        items={rows}
        err={branches.err}
        reload={reloadAll}
        endpoint="/api/branches"
        emptyTitle="No branches"
        emptyHint={unlinkedReason("work", app.project_slug, 0)
          ?? "A branch appears here when a chat or a run starts working in one."}
        overflowHint={<>A branch leaves this list when it is merged or reaped.</>}
        render={(shown) => (
          <ul className="ws-list">
            {shown.map((r) => (
              <BranchRow
                key={r.record.id}
                row={r}
                agents={agents.data ?? []}
                changesAvailable={changesAvailable}
                appSlug={app.slug}
                open={open.has(r.record.id)}
                onToggle={() => toggle(r.record.id)}
                onChanged={reloadAll}
              />
            ))}
          </ul>
        )}
      />
    </section>
  );
}

/**
 * One branch, collapsed to three facts and opening onto everything else.
 *
 * The whole row is the button, so the panel is reachable by Tab and opened by
 * Enter or Space with no key handling of its own, and `aria-expanded` /
 * `aria-controls` say what it does and what it opens. The caret is a CONTROL, so
 * it is neutral and takes the meta line's ink (frontend/CLAUDE.md, "a control is
 * not content").
 */
function BranchRow({
  row, agents, changesAvailable, appSlug, open, onToggle, onChanged,
}: {
  row: WorkRow;
  agents: WorkAgent[];
  changesAvailable: boolean;
  appSlug: string;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const state = rowState(row);
  const panelId = `work-branch-${row.record.id}`;
  const Caret = open ? ChevronDown : ChevronRight;

  return (
    <li>
      <button
        type="button"
        className="ws-listrow"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <span className="ws-listrow__mark" title={state.label} aria-label={state.label}>
          <StatusDot status={state.kind}>{null}</StatusDot>
        </span>
        <span className="ws-listrow__title" title={row.record.branch}>
          {branchName(row.record)}
        </span>
        <span className="ws-listrow__meta">
          {rowFacts(row, state).map((f) => <span key={f}>{f}</span>)}
          <span className="ws-listrow__time">
            <Caret size={16} strokeWidth={1.8} aria-hidden />
          </span>
        </span>
      </button>

      {open && (
        <BranchPanel
          id={panelId}
          row={row}
          agents={agents}
          changesAvailable={changesAvailable}
          appSlug={appSlug}
          detail={state.detail}
          onChanged={onChanged}
        />
      )}
    </li>
  );
}

/**
 * The open branch, INLINE UNDER ITS OWN ROW.
 *
 * Not at the foot of the list: with a dozen rows above it on a phone, a panel
 * that appears at the bottom is off screen, and a reader cannot connect it to
 * what they tapped.
 */
function BranchPanel({
  id, row, agents, changesAvailable, appSlug, detail, onChanged,
}: {
  id: string;
  row: WorkRow;
  agents: WorkAgent[];
  changesAvailable: boolean;
  appSlug: string;
  detail: string;
  onChanged: () => void;
}) {
  const ticket = ticketLabel(row.record);
  const change = changeNote(row.change);

  return (
    <Card id={id}>
      <Stack gap="sm">
        <p style={{ margin: 0 }}>{detail}</p>

        {/* An empty cell is noise, so a fact appears when there is one. The
            branch's full slug is here because the row shows an abbreviation of
            it, and it is the argument to every branch command. */}
        <Props>
          <Prop label="Branch"><CopyButton value={row.record.branch_slug} /></Prop>
          {ticket && (
            <Prop label="Ticket">
              <Link className="ws-link" to={ticketHref(row.record.scope, row.record.ticket_id ?? "")}>
                {ticket}
              </Link>
            </Prop>
          )}
        </Props>

        <div>
          <SectionLabel icon={<MessagesSquare aria-hidden />}>Chats on this branch</SectionLabel>
          {row.threads.length === 0 ? (
            <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
              No chat is on this branch. A chat joins one from its own branch pill.
            </p>
          ) : (
            <Stack gap="sm">
              {row.threads.map((t) => (
                <ThreadRow key={t.id} row={row} thread={t} agents={agents} appSlug={appSlug} />
              ))}
            </Stack>
          )}
          <p className="ws-muted" style={{ margin: "var(--space-2) 0 0", fontSize: "var(--fs-xs)" }}>
            {THREAD_ARMED_NOTE}
          </p>
        </div>

        <div>
          <SectionLabel icon={<GitPullRequestArrow aria-hidden />}>Change</SectionLabel>
          {change && row.change ? (
            <Stack gap="sm">
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <StatusPill status={change.kind}>{change.label}</StatusPill>
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{change.detail}</span>
              </Row>
              <Link className="ws-link ws-truncate" to={`/changes/${encodeURIComponent(row.change.id)}`}>
                {row.change.title || "Review this change"}
              </Link>
            </Stack>
          ) : (
            <p className="ws-muted ws-wrap" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
              {noChangeNote(row, changesAvailable)}
            </p>
          )}
        </div>

        {/* Queue, steer and the reason take-over is not offered, against the
            LIVE session holding this branch. With nobody here there is nothing
            to queue to, and the sentence at the top of the panel has already
            said so. */}
        {row.holder && (
          <HolderControls
            entry={row.holder}
            selfSessionId={null}
            worktreeId={row.record.id}
            onChanged={onChanged}
          />
        )}
      </Stack>
    </Card>
  );
}

/**
 * One chat on this branch, with the two controls it can honestly carry.
 *
 * WHETHER IT HOLDS THE LEASE is the fact this row exists for: only the holder
 * writes (design section 5), so it is the one thing that distinguishes three
 * chats pointed at the same branch. THE ROLE is a `Tag`, which is the kit's
 * categorical treatment and not a status: a role is which kind of work this is,
 * not how it went.
 *
 * THERE IS NO ARMED SWITCH, and its absence is stated once under the list rather
 * than as a dead control on every row. `chat_thread` carries no `armed` column
 * (design section 7 step 1, not landed), so the switch would write nowhere.
 */
function ThreadRow({
  row, thread, agents, appSlug,
}: {
  row: WorkRow;
  thread: WorkThread;
  agents: WorkAgent[];
  appSlug: string;
}) {
  const role = threadRole(thread, agents);
  const status = threadStatus(row, thread);
  const scope = (thread.context_key || thread.scope || appSlug).trim() || appSlug;

  return (
    <Row wrap between style={{ gap: "var(--space-2)", alignItems: "center" }}>
      <Link
        className="ws-link ws-truncate"
        style={{ fontSize: "var(--fs-sm)" }}
        to={`/chat/${encodeURIComponent(scope)}?thread=${encodeURIComponent(thread.id)}`}
        title={thread.title}
      >
        {thread.title || "Untitled chat"}
      </Link>
      <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
        {role && <Tag hue={catFor(role)} variant="neutral">{role}</Tag>}
        {status && <StatusDot status={status.kind}>{status.label}</StatusDot>}
      </Row>
    </Row>
  );
}

export default function AppWorkTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Work" sub="Every branch open on this app.">
      {(app) => <WorkBody app={app} />}
    </AppTabPage>
  );
}
