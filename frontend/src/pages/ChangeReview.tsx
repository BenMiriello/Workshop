import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle, Check, ChevronLeft, FileDiff, GitBranch, RefreshCw,
  ShieldCheck, Undo2,
} from "lucide-react";
import {
  Button, Card, ConfirmDialog, DetailLayout, Notice, PageHeader, Prop, Props,
  Row, SectionLabel, Skeleton, Stack, StateBlock, StatusDot, StatusPill,
  Textarea, shortHttpError, timeAgo,
} from "@workshop/ui";

import { Ref } from "../components/Ref";
import { appWorkHref } from "../lib/apps";
import DiffFile from "../components/changes/DiffFile";
import { useResolvedRefs } from "../lib/useResolvedRefs";
import { shortSha } from "../lib/sha";
import {
  FILE_LIST_CAP, changeStatusKind, changeStatusLabel, decisionRefusal,
  diffTotals, gateKind, isOpenChange, riskSignals, sizeBand, sizeLabel,
  ticketRef, ticketTargetFromHref, type Change, type ChangeDiff,
} from "../lib/changeReview";

// ONE CHANGE, REVIEWED. Step C4 of
// docs/platform-redesign/changes-and-review-2026-08-22.md.
//
// THE ORDER OF THIS PAGE IS THE ARGUMENT. The diff is first and the author's
// summary is below it, labelled as a claim. That is not a layout preference:
// the measured top defect in agent-authored changes is a description asserting
// what the diff does not contain (45.4% of inconsistent PRs, sec 9.3), and in
// this system the same worker writes the code AND the completion claim. A page
// that opens with the summary teaches the operator to accept the claim; a page
// that opens with the patch makes the claim checkable.
//
// WHAT IT DOES NOT DO. There is no merge button of its own. Accepting a ticket
// is what merges (routes/tasks_all.py `review_task` -> work_review.merge_for_
// task), so the decision controls here call THAT endpoint and nothing else. A
// second path to the trunk would be a second definition of "accepted".
//
// The claim-to-diff check (sec 5.2), the intent-beside-outcome panel (5.4) and
// the per-branch preview (5.5) are separate steps and are not here.

type DecisionTicket = {
  ticketId: string;
  scope: string;
  taskId: string;
  title: string;
  status: string;
};

