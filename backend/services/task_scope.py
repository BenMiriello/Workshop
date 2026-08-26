"""Task-scope resolution + the `task-patch` chat protocol (orchestration O2).

A chat bound to a task scope (project:/agent:) can maintain its task list
mid-conversation. Maker chats use the shell CLI (scripts/project_tasks.py) via
Bash; chats with no filesystem emit a fenced ``task-patch`` block,
applied server-side in the turn finalize path exactly like the budget plan-patch:
parsed, applied against THIS scope's store, stripped from the stored reply, with
a human-readable outcome appended to meta.notes (which drives the panel refetch).

Scope is the thread's context binding; a provider only writes its own scope
(correctness, not a permission wall in this single-operator system).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from services import tasks

# ```task-patch\n{...}\n``` — same fence style as the budget plan-patch.
_PATCH_BLOCK = re.compile(r"```task-patch\s*\n(.*?)\n```", re.DOTALL)

# ```learning-note\n{...}\n``` the chat-side durable-preference capture (Phase
# 4). Applied in the SAME finalize path as the task-patch block: parsed, recorded
# into the learnings store at the named (or this chat's) scope, and stripped from
# the stored reply. Deterministic capture in ADDITION to compaction-time
# distillation, by design, so an explicit operator correction lands immediately.
_LEARNING_BLOCK = re.compile(r"```learning-note\s*\n(.*?)\n```", re.DOTALL)

# ```note\n{...}\n``` the chat-side durable-note capture (notes-system plan Phase
# N3). Parallel to learning-note but lands a freeform SCOPED note in the knowledge
# store (services/knowledge.py), which is the Notes substrate. Parsed, written via
# knowledge.put_note at the block's scope (default: this chat's knowledge scope),
# and stripped from the stored reply. This makes "leave a note" a deterministic
# act instead of a hope that compaction catches it.
_NOTE_BLOCK = re.compile(r"```note\s*\n(.*?)\n```", re.DOTALL)

# The shape a registry agent slug may have, matching agents_registry.SLUG_RE.
# Spelled here rather than imported to keep this module free of that import at
# load time; `fullmatch` (not `match`) because `$` in Python also matches before
# a trailing newline, and "the check passes on `foo\n`" is exactly the kind of
# almost-right this module has already been bitten by.
_AGENT_SLUG_RE = re.compile(r"[a-z][a-z0-9-]{0,47}")

# The platform repo — the Workshop's own source tree. Overridable for tests /
# alternate checkouts; the default is this checkout's repo root
# (backend/services -> parents[2]).
#
# Since ADR 0014 this is what the `development` scope means, and ONLY what
# `development` means: it is the one name that resolves to the platform repo,
# and it resolves only when developer mode is on.
WORKSHOP_ROOT = Path(os.environ.get(
    "WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))

# The `workshop` scope's task store — the root inbox, where work is filed,
# triaged and routed to the scope that owns it.
#
# It used to BE the platform repo (the top-level `.tasks.json`), which is the
# accident ADR 0014 unwinds: one name meant both "the root organising layer" and
# "permission to run an agent against the Workshop's own source". Those are two
# different things and now have two different directories. `workshop` is a store
# with no working tree — nothing sweeps it, because there is no code in it — and
# `development` is the platform repo. The root `.tasks.json` did not move; it was
# always the development queue, and is now named that.
WORKSHOP_SCOPE_DIR = Path(os.environ.get(
    "WORKSHOP_SCOPE_DIR", str(WORKSHOP_ROOT / "shared" / "queue")))

# The ONE key that resolves to the platform repo (ADR 0014 §4). Named rather
# than spelled inline, because the point of the constant is that there is
# exactly one of it.
DEVELOPMENT_SCOPE = "development"


def _raw_root_for_context(context_key: str | None) -> Path | None:
    """Where a scope's task store WOULD be, before the developer-mode gate.

    Split out from `root_for_context` so the gate is applied in exactly one
    place and so a refusal can be explained (`dev_mode_refusal`) rather than
    reported as an unknown scope."""
    if not context_key:
        return None
    if context_key == "development":
        return WORKSHOP_ROOT
    if context_key in ("workshop", "general"):
        # The general chat is the orchestrator: it owns the workshop root queue,
        # so a task-patch / task-proposal default resolves to the same store the
        # `workshop` binding uses. This is what turns "a chat that talks about
        # work" into "a chat that owns a queue".
        #
        # This is NOT the `general` -> platform-repo aliasing that ADR 0014
        # removed (§2 door 2). That door existed because `workshop` meant
        # WORKSHOP_ROOT, so a general chat could dispatch a sweep against the
        # Workshop's own source. `workshop` no longer resolves there, so this
        # mapping now reaches an inbox with no working tree, which is the whole
        # point of the split.
        return _workshop_scope_dir()
    if context_key.startswith("project:"):
        from services import build_agents
        return build_agents._project_dir(context_key.split(":", 1)[1])
    if context_key.startswith("agent:"):
        # ONE AGENT SPELLING. `build:<slug>` used to be a second key naming the
        # same agent, and it is deleted rather than folded: which store an
        # agent's tickets live in is decided by what the agent IS (its role and
        # its project link), never by which of two names the caller reached it
        # through.
        slug = context_key.split(":", 1)[1]
        from services import agents_registry
        rec = agents_registry.get(slug)
        if rec and rec.get("role") == "maker":
            return _maker_root(rec)
        return _agent_scope_dir(slug)  # no tree of its own: its own scope
    if context_key.startswith("app:"):
        # STAGE A/B (app-is-the-entity-2026-08-20.md sec 4.2, 5.1). An app
        # owns a ticket queue exactly when it owns a working tree: resolved
        # through `services.app_links.linked_project_slug`, the one place
        # that computes the app<->project merge, off the registries, never a
        # hardcoded roster. An installed-only app with no project (finance,
        # health, wearables at design time) returns None here, same as
        # before this case existed -- it genuinely has no task store, and
        # `chat_work._QUEUELESS_PREFIXES` no longer blankets every `app:`
        # scope for exactly this reason: the exclusion belongs on "no linked
        # project", not on "is an app".
        from services import app_links, build_agents
        project_slug = app_links.linked_project_slug(context_key.split(":", 1)[1])
        return build_agents._project_dir(project_slug) if project_slug else None
    return None


def root_for_context(context_key: str | None) -> Path | None:
    """The task-store directory for a chat's context binding, or None if the
    binding has no task scope (app chats) or the binding is refused.

    CHOKEPOINT 1 of the developer-mode gate (ADR 0014). Every scope kind funnels
    through here, so this is where "does this scope resolve to the platform
    repo?" gets asked once for the ticket launcher, the chat providers and the
    jobs router alike. A scope whose root IS the platform repo resolves to None
    unless developer mode is on — whatever it is called, so renaming a registry
    row or reviving a retired build agent does not reopen the door.

    A product capability boundary and a safety interlock, not a security
    boundary: someone with a shell on this box edits the source directly. See
    ADR 0014.

    Callers that face the operator should pair this with `dev_mode_refusal`, so
    a refusal reads as a refusal instead of as an unknown scope."""
    # CHOKEPOINT 0, ahead of the dev-mode gate: whose scope is this? Every HTTP
    # route that reaches a project or an agent is operator-only, so the REST
    # surface never needed to ask — but a chat `context_key` comes from any
    # authenticated caller and lands here, and this function's answer becomes a
    # task store, a ticket-run `--dir`, and an @-mention cwd. See
    # services/scope_owner.py.
    from services import scope_owner
    if not scope_owner.may_reach(context_key):
        return None
    root = _raw_root_for_context(context_key)
    if root is None:
        return None
    from wsplatform import dev_mode
    if dev_mode.is_self_dir(root):
        # ONE NAME, even with the flag on. §4: exactly one key resolves to the
        # platform repo, and it is `development`. The registry row is named
        # `development` too, so `project:development` also resolved here and the
        # repo had two working addresses again, which is the alias
        # multiplication this design exists to end. Only the OFF case was
        # tested, so it survived the audit that added the gate.
        if context_key != DEVELOPMENT_SCOPE:
            return None
        if not dev_mode.dev_mode_on():
            return None
    return root


def dev_mode_refusal(context_key: str | None) -> str | None:
    """Why `root_for_context` returned None, when the reason was the gate and
    not an unknown scope. None means "not gated" — the caller should report the
    scope as unknown, which is then true."""
    root = _raw_root_for_context(context_key)
    if root is None:
        return None
    from wsplatform import dev_mode
    if dev_mode.is_self_dir(root) and not dev_mode.dev_mode_on():
        return dev_mode.self_dir_refusal(root, f"the {context_key} scope")
    return None


def dev_scopes() -> list[str]:
    """The scope keys that exist only in developer mode. Empty in a normal
    deployment, which is what makes `development` absent from the scope list
    rather than present and refusing.

    Routes the shape question ("does `development` exist as a scope right
    now?") through `services.scope_tree`, the single owner of that question
    (memory-and-documentation-model-2026-08-14.md sec 7.1), rather than
    re-reading `wsplatform.dev_mode.dev_mode_on()` here a second time. `scope_tree.parse`
    already derives conditionally from that same flag."""
    from services import scope_tree
    try:
        scope_tree.parse(scope_tree.DEVELOPMENT_SCOPE)
        return [scope_tree.DEVELOPMENT_SCOPE]
    except ValueError:
        return []


def _workshop_scope_dir() -> Path:
    """The workshop inbox store, created on demand (mirrors _agent_scope_dir)."""
    WORKSHOP_SCOPE_DIR.mkdir(parents=True, exist_ok=True)
    return WORKSHOP_SCOPE_DIR


def key_for_root(root: Path | str | None) -> str | None:
    """The inverse of root_for_context: which scope does this task store belong
    to? Returns None for a path that is not a recognised scope root.

    Structural, not registry-driven, so it stays cheap enough to call on every
    ticket write (which is what bumps the scope's change counter). It mirrors
    root_for_context branch for branch; the two must be read together.

    A maker shares its project's tree, so a project root maps back to
    `project:<slug>` and never to the agent that happened to write there — the
    change belongs to the place, not to whoever touched it.

    The platform repo maps back to `development`, never to `workshop` (ADR
    0014). That is the inverse of the one mapping this redesign changed, and
    getting it wrong would re-key every ticket in the root queue on the next
    write."""
    if root is None:
        return None
    try:
        p = Path(root).resolve()
    except (OSError, ValueError):
        return None
    try:
        if p == WORKSHOP_ROOT.resolve():
            return "development"
    except OSError:
        pass
    try:
        if p == WORKSHOP_SCOPE_DIR.resolve():
            return "workshop"
    except OSError:
        pass
    parent = p.parent.name
    if parent == "projects":
        return f"project:{p.name}"
    if parent == "agent-scopes":
        return f"agent:{p.name}"
    return None


# The scopes whose tickets live in Postgres and NOWHERE ELSE (the cutover of
# docs/platform-redesign/tickets-one-store-2026-08-14.md, completed by
# tickets-postgres-cutover-2026-08-16.md). Every other scope — project trees,
# agent scopes — is still file-backed, and legitimately so: the split is by
# root, not by installation.
#
# ONE DEFINITION, and it lives here rather than in `db/` on purpose.
# `db.ticket_sync.scope_for_root` used to own it, which meant the question "do
# this root's tickets belong in Postgres?" could only be asked by an interpreter
# that could import sqlalchemy. Under one that could not, the answer came back
# False, the retired file store served the read and took the write, and on
# 2026-08-17 a `TODO.md` re-import minted four duplicate ticket ids behind
# Postgres's back. The answer is a fact about a path; it must be answerable with
# pathlib alone.
RELATIONAL_SCOPES = ("development", "workshop")


def relational_scope_for_root(root: Path | str | None) -> str | None:
    """Which relational scope this working tree is, or None if it is not one.

    The whole of `is_relational_scope`, and of `ticket_sync.scope_for_root`,
    which both delegate here. Pure path arithmetic: no database, no driver, no
    import that can fail and be mistaken for "file-backed".
    """
    key = key_for_root(root)
    return key if key in RELATIONAL_SCOPES else None


def root_for_job(job: dict | None) -> Path | None:
    """The task-store directory a Job worked, from its binding. A project sweep
    carries project_slug; an agent sweep carries agent_id ('agent:<slug>'). Lets
    the Jobs API act on an awaiting job's bound tasks without the caller knowing
    project-vs-agent routing."""
    if not job:
        return None
    slug = job.get("project_slug")
    if slug:
        return root_for_context(f"project:{slug}")
    agent_id = job.get("agent_id") or ""
    if agent_id.startswith("agent:"):
        return root_for_context(agent_id)
    return None


def _maker_root(rec: dict) -> Path | None:
    """A maker's task store: its linked project dir, else the working directory
    its execution state records. None when it has neither, which `root_for_context`
    reports as "no task scope" -- a maker with no tree cannot be swept.

    Takes the REGISTRY record, not a slug, so the project link is read off the
    declaration that owns it. It used to be read out of `build_agents.json`,
    which is per-instance execution state: an agent definition can outlive it,
    and then a project-linked maker resolved to nothing."""
    from services import build_agents
    project_slug = rec.get("project_slug")
    if project_slug:
        pd = build_agents._project_dir(project_slug)
        if pd is not None:
            return pd
    try:
        a = build_agents.get_agent(rec.get("slug") or "")
    except KeyError:
        return None
    cwd = a.get("cwd")
    return Path(cwd) if cwd else None


def _agent_scope_dir(slug: str) -> Path | None:
    """A registry agent's own durable task scope (.runtime/agent-scopes/<slug>).

    Contained, and it was not. This join took the slug straight off an HTTP
    `context_key`/`scope` — `POST /chat/threads/{id}/spawn-task` and
    `POST /tasks` both accept one from any authenticated caller — and then
    `mkdir(parents=True)`'d whatever came out. `agent:../../../../tmp/x`
    therefore created a task store at an arbitrary host path, wrote
    `.tasks.json` and `TODO.md` into it, and `launch_task_run` would then start
    `project_ralph.sh --dir <that path>`: a detached, autonomous Claude Code
    sweep rooted outside the workshop tree entirely.

    The one containment downstream, `wsplatform.dev_mode.is_self_dir`, is an
    exact-equality test against the platform repo — so `agent:../..` was caught
    and every deeper escape was not. Same defect and the same shape as the
    `project:` traversal fixed alongside it; this branch is the one that also
    ran something.

    Returns None for an escaping or malformed slug, which `root_for_context`
    already treats as "no task scope" — the caller reports an unknown scope
    rather than 500ing. See
    docs/security-audit-2026-08-11-scope-separation.md section 2.
    """
    from services import ownership
    if not _AGENT_SLUG_RE.fullmatch(slug or ""):
        return None
    base = (ownership._RUNTIME / "agent-scopes").resolve()
    d = (base / slug).resolve()
    if not d.is_relative_to(base):
        return None
    d.mkdir(parents=True, exist_ok=True)
    return d


#: The mediated-write blocks, by the name the commit path books them under.
#: Each `apply_*` helper uses `.search`, so at most ONE block of each kind is
#: ever applied from a reply -- which is what makes a per-kind "already done"
#: marker complete rather than approximate.
_BLOCK_KINDS = {
    "tasks": _PATCH_BLOCK,
    "learnings": _LEARNING_BLOCK,
    "notes": _NOTE_BLOCK,
}


def strip_block_kind(reply: str, kind: str) -> str:
    """Remove one kind of mediated-write block without applying it.

    For a replay that has already applied THIS block but not necessarily the
    others: the block still has to come off the visible reply, or it renders as
    raw JSON that reads like work which just happened."""
    pat = _BLOCK_KINDS.get(kind)
    return reply if pat is None else pat.sub("", reply).strip()


def strip_all_blocks(reply: str) -> str:
    """Remove every mediated-write block WITHOUT applying any of them.

    For a REPLAY. A turn whose reply is re-derived from its journal after a
    restart carries the same task-patch, learning-note and note blocks it
    carried the first time, and applying them again files the ticket twice and
    writes the note twice. The blocks must still come off the visible reply,
    though, or they render as raw JSON that reads like work that just happened.
    Skipping and cleaning are the same act (the argument in strip_task_patch,
    applied to all three)."""
    for pat in (_PATCH_BLOCK, _LEARNING_BLOCK, _NOTE_BLOCK):
        reply = pat.sub("", reply)
    return reply.strip()


def strip_task_patch(reply: str) -> str:
    """Remove a task-patch block WITHOUT applying it.

    For a speaker that is not permitted to file: the block must not be applied,
    and it must not be left in the visible reply either, where it would render
    as raw JSON and read as a ticket that was created. Refusing and cleaning are
    the same act."""
    return _PATCH_BLOCK.sub("", reply).strip()


def apply_task_patch(context_key: str | None, reply: str,
                     thread_id: str | None = None,
                     chat_mode: str | None = None) -> tuple[str, list[str]]:
    """Find a ``task-patch`` block, apply add/update/note against this chat's
    scope store, strip the block, and return (cleaned_reply, notes). No-op (the
    reply unchanged, empty notes) when there is no block or no task scope.

    `thread_id` is which CONVERSATION filed the ticket, recorded as the source
    ref. It used to write the context_key there — a scope where an identity
    belongs, so every ticket filed this way claimed to come from "general"
    rather than from a chat you could open. The scope is already on the ticket
    by virtue of which store it lives in; the ref is for getting back to the
    conversation that wanted it."""
    m = _PATCH_BLOCK.search(reply)
    if not m:
        return reply, []
    root = root_for_context(context_key)
    cleaned = _PATCH_BLOCK.sub("", reply).strip()
    # Direct mode means work stays in the conversation. The prompt says so, but
    # a prompt is a request and this is the enforcement: a chat in Direct mode
    # cannot file, whatever the model emits. Strip the block and say why, rather
    # than filing a ticket the operator did not ask this chat to create.
    if chat_mode == "direct":
        return cleaned, ["(Direct mode: the task edit was not applied)"]
    if root is None:
        return cleaned, ["(no task scope for this chat, so the task edit was skipped)"]
    try:
        patch = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return cleaned, ["(could not parse a task edit. Tasks are unchanged)"]
    if not isinstance(patch, dict):
        return cleaned, []

    # A patch may name a scope, but it may only write ITS OWN binding.
    named = patch.get("scope")
    if named and named != context_key:
        return cleaned, [f"(task edit ignored: a {context_key} chat cannot write {named})"]

    from services import task_dedup

    added = updated = noted = 0
    merged = related = 0
    for op in patch.get("add", []) or []:
        if not isinstance(op, dict) or not (op.get("text") or "").strip():
            continue
        try:
            # Cross-chat dedup (Stage 7 / 2e): fold a duplicate into the existing
            # scope task, link a near-match as related, or create fresh. This is
            # the path that previously passed no provenance at all.
            res = task_dedup.dedup_add(
                root, text=op["text"], priority=int(op.get("priority") or 0),
                depends_on=[str(d) for d in (op.get("depends_on") or [])],
                acceptance=op.get("acceptance") if isinstance(op.get("acceptance"), dict) else None,
                touches=[str(t) for t in (op.get("touches") or [])],
                source={"kind": "chat", "ref": thread_id},
                # A task-patch block is written by the assistant, so the spec is
                # its work even though the request was the operator's. `source`
                # keeps the request, `scoped_by` records the author.
                scoped_by="agent:chat",
                # A ticket the assistant wrote for itself is a SUGGESTION until
                # the operator agrees to it. `draft` is never picked by
                # `next_ready`, so filing one can propose work but can never
                # start it. Approving is the operator's act (POST .../approve).
                status="draft",
            )
            decision = res.get("decision")
            if decision in ("exact", "merge"):
                merged += 1
            elif decision == "related":
                related += 1
                added += 1
            else:
                added += 1
        except ValueError:
            pass
    for op in patch.get("update", []) or []:
        if not isinstance(op, dict) or not op.get("id"):
            continue
        try:
            if tasks.update(
                root, str(op["id"]), status=op.get("status"), text=op.get("text"),
                blocked_reason=op.get("blocked_reason"),
                priority=op.get("priority"),
            ):
                updated += 1
        except ValueError:
            pass
    for op in patch.get("note", []) or []:
        if not isinstance(op, dict) or not op.get("id") or not (op.get("text") or "").strip():
            continue
        if tasks.update(root, str(op["id"]), evidence={"kind": "note", "ref": op["text"]}):
            noted += 1

    bits = []
    if added:
        bits.append(f"added {added} task{'s' if added != 1 else ''}")
    if related:
        bits.append(f"{related} linked as related to a near-match")
    if merged:
        bits.append(f"merged {merged} duplicate{'s' if merged != 1 else ''} into existing task(s)")
    if updated:
        bits.append(f"updated {updated}")
    if noted:
        bits.append(f"noted {noted}")
    notes = ["Tasks: " + ", ".join(bits) + "."] if bits else []
    return cleaned, notes


def apply_learning_note(context_key: str | None, reply: str) -> tuple[str, list[str]]:
    """Find a ``learning-note`` block, record its note as durable operator feedback
    in the learnings store, strip the block, and return (cleaned_reply, notes).
    No-op (the reply unchanged, empty notes) when there is no block. The note is
    recorded at the block's explicit `scope` or, absent one, this chat's own
    knowledge scope (general -> workshop). Mirrors apply_task_patch: server-side
    application, fence stripped, a human-readable outcome for meta.notes."""
    m = _LEARNING_BLOCK.search(reply)
    if not m:
        return reply, []
    cleaned = _LEARNING_BLOCK.sub("", reply).strip()
    try:
        block = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return cleaned, ["(could not parse a learning note. Nothing was captured)"]
    if not isinstance(block, dict):
        return cleaned, []
    note = (block.get("note") or "").strip()
    if not note:
        return cleaned, []

    # Default the scope to this chat's own knowledge scope (which the learnings
    # store shares its scope keys with); an explicit block scope wins.
    #
    # The override may only name a scope this caller could reach anyway. It was
    # briefly operator-only, which was right while `workshop` was one shared
    # directory; now that the learnings store is per-owner, aiming a learning at
    # your OWN `workshop` is ordinary use and refusing it would make a second
    # account unable to record a cross-project preference at all. What stays
    # refused is naming someone else's project or agent scope — see
    # services/scope_owner.py. Not a hard refusal either way: the learning
    # lands, at the scope this chat actually occupies.
    scope = (block.get("scope") or "").strip()
    if scope:
        from services import scope_owner
        if not scope_owner.may_reach(scope):
            scope = ""
    if not scope:
        from services import knowledge
        try:
            scope = knowledge.scope_for_context(context_key or "workshop")
        except Exception:
            scope = "workshop"

    from services import learnings
    try:
        res = learnings.record_feedback(note, source="chat", scope=scope)
    except ValueError as e:
        return cleaned, [f"(learning note skipped: {e})"]
    # The write path reconciles and can decline (ADR 0022): a candidate that is
    # not instructional guidance is refused at the door, and one that restates
    # an existing rule supersedes it or is a noop. Reporting "captured" for all
    # of those was the exact failure the reconciliation exists to stop, just
    # moved one layer out: the store stays clean and the operator is told
    # something untrue about it.
    res = res if isinstance(res, dict) else {}
    resolution = res.get("resolution")
    if resolution == "refused":
        reason = res.get("reason") or "not a durable guardrail"
        return cleaned, [f"(learning note not captured: {reason})"]
    if resolution in ("supersede", "update"):
        return cleaned, [f"Updated an existing preference in learnings ({scope})."]
    if resolution == "noop":
        return cleaned, [f"Preference already recorded in learnings ({scope})."]
    return cleaned, [f"Captured a preference in learnings ({scope})."]


def _note_id_from_title(title: str) -> str:
    """A knowledge-store-safe note id derived from a title: lowercase [a-z0-9]
    segments joined by '-', with a base36 time suffix so a fresh note never
    collides with an existing file. Mirrors the frontend freshNoteId helper."""
    import time
    # Strip AFTER truncating, not before. `[:48]` can land on a word boundary and
    # leave a trailing hyphen, and the id then contains `--`, which the store's
    # kebab rule rejects: the note was silently dropped and the operator was told
    # `'workshop' is not a valid scope`. Any title long enough to be cut at a
    # space hit it, which is most real note titles.
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:48].strip("-") or "note"
    return f"{base}-{int(time.time() * 1000):x}"


async def apply_note_block(context_key: str | None, reply: str,
                           author: str = "", thread_id: str | None = None,
                           ) -> tuple[str, list[str], list[dict]]:
    """Find a ``note`` block, write its {scope, title, body} as a durable note in
    the knowledge store (the Notes substrate), strip the block, and return
    (cleaned_reply, labels, refs). No-op (reply unchanged, empty lists) when
    there is no block. The note lands at the block's explicit `scope` or, absent
    one, this chat's own knowledge scope (general -> workshop). Mirrors
    apply_learning_note: server-side application, fence stripped, a
    human-readable outcome for meta.notes. An invalid scope is reported, not
    raised.

    `thread_id` is WHICH CONVERSATION filed the note, mirroring the `thread_id`
    parameter `apply_task_patch` already takes for the same reason (design doc
    sec 5.2: "some retention of the chat that it came from ... so that at least
    that is reconstructable later"). Passed straight to `knowledge.put_note` as
    `chat_id`; never guessed or fabricated when absent.

    `refs` carries WHAT WAS WRITTEN, not just that something was. This function
    already knows the scope and the note id it just created and used to throw
    both away into a sentence, so the chat could say "Saved a note to workshop"
    and offer no way to go read it. A ref is {scope, id, title}; the caller turns
    it into a link. Empty when nothing was written, and empty on failure, so a
    refusal never renders as a link to a note that does not exist."""
    m = _NOTE_BLOCK.search(reply)
    if not m:
        return reply, [], []
    cleaned = _NOTE_BLOCK.sub("", reply).strip()
    try:
        block = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return cleaned, ["(could not parse a note. Nothing was captured)"], []
    if not isinstance(block, dict):
        return cleaned, [], []
    title = (block.get("title") or "").strip()
    body = (block.get("body") or "").strip()
    if not title and not body:
        return cleaned, [], []

    from services import knowledge, scope_owner
    scope = (block.get("scope") or "").strip()
    # The override (NOTE_PROTOCOL documents it: a build agent in project:foo
    # deciding a finding is really cross-project knowledge belonging in
    # `workshop`) may only name a scope this caller could reach anyway. Writing
    # to your OWN `workshop` is the documented use and is per-owner now; what
    # stays refused is naming someone else's project or agent scope. Not a
    # refusal: the note still lands, just where this chat actually is. See
    # services/scope_owner.py.
    if scope and not scope_owner.may_reach(scope):
        scope = ""
    if not scope:
        try:
            scope = knowledge.scope_for_context(context_key or "workshop")
        except Exception:
            scope = "workshop"

    note_id = _note_id_from_title(title or "note")
    # An optional `type` lets a durable DECISION be filed as a decision-typed
    # note, which is its one home now (the operator moved decisions into memory,
    # 2026-08-25, rather than a second copy in docs/decisions). Unknown or absent
    # type falls back to put_note's own default; a decision ranks above a fact in
    # recall (`knowledge._TYPE_RANK`).
    ntype = (block.get("type") or "").strip().lower()
    if ntype not in knowledge._NOTE_TYPES:
        ntype = ""
    try:
        # `author` is an origin string (services/origin): which speaker wrote
        # this. Without it, a note the assistant wrote, one an @mentioned agent
        # wrote, and one distilled from a compaction are identical on recall.
        await knowledge.put_note(scope, note_id, title or note_id, body, author=author,
                                 note_type=ntype, chat_id=thread_id or "")
    except ValueError as e:
        # Report what actually failed. put_note validates the scope AND the note
        # id, so blaming the scope for either sent the operator looking at a
        # scope that was fine while a malformed id went unmentioned.
        return cleaned, [f"(note skipped: {e})"], []
    # The label names the NOTE, not the act of saving one. "Saved a note to
    # workshop" describes the machine's own bookkeeping; the title is the thing
    # the operator might actually want to open.
    label = title or note_id
    return cleaned, [f"Saved a note to {scope}."], [
        {"scope": scope, "id": note_id, "title": label},
    ]
