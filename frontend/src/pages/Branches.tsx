import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronRight, GitBranch, RefreshCw, Trash2,
} from "lucide-react";
import {
  Button, Card, ConfirmDialog, CopyButton, Notice, PageHeader, Prop, Props, Row,
  Segmented, Skeleton, Stack, StateBlock, StatusDot, shortHttpError,
} from "@workshop/ui";

import HolderControls from "../components/holders/HolderControls";
import {
  useBranchesView, useYieldFlags,
} from "../components/holders/useBranchesView";
import {
  attentionCount, branchLabel, branchState, heartbeatNote, holderLink, orderRows,
  reapPlan, reapUrl, rowFacts, shortSeconds, tipAge,
} from "../lib/branchesView";
import type { BranchRow } from "../lib/branchesView";
import type { BranchesViewRead } from "../components/holders/useBranchesView";

// WHICH TREES EXIST, WHO IS IN THEM, AND HOW TO GET THERE.
//
// The question this page exists for is not "what branches are there". It is
// "is anything wedged, is anyone's work stranded, and where is the conversation
// that put it there" -- so the rows are ordered by attention, the state word
// leads every row, and a row held by a chat LINKS TO THAT CHAT.
//
// THE LINK IS THE POINT OF THE PAGE (operator, 2026-08-28: "we could have this
// branches page in a better simpler clearer form, linking to the chats. But the
// main way we'll find worktrees is through chats or agents"). This is a
// secondary surface. The primary one is the tree pill on the chat toolbar
// (`components/chat/TreePill.tsx`) and the branch picker on an agent
// (`pages/AgentBuild.tsx`); this page is the overview you open when you want to
// see all of them at once, and its job is to hand you back to the chat.
//
// THE ROW IS A BUTTON AND A LINK SIDE BY SIDE, which is why the markup is not
// a bare `.ws-listrow`. An anchor cannot be nested inside a button, and both
// verbs are real: the button opens the row's own panel (the facts, and the reap
// control), the link leaves for the conversation. The wrapper is
// `.branchrow` in styles.css and it is the whole of the extra CSS this page has.
//
// IT SHIPPED WITHOUT ANYONE LOOKING AT IT ON A SCREEN, and the operator opened
// it on a phone the next morning. What the rebuild changed, so it is not undone
// by accident:
//
//   * THE TABLE IS GONE. Eight columns inside a horizontal scroller meant a
//     `STATE` column and then a third column clipped to a single letter at the
//     right edge. `.ws-list` / `.ws-listrow` is the kit's answer and its own
//     comment is about exactly this failure: one row per item, never scrolling
//     sideways at any width. The columns that used to be clipped are on the
//     row's panel now.
//   * THE NAME IS SHORT. A 32-character hex slug wrapped over two lines with
//     its project underneath it in mono, and eleven of those was most of the
//     page. `branchLabel` keeps eight characters and an ellipsis; the whole
//     value is on the panel with a copy button.
//   * NOTHING SAYS DRIFT HERE, and no endpoint answers it any more. It was said
//     three times on one screen (a shell banner, a copy of the same forty words
//     in this body, and a card headed "WHAT IS BEING SERVED"). The operator
//     deleted the banner permanently on 2026-08-28; `/drift` and
//     `services/drift.py` went with it, because an endpoint with no reader is
//     dead weight. Do not reintroduce a drift warning here or anywhere.
//   * NO EXPLAINER SUBTITLE. A page called Branches does not need a sentence
//     defining a branch, and the operator named that sentence specifically.
//   * THE COUNT IS ON THE FILTER THAT PRODUCES IT, not under the title where it
//     could disagree with the view that is actually on screen.
//
// WHY THE STATE WORDS AND THE ORDER ARE NOT IN THIS FILE. `lib/branchesView.ts`
// owns the state ladder, the row's facts, the short name and the holder link,
// and it is tested; this file renders what it decided. That split is the repo's
// rule (the target of a test is the pure rule behind a behaviour, and there is
// no DOM harness here), and it is what stops a status word from being invented
// at a call site.
//
// TAKE OVER IS NOT OFFERED FROM THIS PAGE, and that is a real limit rather than
// an omission. `POST /control/{id}/preempt` requires `new_holder_id`: somebody
// has to receive the lease, and a page is not a session. `offerableVerbs`
// already encodes this and renders the reason as text rather than a dead
// button. Queue and steer are fully live here.
//
// REAP IS THE VERB FOR A TREE WITH NOBODY IN IT. `DELETE /api/worktrees/
// {project}/{branch}` is safe to put behind a button because the server commits
// uncommitted work to `refs/wip/<branch>` before it removes anything and refuses
// when it cannot; `reapPlan` decides whether it may be offered and what the
// dialog has to promise, so the promise and the request cannot drift apart.

