# Who is working here: workspace ownership, leases, and takeover

- Date: 2026-08-22
- Status: **design, nothing built.** Proposal for operator review.
- Answers the question left open by
  [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
  §14: that doc decided a workspace is created when work first needs to write, and
  said nothing about what happens when a second participant wants to write to the
  same one.
- Designed for the production target: any number of users, apps and participants.
  Nothing here is sized to a particular machine.

---

## 1. What was asked

> Does the worktree/branch management need to be its own thing, decoupled from the
> chat? The ticket is coupled with it but can change. An agent, or an instance of
> an agent, can be coupled with a worktree. Have rules for not overlapping or
> duplicating ownership, but do not be overly strict: warn when appropriate, error
> when appropriate, default to new or existing as appropriate. For agents there
> should be one main one doing actual WORK at a time, and it should be the right
> kind of agent; others can communicate and coordinate with it. For chats, how do
> we coordinate if another chat or agent is already there? What if it is old or
> stopped? How do we determine if it is still working or relevant? Who has
> ownership, or the lease? When can it be swapped? If a chat wants to work while an
> agent is working, can it inject the request into the agent's workflow with
> priority, or add it to the ticket and notify the agent? Show that in the chat as
> a card, with options: do nothing, implement directly then hand back the lease, or
> implement directly and take over. If we take over, what happens to the agent's
> work? Stop it in its tracks, but keep a record that is not just text buried in a
> log. When should a new chat take over from another chat? When we make a new chat,
> how do we know which branch to put it in, and what to default to?

---

## 2. The correction that reframes all of it

**The lease already exists, is correctly built, and is invisible.**

`services/tasks.py` carries a complete lease on every ticket: `claim_token` (a
monotonic fencing generation), `lease_until`, `last_heartbeat`, `claim_pid`, a
`reclaim()` sweep, and a `LEASE_TTL_SEC`. `_is_stale_claim` already implements the
two-tier liveness test the question asks for (a dead recorded pid is reclaimed at
once; a live-but-silent worker is reclaimed only when its heartbeat lapses). And
`update(..., expect_claim_token=...)` already implements takeover: an operator write
bumps the generation so the displaced worker's next write is **fenced out** rather
than the operator being blocked.

That is the textbook design (Kubernetes `Lease` + fencing tokens, §11.1), and it is
already here.

**Two cautions on reusing it, both important.**

*It is a different concept from workspace ownership.* The ticket claim answers
"which worker has taken this work" and prevents duplicated effort. A workspace lease
answers "who may write to this tree" and prevents a corrupted checkout. They are not
the same question and one does not imply the other. What carries over is the
**pattern** (single holder, fencing generation, TTL, heartbeat renewal, expiry
without a negotiation), not the queue semantics.

*Part of it does not generalize past a single node.* `_is_stale_claim`'s fast path
calls `_pid_alive`, which is `os.kill(pid, 0)`: a same-host signal probe. On one
machine that is a good optimization, because a crashed worker is reclaimed instantly
rather than after a TTL. Across hosts it is wrong in both directions, since a pid
recorded on one host and probed on another is either absent (reported dead while the
worker is running) or coincidentally reused by something unrelated (reported alive
after the worker died). So the general form of liveness is **heartbeat and expiry
only**, with process-probing kept as an optional single-node accelerator that a
multi-host profile turns off. Any design that treats the pid check as part of the
model has smuggled one deployment's implementation into the architecture.

So this is not a new subsystem, but it is also not a free one. Three things are
genuinely missing:

1. The lease protects **the work item**, not **the working tree**. Two participants
   on different tickets in the same tree still corrupt each other.
2. Nothing **shows** it. There is no answer on screen to "who is working here."
3. There is no **negotiation**: the only outcomes today are wait, or take by force.

## 3. Three nouns, and only one of them is durable

The whole model needs three records, and the discipline is about which ones are
allowed to be cheap.

- **Workspace** — a working tree at a branch. The *resource*. Durable, reapable,
  independent of who is looking at it. This is the thing that gets a lease.
- **Work** — a ticket. What the workspace is *for*. Durable. May move between
  workspaces; a workspace may carry more than one (§7.3).
- **Session** — one participant: an agent run, or a chat. **Cheap, disposable,
  never an owner.** A session may *hold* a lease; it never *owns* a workspace.

The load-bearing decision, and the direct answer to "can people keep spinning up new
chats without worrying":

> **A chat never owns a workspace. Sessions are disposable; workspaces are not.**

Closing a chat, abandoning it, or opening six about the same thing must cost
nothing and orphan nothing. That is only true if the durable things (the tree, the
branch, the work) are attached to the workspace and the ticket, and the chat is a
view. This is also why a workspace is **not** created per chat: it is created per
unit of work.

## 4. One lease, one writer, two admissions

**At most one session may write to a workspace at a time.** Everyone else is a
reader. This is single-writer, the same answer every collaborative system reaches
(§11.2), and the operator's instinct that one agent should be doing the actual work
is correct.

There are two distinct admissions and they must not be merged:

- The **ticket claim** (exists) answers *may I take on this work* and stops two
  sweeps working one ticket.
- The **workspace lease** (new) answers *may I write to this tree* and stops two
  participants corrupting one checkout.

They are different resources and can legitimately be held by different parties: an
operator can hold the tree while a worker still holds the ticket. But they must be
**one mechanism with two subjects**, not two implementations. So the concrete build
is: extract the lease machinery already inside `tasks.py` into a `leases` module
(holder, fencing generation, TTL, heartbeat, stale detection, reclaim), and make the
ticket claim its first caller and the workspace lease its second. Mechanism count
goes from one-buried-in-tasks to one-shared. It is a refactor, not an addition.

## 5. Liveness: answering "is it still working?" mechanically

The question "what if the other one is old or stopped" must never be answered by
judgement. Three states, all computed:

- **Live** — heartbeat within TTL *and* the recorded process alive. Already built.
- **Idle** — process alive, no *work event* within a shorter soft window. Takeable
  with a notice, not a negotiation.
- **Expired** — heartbeat lapsed or process dead. The lease is simply gone. Taking
  it needs no permission and no prompt, because there is nobody to ask.

Two rules that are easy to get wrong, both learned from other systems:

- **Heartbeat on work, not on presence.** A session with a window open is not
  working. The heartbeat must be driven by actual events (a tool call, a write, a
  commit), which is what `ralph.sh` already does from inside its loop. Gitpod's
  well-documented failure is the inverse: workspaces timed out because the nominal
  *owner* was idle while other participants were actively working (§11.3). Liveness
  is a property of the work, not of the owner's keyboard.
- **A held lease is not a running turn.** A chat can hold the lease while thinking.
  Preempting a *lease* and interrupting a *turn* are different acts (§6).

## 6. The three verbs, and why there is no priority number

When a session wants to write and someone else holds the lease, there are exactly
three things it can do. These are the names the field has converged on (§11.4), and
using the standard names is most of the design:

- **Queue** — record the request against the ticket. The holder picks it up when it
  finishes. Nothing is interrupted. *The default.*
- **Steer** — inject the request into the holder's **running turn**, delivered at
  the next safe boundary (between tool calls). The holder changes direction and
  keeps its context and its work. Nothing is lost, nothing restarts.
- **Preempt** — stop the holder, take the lease.

**Steer is the answer to "the user wants this now", not preempt.** That is the
insight the question was circling: immediacy does not require destroying work. Steer
gets the operator what he wants in seconds while keeping the run alive. Preempt
should be rare and should feel heavy.

And a deliberate omission: **no priority numbers.** A numeric priority is a knob
nobody can calibrate and every participant inflates. The verb *is* the priority:
later (queue), now (steer), or instead (preempt). Two of the three are already
half-built here, since a chat message that arrives mid-turn already joins the
running turn rather than starting a new one.

Who may use which:

- **The operator may always preempt.** He is the principal; there is no case where
  the system should tell him to wait.
- **An agent may never preempt another agent.** It queues, or it steers if it has a
  channel. This single rule removes an entire class of thrash and needs no policy
  engine to enforce.

## 7. Preemption: how work survives being stopped

The worry that stopping an agent loses its work is right, and the answer is not a
better log. It is the Kubernetes preemption shape (§11.5): a preempted holder gets a
**grace window whose only job is to make its work durable**, then it is force-stopped.

> **Yield means commit.** On preemption the holder commits whatever it has to its
> own branch as a work-in-progress commit and releases the lease.

That turns "what happened to its work" from a text-recovery problem into a **SHA**.
It is diffable, revertable, resumable, and it needs no special storage. If the grace
window lapses, the holder is killed and the uncommitted diff simply stays in the
workspace, which is the ordinary state any developer leaves a machine in; nothing is
destroyed either way.

The record then belongs on the **workspace timeline** as structured events, not
buried in a transcript: `lease acquired by <session>`, `steered by <session>`,
`preempted by <operator>, holder committed <sha>, N files`. That timeline is the
thing the chat card links to. This is the "not just textual info lost somewhere
deep" requirement, met by giving the event a record of its own.

## 8. Defaults: what a new chat gets

The rule, in order, with no prompt in the common case:

1. **Opened from a ticket, run, or workspace** → that workspace. Inherited, shown,
   changeable.
2. **Otherwise** → the app's default ref, as a **reader**. No workspace, nothing
   provisioned. Most chats never leave this state.
3. **On its first write**, in order: if the work it is doing already has an open
   workspace, join it (and take the lease if free); if the lease is held, §6; if
   there is no workspace, create one.
4. **Writing with no ticket at all** is common and must not be second-class. It gets
   a scratch workspace, which a ticket can adopt later. The alternative (forcing a
   ticket before any edit) is bureaucracy the working agreement forbids.

So "which branch does a new chat go in" is not a question the operator answers. He
picks the *work*; the branch follows. The ref pill shows what was picked and lets
him change it, which satisfies "give option or insight while a chat is starting or
going" without a modal.

### 8.1 Two smaller rules worth stating now

- **A workspace merges as one unit.** If it carries several tickets, they land
  together. Otherwise the first merge strands the others' commits mid-branch.
- **Two of the operator's own chats do not contend.** One person is not two
  principals. The newest surface simply wins, silently. Arbitration is only needed
  when the other holder is a *running turn*, and that is §6, not a chat-versus-chat
  policy. Building a takeover negotiation between a person and themselves is exactly
  the multi-user guardrail cruft this system forbids.

## 9. Assessment of the proposed reasoning

Asked for directly, so answered directly.

**Right, and I would not change it:** decoupling workspace management from the chat;
a lease as the primitive; one agent doing the actual work at a time with others
coordinating; stopping a preempted agent immediately rather than letting it finish;
insisting on a record; and refusing to be overly strict.

**Four things I would change:**

1. **No priority numbers** (§6). The verb carries the urgency. A priority scalar is
   uncalibratable and gets inflated by every caller.
2. **Steer, not take-over, as the default for "I want this now"** (§6). The listed
   options went straight from "do nothing" to "take the lease", skipping the cheap
   middle that every agent harness has converged on.
3. **The record should be a commit, not a log entry** (§7). "Keep a record in the
   agent log" accepts that the work is lost and only the story survives. Committing
   on yield keeps the work.
4. **Do not arbitrate between two of the operator's own chats** (§7.3). That
   question dissolves rather than needing an answer.

**One thing under-weighted, and it is the biggest correctness risk here:** readers
go stale. A chat that is *watching* a workspace someone else is writing is reading a
tree that changes underneath it. It will confidently describe a file that was
rewritten two minutes ago, and unlike a merge conflict nothing will flag it. Every
non-holder needs a "the tree moved since you read it" signal, and any read the
holder's writes invalidate must be re-taken before it is acted on. This is a bigger
practical hazard than contention, because contention is loud and staleness is
silent.

## 10. What else this needs that was not asked

- **Drift from base.** A workspace that lives for days diverges from the trunk. It
  needs a visible ahead/behind count and a rebase prompt, or merges get expensive
  exactly when the work is most valuable.
- **Restart survival.** This backend restarts often. TTL plus heartbeat covers a
  crash, but the *grace window* must also survive one: a preempt interrupted by a
  restart must resume and complete rather than leaving a half-yielded lease.
- **Visible holder.** "Who is working here" must be answerable without opening
  anything: a holder shown on the ref pill, the way presence works everywhere else.
- **Reaping unmerged work.** The existing sweep reaps *merged* workspaces. The hard
  case is an abandoned workspace with unmerged commits, which must be surfaced
  before it is destroyed.

## 11. Research appendix

### 11.1 Leases, fencing, liveness
Kubernetes `coordination.k8s.io/Lease`: a time-bound grant to one identity, renewed
by heartbeat, expiring silently when the holder dies. Paired with a monotonically
increasing fencing token validated at the storage layer, because a paused holder
that wakes after expiry will otherwise keep writing alongside the new one. This is
exactly the shape `tasks.py` already implements.
- kubernetes.io/docs/concepts/architecture/leases/
- singhajit.com/distributed-systems/lease/ ; acritelli.com/blog/kubernetes-leases/

### 11.2 Single-writer and presence
Figma's multiplayer is server-authoritative with presence (cursors, selection,
follow), and permission is a separate axis from presence. The generalizable part is
that *who is here* and *who may write* are two different questions, both of which
must be visible.
- figma.com/blog/how-figmas-multiplayer-technology-works/

