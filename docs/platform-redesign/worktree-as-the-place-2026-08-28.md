# The worktree is the place, and nobody picks one

**Status: proposal, 2026-08-28.** Rides on
[`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
§14 (branch on write) and
[`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
§7, §10, §14. It changes neither model. It settles the **interaction contract** those
two docs left as "a ref pill", which is the part that has now been built twice and
read wrong both times.

---

## 1. What was said

Operator, 2026-08-28, on the tree control that shipped this morning:

> "I notice you mentioned only branches and never work trees when I mean I realize I
> was talking about branches too, but I mean just as important is which work tree you
> are working in. And I think there is a fundamental flaw here where it is too easy to
> switch the work tree, or rather it's presented as though it's a casual change you can
> just switch around any time, which I'm not saying shouldn't be allowed, but it just
> doesn't need to be like a regular thing that you do."

> "you have a 'where this chat works' section, which is like a perfect example of how
> you name things in a stupid way... We have this one that's called shared tree main.
> Okay, so it means that we're working on main. Why are there two terms here? Is shared
> tree the name of the work tree? Or is it something else? and then all of the branches
> below have just a long sort of string of letters and numbers as the name."

> "what does it mean to be able to switch to a different branch, and why would we be
> able to? because it's also not just switching to another branch, right? Isn't it like
> switching to a different work tree?"

And the trap he names before anyone can walk into it:

> "everything I'm saying would lead us naturally, probably to separate out the work tree
> and the branch and the lease and sort of indicate all three of those things. And that
> wouldn't be a terrible reasoning. However, I think if we were to just go in that
> direction, that's a little bit naive... it's like we would just be giving them all of
> the tools, which seems like a good thing. But in reality, it just makes things sort of
> shapeless and confusing."

Four defects, in his order: the control names the wrong noun; its words are invented
and doubled; switching is offered as a casual act when it is not one; and the obvious
fix (expose worktree + branch + lease as three controls) is worse than the disease.

---

## 2. The decision

> **The worktree is the place work happens. The branch is its shadow, named after it
> and never named separately. The lease is who is in the room right now, shown but
> never managed. And nobody picks a worktree: the first write makes one.**

Three nouns collapse to one visible thing plus one piece of status. That is the answer
to "don't give them all the tools": the tools stay, one level down, on the worktree's
own detail view, where somebody who wants to rename a branch or hand off a lease can
find them. The default path never mentions them.

### 2.1 Why this is not a new model

§14.1 of the branches doc already decided **branch on write**: a scope's canonical tree
is the shared read view; a worktree is a write view, created the moment work first
needs to write. §10.3 of the leases doc already decided that a first write **creates a
new branch by default** rather than joining one.

What was never built is the half that makes those decisions invisible: the creation is
still a thing a person does through a picker, and the created thing is still named
after a database id. So the operator meets the machinery instead of the work. This
proposal builds the missing half and deletes the picker from the normal path.

His own instinct in the same message ("I'm not necessarily requesting that we need to
do all of the work in a work tree, but I guess maybe that's what our system sort of
requires now") lands within a hair of §14.1 and is worth stating precisely, because the
difference matters: **a chat that only reads never gets a tree; a chat that writes
always does, without being asked.** In practice that means every working chat is in a
worktree and no one ever chose one.

---

## 3. The words

The words in the shipped control are the ones being replaced. Each replacement is a
word already in the operator's own vocabulary.

| shipped today | replaced by | why |
|---|---|---|
| "Where this chat works" | *(no heading)* | A menu of trees needs no label saying it is a menu of trees. |
| "Shared tree · main" | **Main** | One name, not two. The trunk is called what its branch is called. |
| `t-30777f91367b4c...` | **the work's own name** | A worktree is named from the ticket or the chat that made it: "chat toolbar fixes". The branch becomes `wt/development/chat-toolbar-fixes`. |
| "branch" as the noun | **worktree** as the noun | He asked for it directly. Branch stays a real word for a real thing, on the detail view. |

**Rule: no worktree is ever created without a human-readable name.** Today every one of
the eleven on disk is named `t-<32 hex>` or bare hex, because `allocate_task_worktree`
passes the ticket id as the slug and `worktrees._kebab_branch_slug` (which exists, and
does exactly the right thing) is never given the title. That is a one-caller fix and it
is the single highest-value change in this document, because it is why the menu reads
as noise.

---

## 4. The default flow, end to end

1. A chat opens. It resolves to the canonical tree, read-only, and shows **Main**. No
   lease, no record, no cost. Most chats stop here forever (leases §10.2).
2. The turn tries to write. The mount refuses it (`EROFS`, B1), and
   `build_streaming._maybe_write_blocked` catches the first refusal.
3. The platform creates a worktree **named from the chat's title or the ticket's
   title**, pins the chat to it, takes the lease, and continues the same turn. The
   transcript says one line: *"Working in a new worktree: chat toolbar fixes."*
4. The pill now reads **chat toolbar fixes**. It is a statement of where you are, not a
   control you are expected to operate.
5. The work merges (changes-and-review doc), and the worktree goes to **Done**.

Nobody was asked a question. That is the whole design.

**The one place a person does choose** is when opening work that already has a home: a
ticket that has a worktree, or a chat opened from a branch. That is leases §10.1
(inherited, shown, changeable) and it needs no picker either, because the choice is
made by what you opened.

---

## 5. Switching, demoted

Switching stays possible and stops being the menu's main event. The menu becomes:

- **Row 1, not a control:** the worktree this chat is in, with its state and, when
  someone else is in it, who.
- **Then:** *Move this chat to…* — a submenu, not a list of eleven rows in your face.
  Moving a chat with uncommitted work in its current tree asks first, naming what
  would be left behind.
- **Then:** *Open the worktree* — the detail view, where branch, lease, diff, merge and
  discard live. This is the "all the tools" surface, one level down.

Overflow behaviour stays as specified: at most a handful of rows, then a count and a way
through to the full list.

---

## 6. The lease, and the operator's override

Shown, never managed. The pill carries a dot when someone else is in the tree; the row
says who; tapping the holder opens their chat. There is no lease control on the pill.

**New rule, from today's message:** *"if the user is in a chat and it is user operated,
the chat, and it is direct executing a direct user message, then that should be able to
override the lease."*

This is consistent with leases §8 ("authority is bound to a live human action, not to a
session kind"), which already forbids an agent preempting an agent and already permits a
human-carried request to preempt. What it adds is that the operator should not have to
click a **Take over** button: sending a message in his own chat IS the human action.

So: a turn started by a direct operator message in an attended chat **preempts** the
holder through the existing snapshot-and-quiesce path (leases §9) rather than blocking.
The holder's work is snapshotted to `refs/wip/<session-id>` before the tree changes
hands, which is already built and verified. The transcript states plainly what was taken
and from whom, and the worktree timeline records it. An unattended run in the same
position still queues.

---

## 7. Outside the chat

**Project / app view.** The worktree belongs on the second row of the secondary bar,
not beside the title: the title row is already full, and this is a statement about
where the page is pointed rather than part of the page's name. Same pill, same menu.

**The chats page.** ~~The list groups by worktree.~~ **AMENDED 2026-08-29, see the correction at the end of this document.** The list groups by worktree.
A worktree row carries its name, its state, and its chats; the common case (one chat)
links straight through, and the row expands when there is more than one. A chat with no
worktree (read-only, on Main) sits ungrouped under Main. This makes the worktree
visible without a page of its own, which is what he asked for: *"the main way we'll find
worktrees is through chats or agents."*

**The Branches page.** He is right that he never asked for it. Its content is not
worthless (it is the only place that reads the disk), but it is a second answer to a
question the chats page will answer better. Proposal: **fold it into the chats page as
a view, and delete the nav entry.** Nothing else links to it.

---

## 8. What this retires

Per the working agreement, an addition pays for itself:

- The "Where this chat works" heading and the "Shared tree" compound noun.
- The branch list as a first-class picker (it becomes a submenu).
- The standalone `/branches` page and its nav entry.
- Machine-id branch names, which take the toolbar's whole width and mean nothing.
- The `HEX_ID_MIN` / `HEX_ID_KEEP` truncation rules in `chatTree.ts` and
  `lib/branchesView.ts`, which exist only to make unreadable names shorter, and which
  are unnecessary once names are readable.

---

## 9. Build sequence

Each of these is a filed ticket. B-numbers refer to the leases doc §15 plan.

1. **Name worktrees after work, not after ids.** `worktrees.create_branch` callers pass
   a title; `_kebab_branch_slug` derives the slug; the record keeps the display name.
   Backfill is not required (the eleven on disk are mostly reapable), but the Branches
   view must show the name when one exists.
2. **First write creates the worktree, silently.** Wire `_maybe_write_blocked` to
   `session_defaults.first_write_offer` → create → pin → lease → continue the turn,
   instead of returning an offer. This is what turns B1's flag on safely.
3. **An operator message preempts.** Attended turn start takes the lease through
   snapshot-and-quiesce; unattended queues.
4. **The unattended half of the lease. ALREADY DONE**, 2026-08-27, `cd8e7b09`:
   `services/run_worktree.py` is `chat_worktree`'s non-interactive twin and
   `scripts/tree_lease.sh` is sourced by both `project_ralph.sh` and `ralph.sh`. A
   claim on 2026-08-28 that unattended runs still take no lease was a stale reading;
   both halves of the lease now exist.
5. **The pill and menu, respecified** (§3, §5).
6. **Chats page groups by worktree; `/branches` folds in and leaves the nav** (§7).

**The flag is the last step of 2, not a step of its own.** `WORKSHOP_TREE_LEASE_MOUNT`
is now armable (both lease halves exist), but arming it before 2 lands would make every
unpinned chat read-only, including the operator's own, since §14.3 of the branches doc
deliberately does not carve out the self-repo. So 2 is developed against the flag in a
test tree and arms it in the instance config as its final action.

---

## 10. Open, and genuinely his call

- **Does a worktree outlive its chats?** A worktree with all its chats archived is either
  garbage to reap or a place to come back to. Reaping is safe (snapshots first) but the
  chats page grouping reads differently depending on the answer.
- **Does the operator want the preempt in §6 to be silent, or to say so and continue?**
  This proposal says continue and state it. The other reading of his words is a
  confirmation, which he has objected to elsewhere as single-user cruft.


---

# Correction, 2026-08-29: the chats page does not group by worktree

Section 7 said the chats list should group by worktree, one row per tree with its
chats under it. That shipped (`cab63e56`) and it was wrong. What it produced was a
column of worktree headings above the operator's conversations, most of them named
`t-<32 hex>` because they predate the naming fix, none of them linking anywhere he
needed to go, pushing the chats themselves down the page.

> "OBVIOUSLY just jamming a long list of worktrees that have no discernable name,
> no link to them, no point or use, in top of the chat page, makes no sense."

**The rule instead, which is what every chat application does: a chats page is a
list of chats, newest first.** A worktree is a property of a chat, so it renders as
a small label on that chat's row, beside the scope and agent tags that are already
there, linking to that chat's worktree view. It is not a heading, not a grouping,
and not a section.

The error underneath it is worth naming, because it is the same one as the Branches
page: **a thing the system needs to track is not automatically a thing that
organises the user's screen.** Worktrees are real and they matter; that does not
make them the axis a list of conversations should be sorted by. The operator said
as much when the Branches page was retired, and section 7 reintroduced it one level
down.

What stays from section 7: retiring `/branches` and its nav entry was right and is
not revisited.