export default function ChangeReview() {
  const { id = "" } = useParams();

  const [change, setChange] = useState<Change | null>(null);
  const [diff, setDiff] = useState<ChangeDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [diffErr, setDiffErr] = useState<string | null>(null);
  // The diff loads on its own clock. Without a flag of its own the page shows
  // "No files changed" in the window between the record arriving and the patch
  // arriving, which is a false statement about the change.
  const [diffLoading, setDiffLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<DecisionTicket | null>(null);
  const [decisionErr, setDecisionErr] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch(`/api/changes/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setChange((await r.json()) as Change);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [id]);

  // The diff fails on its own terms and must not take the record down with it:
  // a merged change has had its branch reaped, so the patch is gone while every
  // other fact about the change is still true and still worth reading.
  const loadDiff = useCallback(async () => {
    setDiffErr(null);
    setDiffLoading(true);
    try {
      const r = await fetch(`/api/changes/${encodeURIComponent(id)}/diff`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setDiff((await r.json()) as ChangeDiff);
    } catch (e) {
      setDiff(null);
      setDiffErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDiffLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); void loadDiff(); }, [load, loadDiff]);

  // Re-read the branch, then re-read everything derived from it. A change
  // tracks a MOVING branch, so the sha on screen is only as current as the last
  // time somebody asked git.
  const refresh = useCallback(async () => {
    setRefreshing(true);
    setDecisionErr(null);
    try {
      const r = await fetch(`/api/changes/${encodeURIComponent(id)}/refresh`, { method: "POST" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setChange((await r.json()) as Change);
      await loadDiff();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }, [id, loadDiff]);

  const ticketIds = useMemo(() => change?.ticket_ids ?? [], [change]);
  const refs = useMemo(() => ticketIds.map(ticketRef), [ticketIds]);
  const resolved = useResolvedRefs(refs);

  // Every ticket endpoint is addressed /{scope}/{id}, and a change stores ids
  // alone. The managed-ref resolver is what knows the scope, so the chips and
  // the decision controls are fed from the same one resolution.
  const decisionTickets: DecisionTicket[] = useMemo(() => {
    const out: DecisionTicket[] = [];
    for (const tid of ticketIds) {
      const hit = resolved[ticketRef(tid)];
      if (!hit) continue;
      const target = ticketTargetFromHref(hit.href);
      if (!target) continue;
      out.push({ ticketId: tid, scope: target.scope, taskId: target.id, title: hit.title, status: hit.status });
    }
    return out;
  }, [ticketIds, resolved]);

  const decide = useCallback(async (t: DecisionTicket, action: "accept" | "send-back") => {
    setBusy(`${t.ticketId}:${action}`);
    setDecisionErr(null);
    setSaid(null);
    try {
      const r = await fetch(
        `/api/tasks/${encodeURIComponent(t.scope)}/${encodeURIComponent(t.taskId)}/review`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action, note: note.trim() || null }),
        },
      );
      if (!r.ok) {
        // A 409 IS THE MERGE-CONFLICT ANSWER and its sentence names the branch
        // state the operator has to go and fix. Showing "409 Conflict" here
        // would be the generic failure this surface exists to replace.
        throw new Error(decisionRefusal(r.status, r.statusText, await r.text().catch(() => "")));
      }
      setNote("");
      setConfirm(null);
      setSaid(action === "accept"
        ? "Accepted. The branch is merged and the ticket is done."
        : "Sent back. The next attempt reads your note in the ticket body.");
      await load();
      await loadDiff();
    } catch (e) {
      setDecisionErr(e instanceof Error ? e.message : String(e));
      setConfirm(null);
    } finally {
      setBusy(null);
    }
  }, [note, load, loadDiff]);

  const files = diff?.files ?? [];
  const totals = useMemo(() => diffTotals(files), [files]);
  const risks = useMemo(() => riskSignals(files), [files]);
  const shown = files.slice(0, FILE_LIST_CAP);
  const band = sizeBand(totals.changed);
  const gates = change?.gates ?? [];

  // ---- the diff, which is the page -------------------------------------------

  const diffCard = (
    <section>
      <div className="ws-sectionhead">
        <SectionLabel icon={<FileDiff aria-hidden />}>What changed</SectionLabel>
        {diff && (
          <span className="ws-sectionhead__sub">
            {totals.files} file{totals.files === 1 ? "" : "s"} ·{" "}
            <span className="ws-diff__plus">+{totals.added}</span>{" "}
            <span className="ws-diff__minus">-{totals.removed}</span> · {sizeLabel(band)}
          </span>
        )}
      </div>

      {diffLoading ? (
        <Card compact><Stack gap="sm"><Skeleton width="70%" /><Skeleton width="50%" /><Skeleton width="60%" /></Stack></Card>
      ) : diffErr ? (
        <StateBlock
          variant="error"
          title="Couldn't read the diff"
          hint={<code>GET /api/changes/{id}/diff ({diffErr})</code>}
          action={<Button size="sm" onClick={() => void loadDiff()}>Retry</Button>}
        />
      ) : files.length === 0 ? (
        <StateBlock
          title={change?.status === "merged" ? "The branch is gone" : "No files changed"}
          hint={change?.status === "merged"
            ? "This change merged, and merging removes its branch. The patch is in the trunk's history now, at the merge commit in the rail."
            : "Git reports nothing between this branch and its base. Refresh to re-read the branch."}
          action={<Button size="sm" onClick={() => void refresh()} loading={refreshing}>Refresh</Button>}
        />
      ) : (
        <Stack gap="sm">
          {risks.length > 0 && (
            <Card compact>
              <Stack gap="sm">
                <SectionLabel icon={<AlertTriangle aria-hidden />}>Worth knowing before you read</SectionLabel>
                {risks.map((s) => (
                  <Row key={s.id} wrap className="ws-risk">
                    <StatusDot status="warn">{s.label}</StatusDot>
                    <span className="ws-risk__detail">{s.detail}</span>
                  </Row>
                ))}
              </Stack>
            </Card>
          )}

          {diff?.summary && <p className="ws-diff__shortstat">{diff.summary}</p>}

          <div className="ws-diff">
            {shown.map((f) => (
              <DiffFile key={f.path} file={f} fileCount={files.length} />
            ))}
          </div>

          {files.length > shown.length && (
            <p className="ws-diff__note">
              Showing the first {shown.length} of {files.length} files. The rest
              are on the branch; a change this wide is the argument for splitting
              it rather than for a longer page.
            </p>
          )}
        </Stack>
      )}
    </section>
  );

  // ---- the author's claim, clearly labelled ----------------------------------

  const claimCard = (
    <section>
      <SectionLabel>What the author says it does</SectionLabel>
      <Card compact>
        {change?.summary?.trim() ? (
          <Stack gap="sm">
            <p className="ws-claim__body">{change.summary}</p>
            <p className="ws-claim__note">
              Written by whoever produced the change, and not checked against the
              diff above. In agent-authored changes the most common defect is a
              description claiming work the patch does not contain, so read this
              as an assertion to verify, not as a record of what happened.
            </p>
          </Stack>
        ) : (
          <p className="ws-claim__note">
            No summary was written. The diff above is the whole record.
          </p>
        )}
      </Card>
    </section>
  );

  // ---- machine gates, honestly empty -----------------------------------------

  const gatesCard = (
    <section>
      <SectionLabel icon={<ShieldCheck aria-hidden />}>Machine checks</SectionLabel>
      <Card compact>
        {gates.length === 0 ? (
          <p className="ws-claim__note">
            No machine checks are recorded on this change. The sweep writes them
            here when the reviewer runs against a branch that has a change open,
            so a change opened by hand, or one whose reviewer run predates it,
            has none. Silence is not a pass: anything you want checked here,
            check yourself.
          </p>
        ) : (
          <Stack gap="sm">
            {gates.map((g, i) => (
              <Row key={`${g.name}-${i}`} wrap className="ws-risk">
                <StatusDot status={gateKind(g.status)}>{g.name}</StatusDot>
                {g.detail && <span className="ws-risk__detail">{g.detail}</span>}
                {g.at && <span className="ws-muted">{timeAgo(g.at)}</span>}
              </Row>
            ))}
          </Stack>
        )}
      </Card>
    </section>
  );

  // ---- the decision ----------------------------------------------------------

  const merged = change?.status === "merged";
  const decidable = decisionTickets.filter((t) => t.status === "in_review");

  const decisionCard = change && !merged ? (
    <section>
      <SectionLabel>Decide</SectionLabel>
      <Card compact>
        <Stack>
          {decisionErr && <Notice tone="error" detail={decisionErr}>That decision was refused.</Notice>}
          {said && <Notice tone="info">{said}</Notice>}

          {ticketIds.length === 0 ? (
            <p className="ws-claim__note">
              This change carries no ticket. Accepting is a decision about a
              ticket (it is what merges the branch), so there is nothing to
              decide here until one is linked.
            </p>
          ) : decisionTickets.length === 0 ? (
            <p className="ws-claim__note">
              Reading this change's tickets. If they stay unresolved they no
              longer exist, and the decision has to be made wherever they went.
            </p>
          ) : decidable.length === 0 ? (
            <p className="ws-claim__note">
              {decisionTickets.length === 1
                ? `This change's ticket is ${decisionTickets[0].status.replace("_", " ")}, not waiting on a review, so there is nothing to accept.`
                : "None of this change's tickets are waiting on a review, so there is nothing to accept."}
            </p>
          ) : (
            <>
              <Textarea
                rows={3}
                value={note}
                aria-label="What should change"
                // The next attempt READS this: it goes into the ticket body,
                // which is all a worker is handed. A bare send-back leaves the
                // next attempt guessing what was wrong, which is why the button
                // below stays disabled until there is something to read.
                placeholder="Required to send back, optional to accept. It is written into the ticket, so the next attempt reads it."
                onChange={(e) => setNote(e.target.value)}
              />
              {decidable.map((t) => (
                <Stack gap="sm" key={t.ticketId}>
                  {decidable.length > 1 && <span className="ws-muted">{t.title}</span>}
                  <Row wrap>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy != null}
                      onClick={() => setConfirm(t)}
                    >
                      <Check aria-hidden /> Accept and merge
                    </Button>
                    <Button
                      size="sm"
                      loading={busy === `${t.ticketId}:send-back`}
                      disabled={busy != null || !note.trim()}
                      onClick={() => void decide(t, "send-back")}
                    >
                      <Undo2 aria-hidden /> Send back
                    </Button>
                  </Row>
                </Stack>
              ))}
              <p className="ws-claim__note">
                Accepting merges {change.branch_slug} into {change.base_ref} and
                completes the ticket. If the merge conflicts nothing is accepted
                and the reason is shown here.
              </p>
            </>
          )}
        </Stack>
      </Card>
    </section>
  ) : null;

  const mergedCard = change && merged ? (
    <section>
      <SectionLabel>Decided</SectionLabel>
      <Card compact>
        <Notice tone="info">
          Merged{change.merged_at ? ` ${timeAgo(change.merged_at)}` : ""}
          {change.merged_sha ? ` as ${shortSha(change.merged_sha)}` : ""}. There
          is nothing left to decide.
        </Notice>
      </Card>
    </section>
  ) : null;

  // ---- rail ------------------------------------------------------------------

  const rail = change ? (
    <Stack>
      <Card compact>
        <Props>
          <Prop label="Status">
            <StatusPill status={changeStatusKind(change.status)}>
              {changeStatusLabel(change.status)}
            </StatusPill>
          </Prop>
          <Prop label="Project">
            <Link to={`/projects/${encodeURIComponent(change.project_slug)}`}>{change.project_slug}</Link>
          </Prop>
          <Prop label="Branch"><span className="ws-mono">{change.branch_slug}</span></Prop>
          <Prop label="Merges into"><span className="ws-mono">{change.base_ref}</span></Prop>
          <Prop label="Head"><span className="ws-mono">{shortSha(change.head_sha) || "unknown"}</span></Prop>
          {change.merged_sha && (
            <Prop label="Merge commit"><span className="ws-mono">{shortSha(change.merged_sha)}</span></Prop>
          )}
          {change.closed_reason && <Prop label="Closed because">{change.closed_reason}</Prop>}
          {change.updated_at && <Prop label="Updated">{timeAgo(change.updated_at)}</Prop>}
        </Props>
      </Card>

      <Card compact>
        <Stack gap="sm">
          <SectionLabel>Tickets it carries</SectionLabel>
          {ticketIds.length === 0 ? (
            <span className="ws-muted">None linked.</span>
          ) : (
            <Row wrap>
              {ticketIds.map((tid) => (
                <Ref key={tid} resolved={resolved[ticketRef(tid)]} refStr={ticketRef(tid)} />
              ))}
            </Row>
          )}
        </Stack>
      </Card>

      {change.remote?.url && (
        <Card compact>
          <Stack gap="sm">
            <SectionLabel>Mirrored</SectionLabel>
            <a href={change.remote.url} target="_blank" rel="noreferrer">
              {change.remote.kind}
              {change.remote.number ? ` #${change.remote.number}` : ""}
            </a>
          </Stack>
        </Card>
      )}
    </Stack>
  ) : null;

  if (loading && !change) {
    return (
      <div>
        <PageHeader variant="entity" title={<Skeleton width="40%" />} />
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack></Card>
      </div>
    );
  }

  if (err || !change) {
    return (
      <div>
        {/* No back link. The global /changes list was deleted on 2026-09-01
            (threads-branches-and-pages-2026-08-29.md section 6) and what
            replaced it is the Branches lens of ONE app's Work view -- which
            needs the record, and the record is exactly what failed to load
            here. Home is one tap away in the dock; a guessed app is not. */}
        <PageHeader
          variant="entity"
          title="Change"
        />
        <StateBlock
          variant="error"
          title="Couldn't load this change"
          hint={<code>GET /api/changes/{id} ({err ?? "no record"})</code>}
          action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
        />
      </div>
    );
  }

  // Back goes to the branches of the app this change is on. A change is what a
  // branch produced, and a branch is a place inside one app, so the list that
  // holds this record's siblings is that app's Work view on its Branches lens.
  // The cross-app /changes page it used to point at was deleted on 2026-09-01
  // (threads-branches-and-pages-2026-08-29.md section 6).
  const back = (
    <Link to={appWorkHref(change.project_slug, "branches")}>
      <ChevronLeft aria-hidden /> {change.project_slug}
    </Link>
  );

  return (
    <div>
      <PageHeader
        variant="entity"
        back={back}
        title={change.title || change.branch_slug}
        meta={
          <Row wrap>
            <StatusPill status={changeStatusKind(change.status)}>
              {changeStatusLabel(change.status)}
            </StatusPill>
            <span className="ws-muted">
              <GitBranch aria-hidden size={13} /> {change.branch_slug} into {change.base_ref}
            </span>
            <span className="ws-mono ws-muted">{shortSha(change.head_sha)}</span>
            {change.updated_at && <span className="ws-muted">{timeAgo(change.updated_at)}</span>}
          </Row>
        }
        actions={
          <Button size="sm" variant="ghost" loading={refreshing} onClick={() => void refresh()}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        }
      />

      {!isOpenChange(change) && change.status !== "merged" && (
        <Notice tone="warn">This change is closed. Its branch was never merged.</Notice>
      )}

      <DetailLayout
        rail={rail}
        main={
          <Stack>
            {diffCard}
            {claimCard}
            {gatesCard}
            {decisionCard}
            {mergedCard}
          </Stack>
        }
      />

      <ConfirmDialog
        open={confirm != null}
        danger={false}
        title="Merge this change?"
        body={
          <Stack gap="sm">
            <p>
              {change.branch_slug} merges into {change.base_ref} in{" "}
              {change.project_slug}, and {confirm?.title || "the ticket"} is
              completed.
            </p>
            <p className="ws-claim__note">
              {totals.files} file{totals.files === 1 ? "" : "s"}, {totals.changed}{" "}
              changed lines. If the merge conflicts, nothing is accepted and the
              reason is shown on the page.
            </p>
          </Stack>
        }
        confirmLabel="Merge and accept"
        busy={busy === `${confirm?.ticketId}:accept`}
        onCancel={() => setConfirm(null)}
        onConfirm={() => { if (confirm) void decide(confirm, "accept"); }}
      />
    </div>
  );
}