### 11.3 Session-to-workspace binding
Gitpod and Codespaces bind many client sessions to one workspace with an idle
timeout reset by real activity. Their documented failure modes are the design
lessons: a workspace timed out while collaborators were active because only the
*owner's* idleness was measured, and the lifecycle assumed a single client
connection per workspace.
- gitpod.io/docs/configure/workspaces/workspace-lifecycle
- github.com/gitpod-io/gitpod issues 7350, 10373 ; docs.github.com Codespaces timeout

### 11.4 Steer vs queue vs preempt
The agent-harness field has converged on distinct delivery semantics: *steering*
injects into the in-flight turn at a safe boundary between tool calls; *queue*
delivers when the current work finishes; a *priority interrupt* channel stops the
tool chain outright. Naming these separately is what makes the behaviour predictable.
- github.com/anthropics/claude-code issues 30492, 64624, 71726
- github.com/langchain-ai/deepagents issue 1390 ; ably.com/blog/ai-transport-redirect-steering

### 11.5 Preemption with a grace window
Kubernetes preemption evicts a lower-priority pod and gives it its graceful
termination period before force-kill; GitHub Actions concurrency groups cancel a
superseded in-progress run, and deployments opt out of cancellation instead of
queueing behind it. Both are the "stop it, but give it a moment to become durable"
shape.
- kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/
- docs.github.com Actions concurrency ; starsling.dev/best-practices/github-actions/cancel-superseded-runs

## 12. Build sequence, if this is accepted

Ordered so each stage is usable alone. Not filed: the model wants review first.

1. **Extract `leases`** from `tasks.py` and make the ticket claim its first caller.
   Pure refactor, no behaviour change, provable by the existing tests.
2. **Workspace record + lease** (§4) with the three liveness states (§5).
3. **Show the holder** (§10) on the ref pill. Read-only, and it is what makes every
   later stage debuggable.
4. **Queue and steer** (§6), reusing the mid-turn join that chat messages already
   use, plus the ticket note path.
5. **Preempt with commit-on-yield** (§7) and the workspace timeline.
6. **Chat defaults** (§8): inherit, read-only until first write, join-or-create.
7. **Staleness signals** (§9) for readers, and drift-from-base (§10).