// A hundred live trees would be a different problem than this page is for. The
// count says the rest rather than the table growing without end.
const PAGE = 100;

type Filter = "attention" | "all";

// ALL BRANCHES IS THE DEFAULT, and the reason is a measured failure rather than
// a preference. Opening on "Needs a look" meant the first thing this page ever
// said was an empty state reading "Nothing is stuck", and on 2026-08-27 it said
// that with eleven trees of stranded work on disk. Two different facts -- "there is
// nothing here" and "nothing here is wrong" -- rendered identically, and the
// wrong one is unfalsifiable from the screen.
//
// Opening on the whole list costs nothing that the filter was buying: the rows
// are already ordered by attention, so what needs a look is still at the top,
// and the filter itself carries both counts. The filter stays, because
// narrowing to the decisions is a real thing to want once you can see there are
// some.
const DEFAULT_FILTER: Filter = "all";

export default function Branches() {
  const [filter, setFilter] = useState<Filter>(DEFAULT_FILTER);
  const [selected, setSelected] = useState<string | null>(null);
  const now = Date.now();

  const view = useBranchesView();
  const yielding = useYieldFlags(view.rows);

  const withYield: BranchRow[] | null = view.rows === null ? null : view.rows.map((r) => ({
    ...r,
    yieldRequested: !!r.lease && yielding.has(r.lease.holder_id),
  }));

  const ordered = withYield === null ? null : orderRows(withYield, view.windows, null, now);
  const attention = withYield === null ? null : attentionCount(withYield, view.windows, null, now);
  const filtered = ordered === null ? null : (
    filter === "attention"
      ? ordered.filter((r) => branchState(r, view.windows, null, now).needsAttention)
      : ordered
  );
  const shown = filtered === null ? null : filtered.slice(0, PAGE);

  // THE NUMBER SITS ON THE CONTROL THAT PRODUCES IT. Under the title it was a
  // claim about a view that was not on screen. Here each option counts its own
  // result, so picking one can never disagree with the number beside it.
  const filters = [
    {
      value: "attention" as const,
      label: attention === null ? "Needs a look" : `Needs a look (${attention})`,
    },
    {
      value: "all" as const,
      label: withYield === null ? "All" : `All (${withYield.length})`,
    },
  ];

  return (
    <div>
      {/* NO ACTIONS SLOT. `.ws-page-header__main` is `flex: 1 1 16rem`, so at
          390px the actions could never share the title's line and Refresh got a
          line of its own plus the header's own bottom margin under it: about
          forty pixels of nothing between the page name and the first row, at
          the top of the screen this page is read on. It sits on the filter row
          instead, where there is room for it. */}
      <PageHeader title="Branches" />

      {!view.leasesAvailable && (
        <Notice tone="info">
          This installation keeps no lease records, so nothing below can say who
          holds a tree. The branches themselves are still real.
        </Notice>
      )}

      {!view.recordsAvailable && (
        <Notice tone="info">
          The branch records could not be read, so no row below can say which
          ticket it was cut for or who allocated it. The trees are still real:
          they were read from git.
        </Notice>
      )}

      <Row wrap between>
        <Segmented
          ariaLabel="Which branches to list"
          options={filters}
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
        />
        <Button size="sm" variant="ghost" onClick={() => { view.reload(); }}>
          <RefreshCw aria-hidden /> Refresh
        </Button>
      </Row>

      {/* A failed read with the BRANCH LIST still loaded is a LINE, not a
          region (frontend/CLAUDE.md: would this space be empty if the failure
          had not happened?). The list is there, so the answer below is real and
          only some of its decoration is missing. */}
      {view.err && !view.fatal && (
        <Notice
          tone="warn"
          detail={view.err}
          action={<Button size="sm" onClick={() => { view.reload(); }}>Retry</Button>}
        >
          One of the reads behind this page did not land, so some rows may be
          missing their holder or their tree state.
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
      ) : view.fatal ? (
        <StateBlock
          variant="error"
          title="Couldn't load branches"
          hint={<code>GET /api/branches ({view.fatal})</code>}
          action={<Button size="sm" onClick={() => { view.reload(); }}>Retry</Button>}
        />
      ) : shown === null || shown.length === 0 ? (
        <StateBlock
          icon={<GitBranch aria-hidden />}
          title={filter === "attention" ? "Nothing is stuck" : "No branches"}
          hint={filter === "attention"
            ? "Nothing is being stopped, nothing is holding unsaved work. Switch to All to see the trees that are simply sitting there."
            : "A branch appears here when a chat or a run starts working in one."}
          action={filter === "attention"
            ? <Button size="sm" onClick={() => setFilter("all")}>Show all branches</Button>
            : undefined}
        />
      ) : (
        <Stack gap="sm">
          {/* NO CARD AROUND THE LIST. The open row's panel IS a card, and a
              card inside a card gave the panel a second border a few pixels
              inside the first. With the rows flat, the one raised surface on
              the screen is the row you opened, which is the hierarchy the
              interaction actually has. */}
          <ul className="ws-list">
            {shown.map((r) => {
              const state = branchState(r, view.windows, null, now);
              const open = selected === r.record.id;
              const link = holderLink(r);
              return (
                <li key={r.record.id}>
                  {/* A ROW AND ITS WAY OUT. The button opens the panel under
                      it; the link leaves for whatever is working in this tree,
                      which for a chat-held tree is the conversation itself.
                      Two elements because an anchor may not live inside a
                      button, and both are real verbs. */}
                  <div className="branchrow">
                    <button
                      type="button"
                      className="ws-listrow"
                      aria-expanded={open}
                      onClick={() => setSelected(open ? null : r.record.id)}
                    >
                      <span
                        className="ws-listrow__mark"
                        title={state.label}
                        aria-label={state.label}
                      >
                        <StatusDot status={state.kind}>{null}</StatusDot>
                      </span>
                      {/* The project sits BESIDE the name, not under it: it is
                          the second half of the same identifier, and stacking
                          it cost a whole line per row. */}
                      <span className="ws-listrow__title">
                        <span className="ws-mono">{branchLabel(r.record)}</span>
                        {" "}
                        <span className="ws-muted">{r.record.project_slug}</span>
                      </span>
                      <span className="ws-listrow__meta">
                        {rowFacts(r, state).map((f) => <span key={f}>{f}</span>)}
                        {/* How long it has been sitting there, and NOTHING
                            when nothing dated it: an empty trailing span still
                            takes the meta line's separator, so a row read
                            "Unsaved work ·" with a middot pointing at nobody.
                            For an unrecorded tree this is the only age there
                            is, because nothing wrote a created_at. */}
                        {tipAge(r.record, now) && (
                          <span className="ws-listrow__time">{tipAge(r.record, now)}</span>
                        )}
                      </span>
                    </button>
                    {link && (
                      <Link className="branchrow__go" to={link.to} title={link.label}
                            aria-label={link.label}>
                        <ChevronRight size={16} aria-hidden />
                      </Link>
                    )}
                  </div>
                  {open && <BranchPanel row={r} view={view} now={now} onDone={() => {
                    setSelected(null);
                    view.reload();
                  }} />}
                </li>
              );
            })}
          </ul>

          {filtered !== null && filtered.length > PAGE && (
            <p className="ws-muted">
              {filtered.length} branches match. This list stops at the first {PAGE},
              ordered by what needs a look first.
            </p>
          )}
        </Stack>
      )}
    </div>
  );
}

/**
 * One branch opened, INLINE UNDER ITS OWN ROW.
 *
 * Not at the foot of the page, which is where it was: with thirteen rows above
 * it on a phone, tapping a row scrolled nothing and the panel that appeared was
 * off the bottom of the screen. Under the row is the only place a reader can
 * connect it to what they tapped.
 *
 * It is also where every column the list dropped went. `Props` is a responsive
 * auto-fill grid, so the eight columns that could not fit a 390px table read
 * here as stacked label/value pairs and reflow into rows on a desktop, which is
 * the design system's answer to a narrow viewport rather than a second layout.
 */
function BranchPanel({ row, view, now, onDone }: {
  row: BranchRow;
  view: BranchesViewRead;
  now: number;
  onDone: () => void;
}) {
  const state = branchState(row, view.windows, null, now);
  const lease = row.lease;
  const link = holderLink(row);
  const beat = heartbeatNote(lease, view.windows);
  const age = tipAge(row.record, now);
  return (
    <Card>
      <Stack gap="sm">
        <p>{state.detail}</p>
        {beat && <p className="ws-muted">{beat}</p>}

        {/* EMPTY CELLS ARE NOISE, which is this module's rule about the age
            column applied to the panel: a row of "Holder: nobody / Held for:
            not held / Vs main: unknown / Ticket: none" is four labels saying
            the sentence above already covered it. A fact appears when there is
            one. */}
        <Props>
          <Prop label="Branch"><CopyButton value={row.record.branch_slug} /></Prop>
          <Prop label="Tree">
            {row.record.presence !== "tree"
              // A row with no git behind it has no working tree to read, so
              // "reading" would be a promise that never resolves. The state
              // above already said which.
              ? <span className="ws-muted">no tree</span>
              : row.tree === null
                ? <span className="ws-muted">reading</span>
                : row.tree.clean
                  ? "clean"
                  : `${row.tree.entries} uncommitted`}
          </Prop>
          {age && <Prop label="Last commit">{age}</Prop>}
          {/* THE HOLDER IS A LINK WHEN IT IS SOMETHING YOU CAN OPEN. The row
              above carries the same destination as a chevron; here it is
              named, because this is where a reader has already stopped to
              read. A holder kind with no page (anything but a chat or a run)
              is plain text rather than a link that would 404. */}
          {lease && (
            <Prop label="Holder">
              {link
                ? <Link to={link.to}>{lease.holder_kind === "chat" ? "Open the chat" : "Open the run"}</Link>
                : <span className="ws-mono">{lease.holder_kind} {lease.holder_id}</span>}
            </Prop>
          )}
          {lease?.held_for_sec != null && (
            <Prop label="Held for">{shortSeconds(lease.held_for_sec)}</Prop>
          )}
          {(row.ahead !== null || row.behind !== null) && (
            <Prop label={`Vs ${row.record.base_ref}`}>
              +{row.ahead ?? 0} / -{row.behind ?? 0}
            </Prop>
          )}
          {row.record.ticket_id && (
            <Prop label="Ticket">
              <Link
                to={`/work/${encodeURIComponent(row.record.scope)}/${encodeURIComponent(row.record.ticket_id)}`}
              >{row.record.ticket_id}</Link>
            </Prop>
          )}
        </Props>

        {/* `ws-wrap` because a branch path ends in a 32-character hex token that
            no line break can fall inside, so without it the path ran off the
            right of a 390px panel exactly like the table it replaced. */}
        <p className="ws-muted ws-mono ws-wrap">{row.record.path}</p>

        {/* The three verbs, against the LIVE session that holds this tree. With
            no live session there is nothing to queue to, and the state sentence
            above has already said nobody is here: a notice repeating it would
            be on eleven of thirteen panels, which is a constant. */}
        {row.holder && (
          <HolderControls
            entry={row.holder}
            selfSessionId={null}
            worktreeId={row.record.id}
            onChanged={() => { view.reload(); }}
          />
        )}

        <ReapControl row={row} onDone={onDone} />
      </Stack>
    </Card>
  );
}

/**
 * Reap one tree, with the consequence named before it happens.
 *
 * THE DIALOG'S SENTENCE AND THE REQUEST COME FROM THE SAME DECISION.
 * `reapPlan` says whether the verb may be offered, what it will cost and
 * whether the call has to carry `force`; `reapUrl` builds the request from that
 * same plan. Deciding twice is how a confirm ends up promising a snapshot on a
 * request that could not take one.
 *
 * A REFUSAL IS AN ANSWER, NOT AN OUTAGE. The route replies 200 with
 * `ok: false` and its own `detail` when it left the tree alone (the work could
 * not be snapshotted), so that string is shown verbatim rather than being
 * re-worded here into a promise the server did not make.
 */
function ReapControl({ row, onDone }: { row: BranchRow; onDone: () => void }) {
  const plan = reapPlan(row);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!plan.offered) {
    return <p className="ws-muted">{plan.blockedBecause}</p>;
  }

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch(reapUrl(row, plan), { method: "DELETE" });
      if (!res.ok) {
        throw new Error(shortHttpError(
          res.status, res.statusText, await res.text().catch(() => "")));
      }
      const body = await res.json() as {
        ok: boolean; detail?: string; snapshot_ref?: string | null;
      };
      if (!body.ok) {
        setErr(body.detail || "The tree was left alone.");
        return;
      }
      setAsking(false);
      onDone();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack gap="sm">
      {err && (
        <Notice
          tone="warn"
          action={<Button size="sm" onClick={() => { setErr(null); }}>Dismiss</Button>}
        >
          {err}
        </Notice>
      )}
      {/* The consequence is named ONCE, in the dialog, which is the repo's
          rule for a destructive action. It used to be printed under the button
          as well, so every open panel carried the sentence whether or not
          anybody was about to press anything. */}
      <Row>
        <Button size="sm" variant="danger" onClick={() => setAsking(true)}>
          <Trash2 aria-hidden /> Reap this tree
        </Button>
      </Row>

      <ConfirmDialog
        open={asking}
        busy={busy}
        title={`Reap ${branchLabel(row.record)}?`}
        body={plan.consequence}
        confirmLabel="Reap it"
        onCancel={() => { setAsking(false); setErr(null); }}
        onConfirm={() => { void run(); }}
      />
    </Stack>
  );
}
