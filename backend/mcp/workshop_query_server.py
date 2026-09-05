#!/usr/bin/env python3
"""Workshop MCP server (Stage 7 / design 2e).

Two read-only tools for the chat to answer "what is going on / what tasks and
notes exist" from the task + knowledge digest and a semantic search. Neither is
a filesystem Read grant: they expose a bounded, computed summary and ranked
passages, never arbitrary files, so they cannot leak `.runtime` secrets through
the model's egress.

Plus every `command` tool declared in `services/tool_registry`, listed beside
these and dispatched to `services/command_tools` -- which is how a tool a user
declared becomes callable at all (tools-are-declared-2026-08-23 §5 stage 4).
They ride on THIS server rather than a second one so a declared tool's CLI name
is an ordinary `mcp__workshop__<id>`, granted by the argv `llm_router.tool_flags`
already builds and by nothing else.

Plus the WRITE tools, offered ONLY to a real chat (when WORKSHOP_QUERY_THREAD is
bound), each of which acts ON that chat rather than returning file contents:
`spawn_background` launches a background agent for it (services/background_runs)
whose result reaches the operator through the chat, not this tool's return; and
`wake_me` / `list_wakeups` / `cancel_wakeup` arm and manage a durable alarm on it
(services/wakeups), which starts a later turn there and outlives this process.

Transport: MCP stdio (newline-delimited JSON-RPC 2.0), implemented by hand so it
needs no MCP SDK (none is installed in the backend venv). The claude CLI spawns
this over stdio; it answers initialize / tools/list / tools/call / ping.

Scope comes from WORKSHOP_QUERY_SCOPE (the chat's binding, default "workshop" =
workspace-wide). Runs under the backend venv with PYTHONPATH=backend so it can
reuse services.workspace_digest + services.knowledge (single source of truth for
the digest, shared with the ambient context injection).
"""
from __future__ import annotations

import json
import os
import sys

# Module scope, unlike this file's other service imports. Those are local to
# keep a per-turn subprocess's startup cheap; these two are on the `tools/list`
# path that every turn takes anyway, so deferring them buys nothing and costs
# the backend's import graph a pair of function-local edges.
from services import chat_log, operator_notify

SERVER_NAME = "workshop"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2025-06-18"

TOOL_NAME = "query"
TOOL_DESC = (
    "Read-only summary of ongoing work in the Workshop: open tasks, running and "
    "recent jobs, and knowledge note titles for the current scope. Use it to "
    "answer 'what is going on', 'what tasks exist', or 'what notes do we have'. "
    "It returns a computed digest only; it cannot read files or secrets."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# The `topic` parameter is gone rather than implemented. It was declared, was
# accepted, and was then discarded: `_build_digest()` takes no arguments and
# never read it. A tool that advertises a filter it ignores is worse than one
# that advertises nothing, because the model spends a turn phrasing a query and
# reasons over an answer it believes was narrowed. Searching is now a real tool,
# below, and `query` is honestly what it always was: a fixed digest.

SEARCH_TOOL_NAME = "search"
SEARCH_TOOL_DESC = (
    "Search the workshop's own writing by meaning: knowledge notes, everything "
    "under docs/, tickets, and the full text of past chats. Returns ranked "
    "passages with the text itself and where it came from, so you can read what "
    "you find. Use it for 'have we decided X before', 'why is Y built this way', "
    "'what did we say about Z', 'is there already a ticket for this', and before "
    "designing anything that may already exist. Returns nothing when nothing is "
    "genuinely relevant, which means an empty result is an answer: there is no "
    "prior art in the workshop for that. Ranking is by meaning AND by what each "
    "source declares itself to be: a document this repository's own agent-rules "
    "file names as the source of record outranks a conversation that merely "
    "mentions it, and a document that declares itself superseded is demoted and "
    "arrives labelled rather than hidden. Read the labels on each hit. "
    "Each hit names its source as a ref, and open_ref reads the whole of "
    "that source, so a passage is a way in rather than all you get."
)
SEARCH_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What you want to know, phrased as a question or a topic.",
        },
        "kinds": {
            "type": "array",
            "items": {"type": "string", "enum": ["note", "doc", "chat", "ticket", "vault"]},
            "description": (
                "Restrict to some sources. Default: note, doc, chat and ticket. "
                "'vault' is an external personal-notes vault this installation "
                "has connected, and is only searched when you ask for it by name."
            ),
        },
        "scope": {
            "type": "string",
            "description": (
                "Narrow to one scope and everything below it in the tree "
                "(e.g. 'project:<slug>', 'app:<id>'). Default: everything "
                "this chat's own scope can reach, which already excludes "
                "sibling projects and, unless this chat IS a development "
                "chat, the development scope."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "How many passages to return. Default 6, max 20.",
        },
        "since": {
            "type": "string",
            "description": (
                "Only consider material from this point on. A relative window "
                "like '30d', '2w', '3m', or a date like '2026-08-01'. Use it "
                "for 'lately' or 'recently' questions; leave it off to search "
                "the whole record."
            ),
        },
        "repeats": {
            "type": "boolean",
            "description": (
                "Count instead of rank. Returns the things the operator said "
                "MORE THAN ONCE in past chats, grouped, each with how many "
                "separate occasions it was said on, when it was first and last "
                "said, and every message it was said in. Ordinary search "
                "cannot answer this: six restatements of one thing are six "
                "passages competing for one slot. Use it for 'what has come up "
                "repeatedly', 'what keeps getting raised', 'what did the "
                "operator say again'. `query` is optional here and narrows to "
                "a topic; with no query you get the whole list."
            ),
        },
    },
    "required": [],
    "additionalProperties": False,
}

# The one WRITE tool on this otherwise read-only server: spawn a background agent
# for THIS chat. It does not return work to the model; it launches a detached
# run that reports back into the chat later and lets the chat continue on its
# own (services/background_runs). The thread it belongs to comes from
# WORKSHOP_QUERY_THREAD, pinned at turn-start exactly like the owner and scope.
SPAWN_TOOL_NAME = "spawn_background"
SPAWN_TOOL_DESC = (
    "Start a BACKGROUND AGENT for a piece of work you want done out-of-band while "
    "you keep talking to the operator: a slow investigation, reading a large "
    "surface, a long search, drafting something sizeable. It runs as its own "
    "process, so the operator is not left waiting, and when it finishes its "
    "result is posted back into THIS chat and the chat continues on its own. Use "
    "this instead of a foreground subagent ONLY when the work is genuinely worth "
    "running in the background; for something quick, just do it inline. Give a "
    "clear, self-contained prompt: the agent does not see this conversation. "
    "Returns immediately with the agent's id; it does NOT return the result."
)
SPAWN_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": (
                "The full, self-contained instruction for the background agent. "
                "It cannot see this chat, so state everything it needs."
            ),
        },
        "label": {
            "type": "string",
            "description": "A short name for the agent, shown to the operator. Optional.",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


# --- Workshop toolkit (Phase L1/L2): reach things by tool, not by grep. -------

WHERE_IS_TOOL_NAME = "where_is"
WHERE_IS_TOOL_DESC = (
    "Return the orientation map for this chat: what its scope is and which tool "
    "reaches each kind of thing (uploads, notes, docs, tickets, runs). Call it "
    "when you are unsure where something lives, instead of searching the "
    "filesystem for it. Takes no arguments."
)
WHERE_IS_TOOL_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

LIST_ATTACHMENTS_TOOL_NAME = "list_attachments"
LIST_ATTACHMENTS_TOOL_DESC = (
    "List the files uploaded to THIS chat: their id, name, kind and size. Use it "
    "to see what the operator attached before reading one with get_file. Text "
    "attachments are already injected into the conversation; use this and "
    "get_file for binaries (zips, images, PDFs) or to re-read one on demand. "
    "Takes no arguments."
)
LIST_ATTACHMENTS_TOOL_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

GET_FILE_TOOL_NAME = "get_file"
GET_FILE_TOOL_DESC = (
    "Read one file uploaded to this chat, by its attachment id (from "
    "list_attachments). Returns the file's text when it is textual or has a text "
    "rendering; for a binary (a zip, an image) it returns the file's on-disk path "
    "so a tool-capable chat can open it directly. This is how you reach an upload "
    "without hunting for where it is stored."
)
GET_FILE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The attachment id, as shown by list_attachments."},
    },
    "required": ["id"],
    "additionalProperties": False,
}

_GET_FILE_MAX_CHARS = 100_000


# --- Reading the whole of what search found ----------------------------------
#
# Search returns a RANKED SLICE: passages bounded by the chunker, at most a
# couple per document, chosen for one query. That is the right shape for
# finding something and the wrong shape for reading it, and until this tool
# existed there was no second step: an agent that found the paragraph it needed
# could not get the paragraph after it, so it either answered from the slice or
# went hunting the filesystem for a path, which is the rediscovery the
# orientation map exists to stop.
#
# It reads by REF, not by path, because the ref vocabulary already exists, is
# already what search returns on every hit, and is already what a citation
# renders as. A second addressing scheme would be a second thing to keep true.
#
# IT CANNOT REACH PAST SEARCH. The scope and share decision is
# `semantic_index.reachable_item`, which is `_keep_mask`'s own rule; a document
# a search would not have surfaced is refused here in the same words.
OPEN_REF_TOOL_NAME = "open_ref"
OPEN_REF_TOOL_DESC = (
    "Read the WHOLE of something search found, by its ref. Search returns "
    "ranked passages, at most a couple from any one document, so a hit is a "
    "pointer and not the document. Pass the ref from a hit "
    "(workshop:note/<scope>/<id>, workshop:doc/<path>, workshop:ticket/<id>, "
    "workshop:chat/<thread>) and this returns the body, with its title, its "
    "date and whether it declares itself superseded. Long bodies come back in "
    "parts: every part says which characters it covers, how many characters "
    "there are in total, and which part to ask for next, so nothing is ever cut "
    "off silently. It reaches exactly what a search from this chat reaches and "
    "nothing further."
)
OPEN_REF_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": (
                "The ref to read, as search printed it: "
                "workshop:note/<scope>/<id>, workshop:doc/<path>, "
                "workshop:ticket/<id>, or workshop:chat/<thread>."
            ),
        },
        "part": {
            "type": "integer",
            "description": (
                "Which part of a long body to read, counting from 1. Default 1. "
                "Each part states how many there are."
            ),
        },
    },
    "required": ["ref"],
    "additionalProperties": False,
}


# --- Waking this chat up later ------------------------------------------------
#
# The other three WRITE tools, and like `spawn_background` they are offered only
# to a real chat: a wake-up is an alarm ON a thread, so with no thread bound
# there is nothing for it to wake. The alarm is held by the platform rather than
# by this process, which is the whole point -- see services/wakeups.

WAKE_TOOL_NAME = "wake_me"
WAKE_TOOL_DESC = (
    "Wake yourself up later. Arms a durable alarm on THIS chat: at the time you "
    "ask for, a new turn starts here carrying the reason you gave, and you act "
    "on it then. It is held by the platform, not by this turn, so it survives "
    "the turn ending, the turn being stopped, and a restart in between. Use it "
    "for anything that has to happen at a TIME rather than now: check whether a "
    "deploy settled, chase something that was promised, follow up on a reply "
    "that has not come, report at an hour the operator asked for, re-read a "
    "measurement once enough of it has accumulated. Say when with EITHER "
    "in_minutes OR at, never both. Write the reason to your future self and "
    "make it self-contained: it is what that turn is handed, and it should say "
    "what to check and what to do about each outcome. Returns the wake-up's id."
)
WAKE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": (
                "What the woken turn should do, in enough detail to act on "
                "without re-deriving it. This is the whole of what that turn "
                "is given about why it is running."
            ),
        },
        "in_minutes": {
            "type": "number",
            "description": "Wake this many minutes from now. Use this OR `at`.",
        },
        "at": {
            "type": "string",
            "description": (
                "Wake at a specific time. A 24-hour clock time like '16:30' "
                "means the next time the clock reads that; a full date and time "
                "like '2026-01-31T09:15' means that instant. Read in this "
                "installation's own timezone unless the value states an offset. "
                "Use this OR `in_minutes`."
            ),
        },
    },
    "required": ["reason"],
    "additionalProperties": False,
}

LIST_WAKEUPS_TOOL_NAME = "list_wakeups"
LIST_WAKEUPS_TOOL_DESC = (
    "List the wake-ups currently armed on THIS chat: their id, when each fires, "
    "and the reason it carries. Call it before arming another one to avoid "
    "stacking duplicates, and to get the id you need to cancel one. Takes no "
    "arguments."
)
LIST_WAKEUPS_TOOL_SCHEMA = {"type": "object", "properties": {},
                            "additionalProperties": False}

CANCEL_WAKEUP_TOOL_NAME = "cancel_wakeup"
CANCEL_WAKEUP_TOOL_DESC = (
    "Cancel a wake-up armed on this chat, by its id (from list_wakeups). Use it "
    "when the thing it was going to check has already happened, or the operator "
    "has changed the plan, so a turn does not start for a reason that no longer "
    "holds."
)
CANCEL_WAKEUP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "The wake-up id, as shown by list_wakeups."},
    },
    "required": ["id"],
    "additionalProperties": False,
}


# The fourth chat-bound WRITE tool. Chat-scope for a different reason than the
# alarm: the notification carries a link back to the conversation that sent it,
# so the operator can act on it where it happened rather than hunting for it.
NOTIFY_TOOL_NAME = "notify_operator"
NOTIFY_TOOL_DESC = (
    "Reach the operator when they are not looking at the app. Sends a message "
    "to them directly, wherever they are, and returns whether it was actually "
    "delivered. Use it for exactly two things: something they asked for is now "
    "DONE, or something they asked for is now BLOCKED and cannot go further "
    "without them. Say which of the two it is, say what the result or the "
    "obstacle is, and if it is blocked say what you need from them. This is an "
    "interruption, not a log: it is not for progress updates, not for narrating "
    "what you are about to do, not for acknowledging a request, and not for "
    "anything they will see anyway the moment they open this chat. One per "
    "finished or stuck request, not one per step. The chat is linked "
    "automatically, so do not write out where you are; write what happened."
)
NOTIFY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": (
                "What the operator needs to know, in a sentence or two they can "
                "read on a phone without opening anything. Lead with the result "
                "or the obstacle, not with context they already have."
            ),
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}


def _pin_owner() -> None:
    """Pin the data owner so owner-scoped stores (jobs, knowledge) resolve.

    This runs as a SEPARATE stdio subprocess of the `claude` CLI, spawned fresh
    per turn — it has no access to the backend request's `ownership`
    contextvar, only whatever the parent process put in its environment.
    `WORKSHOP_QUERY_OWNER` (app_registry._platform_mcp_servers) is the real
    caller, set at spawn time from the actual request. Falling back to the
    operator when it's absent is deliberate, not a workaround: a background
    automation pass has no per-request owner to hand down either, and the
    operator's id IS the correct one there (ownership.py's own rule for
    background loops). What changed is that an ordinary chat turn now passes
    its real caller instead of this always defaulting to the operator
    regardless of who was actually asking — see
    the 2026-08-11 scope-separation security audit section 3.
    """
    try:
        from services import auth_store, ownership
        raw = os.environ.get("WORKSHOP_QUERY_OWNER", "").strip()
        oid = int(raw) if raw else auth_store.operator_id()
        if oid is not None:
            ownership.set_current_owner(oid)
    except Exception:
        pass


def _build_digest() -> str:
    scope = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    _pin_owner()
    parts: list[str] = []
    # Task + job digest (workspace-wide for general; own scope otherwise).
    try:
        from services import workspace_digest
        ctx = None if scope in ("workshop", "general", "global") else scope
        body = workspace_digest.digest_for_context(ctx)
        if body:
            parts.append(body)
    except Exception as e:  # noqa: BLE001
        parts.append(f"(work digest unavailable: {e})")
    # Knowledge note titles for the scope (index only, never note bodies here).
    try:
        from services import knowledge
        kscope = "workshop" if scope in ("general", "global") else scope
        idx = knowledge.index_block(kscope)
        if idx:
            parts.append(f"Knowledge notes (scope: {kscope}):\n{idx}")
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "No open tasks, jobs, or notes in scope."


def _caller_scope_key(raw: str) -> str:
    """`WORKSHOP_QUERY_SCOPE` is the chat's `context_key` ("general",
    "workshop", or a project/app/agent key...) -- the same value
    `_build_digest` reads. `reach()` operates on `scope_tree`'s scope_key
    namespace, so this is the same translation `_build_digest` already
    applies inline, pulled out because the search path needs it too."""
    from services import knowledge
    ck = "workshop" if raw in ("general", "global") else raw
    try:
        return knowledge.scope_for_context(ck)
    except Exception:
        return "workshop"


def _reach_scopes(scope_key: str) -> set[str] | None:
    """`reach(scope_key)` as a set, or None when the tree does not recognise
    the key. `reach` is curation, not a security boundary (scope_tree module
    docstring, SEC-A2), so an unrecognised scope degrades to "no filter"
    rather than an error."""
    from services import scope_tree
    try:
        return set(scope_tree.reach(scope_key))
    except ValueError:
        return None


# How a passage's declared status reads to the model. Kept beside the renderer
# rather than in the index, because these are words chosen for a reader; the
# index stores the bare status and the numbers it ranks by.
_STATUS_FLAGS = {
    "superseded": " \u00b7 SUPERSEDED, historical",
    "authoritative": " \u00b7 SOURCE OF RECORD, named by this repository's own agent rules",
    "current": " \u00b7 declares itself current",
}


def _run_search(args: dict) -> str:
    """Ranked passages, rendered for a model to read.

    Each hit leads with its source so the model can cite it or go read the whole
    thing, and carries the passage itself. Returning a title alone was the old
    tool's defect and is the one thing this must not repeat."""
    _pin_owner()
    from services import semantic_index

    query = (args.get("query") or "").strip()
    want_repeats = bool(args.get("repeats"))
    if not query and not want_repeats:
        return "error: a query is required"
    kinds = args.get("kinds") or list(semantic_index.DEFAULT_KINDS)
    limit = max(1, min(int(args.get("limit") or 6), 20))
    since = (args.get("since") or "").strip()

    # The default candidate set is what THIS chat's scope can reach, not
    # everything: a workshop chat searches every project and app but not
    # development, a project chat cannot reach a sibling project (memory-and-
    # documentation-model-2026-08-14.md sec 4.2). An explicit `scope` argument
    # narrows further and is itself bounded by the caller's own reach, so a
    # workshop chat cannot ask its way into development just by naming it.
    caller_raw = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    my_reach = _reach_scopes(_caller_scope_key(caller_raw))
    requested = (args.get("scope") or "").strip()
    scopes = my_reach
    if requested:
        req_reach = _reach_scopes(requested)
        if req_reach is not None:
            scopes = req_reach if my_reach is None else (my_reach & req_reach)

    # `from_scope` is the caller's own scope, distinct from the reach SET above.
    # The `share` field (Phase 5) needs it: a `private` item is found only from
    # inside its own scope, and "which scope am I" is not recoverable from a set
    # of scopes I am allowed to see.
    from_scope = _caller_scope_key(caller_raw)

    # COUNTING IS A DIFFERENT OPERATION FROM RANKING, and it rides the same
    # tool rather than a second one: a caller who wants "what came up
    # repeatedly" should not have to know it is a different subsystem.
    if want_repeats:
        return _render_repeats(query, since, limit, scopes, from_scope)

    result = semantic_index.search(query, limit=limit, kinds=tuple(kinds),
                                   scope=scopes, since=since,
                                   from_scope=from_scope)
    hits, mode, rebuilding = result["hits"], result["mode"], result["rebuilding"]
    # ONE LINE PER CALL, so "does anyone use search" is a count and not an
    # argument. Recorded before the render, and never allowed to fail the call
    # it describes (services/search_log swallows its own errors).
    from services import search_log
    search_log.record(SEARCH_TOOL_NAME, query=query, kinds=list(kinds),
                      scope=from_scope, searched=result.get("searched", 0),
                      corpus=result.get("corpus", 0), mode=mode,
                      top_score=(round(hits[0]["score"], 4) if hits else None),
                      hits=len(hits), empty=not hits,
                      turn=_bound_turn(), thread=_bound_thread())
    # What the scope and `share` filters actually left in play, NOT the index
    # total. A tool that reports the total overstates its own coverage: on
    # 2026-08-23 this line said "6089 indexed" for searches the policy had
    # narrowed to as few as 2 passages, which is how an agent concludes there
    # is no prior art when there is.
    searched, corpus = result.get("searched", 0), result.get("corpus", 0)

    rebuild_note = ("\n(the index is rebuilding in the background; these results "
                     "are from what is on disk right now)" if rebuilding else "")

    if not hits:
        st = semantic_index.status()
        if not st["chunks"]:
            why = st["embedder_error"] or "it has not been built yet"
            return f"The search index is empty: {why}.{rebuild_note}"
        # An empty result is a real answer, and saying so plainly stops the model
        # from reading it as a malfunction and retrying the same query.
        #
        # COVERAGE IS STATED HONESTLY, and when it is partial the model is told
        # so it can widen the search rather than conclude the workshop is
        # silent on the subject.
        if searched < corpus:
            reach = (f"{searched} of {corpus} indexed passages were searchable "
                     f"from this scope and none is relevant")
        else:
            reach = (f"none of the {corpus} indexed passages is relevant")
        return (f"No passages in the workshop are relevant to that "
                f"({reach}, mode: {mode}). Nothing reachable from here has "
                f"been written about it.{rebuild_note}")

    # The mode is surfaced up front, not buried in a footer: a model that does
    # not know it got a degraded (lexical) result over-trusts it.
    out = [f"{len(hits)} passage(s) for: {query}  "
           f"[mode: {mode}; searched {searched} of {corpus} indexed passages]"
           f"{rebuild_note}", ""]
    for h in hits:
        # THE HIT NAMES ITSELF IN THE ONE VOCABULARY, so the next step is
        # possible. This line used to print the collector's raw locator: a
        # repo-relative path, or a bare thread id behind the word "chat". Every
        # one of those is unusable as an address -- `open_ref` reads refs, the
        # orientation map already tells a caller to CITE refs, and for a note
        # the raw path and the ref do not share a prefix, so a reader could not
        # convert one into the other. The ref contains at least as much as the
        # raw locator did in all four kinds, so nothing was traded away for it.
        where = h.get("item_ref") or h["ref"]
        stamp = (h["ts"] or "")[:10]
        scope = f" · scope {h['scope']}" if h.get("scope") else ""
        # THE STATUS IS SAID OUT LOUD, in both directions. These passages are
        # already reordered by it, but an adjustment the reader cannot see is
        # only a reordering: a historical doc that still makes the cut has to
        # ARRIVE labelled, or the model quotes a retired design as current.
        # That is the exact failure this repo keeps hitting, agents
        # rediscovering superseded plans and building against them. The
        # positive labels are here for the mirror-image reason: a model that
        # cannot tell the source of record from a passing remark about it has
        # to weigh five passages equally and will pick by tone.
        flag = _STATUS_FLAGS.get(h.get("status") or "", "")
        out.append(f"--- [{h['score']:.2f}] {h['kind']}: {h['title']} ({where}{scope}"
                   + (f" · {stamp}" if stamp else "") + flag + ")")
        out.append(h["text"])
        out.append("")
    return "\n".join(out)


# A repeats answer has to fit in ONE tool result. `limit` bounds statements and
# nothing bounds what hangs off each one, which is how an unfiltered call reached
# 52,565 characters. These two bound the render itself; both are stated in the
# output whenever they bite.
_REPEATS_MAX_CHARS = 24_000
_REPEATS_MAX_OCCURRENCES = 8


def _render_repeats(query: str, since: str, limit: int,
                    scopes: set | None, from_scope: str) -> str:
    """What was said more than once, rendered for a model to read.

    Every number here is a count over the transcript -- occasions, totals,
    threads, dates. None of it is a judgment about what a repetition means;
    that reading is the reader's to make, and the provenance is carried so the
    reader can make it from the messages themselves rather than from this
    summary."""
    from services import repetition, search_log

    result = repetition.repeats(query=query or None, since=since or None,
                                limit=limit, scope=scopes)
    rows = result["repeats"]
    search_log.record(SEARCH_TOOL_NAME, query=query or None, mode="repeats",
                      scope=from_scope, searched=result.get("considered", 0),
                      corpus=result.get("repeat_total", 0), hits=len(rows),
                      empty=not rows, turn=_bound_turn(), thread=_bound_thread())
    window = f" since {result['since']}" if result["since"] else ""
    if result.get("rebuilding") and not rows:
        return ("The repetition store is still being built from the chat log; "
                "nothing to report from it yet. Ordinary search still works.")
    if not rows:
        topic = f" about that{window}" if query else window
        return (f"Nothing was said more than once{topic} "
                f"({result['considered']} messages considered).")

    note = ("\n(the store is catching up with the newest messages)"
            if result.get("rebuilding") else "")
    # The head counts what is BELOW it, so it is written after the loop: it used
    # to promise `len(rows)` statements and the render can now stop short of
    # that, and a header that overstates its own body is the silent truncation
    # this bound exists to avoid, moved up one line.
    out: list[str] = []
    used = 0
    shown = 0
    for r in rows:
        block: list[str] = []
        first, last = (r["first_ts"] or "")[:10], (r["last_ts"] or "")[:10]
        span = f"{first} to {last}" if first != last else first
        block.append(f"--- said {r['total']}x on {r['occasions']} occasion(s) "
                     f"across {r['threads']} chat(s), {span}")
        block.append(r["text"])
        # The occurrences, so the reader can go read them rather than take this
        # count on trust. Bounded per statement and SAID to be bounded: a
        # statement made forty times listed forty provenance lines, and forty
        # lines of timestamps is not forty times more evidence than eight.
        prov = r["provenance"]
        block.append("  occurrences:")
        for occ in prov[:_REPEATS_MAX_OCCURRENCES]:
            block.append(f"  - {(occ['ts'] or '')[:16]}  {occ['ref']}  ({occ['title']})")
        if len(prov) > _REPEATS_MAX_OCCURRENCES:
            block.append(f"  - and {len(prov) - _REPEATS_MAX_OCCURRENCES} more "
                         "occurrence(s), not listed")
        # The cross-source half: the same thing already written down elsewhere.
        # Without it, "said six times" cannot be told apart from "said six
        # times and filed as a ticket after the second".
        ech = repetition.echoes(r["text"], scope=scopes, from_scope=from_scope)
        if ech:
            block.append("  also written down as:")
            for e in ech:
                flag = _STATUS_FLAGS.get(e.get("status") or "", "")
                block.append(f"  - {e['kind']}: {e['title']} ({e['ref']}){flag}")
        block.append("")

        size = sum(len(line) + 1 for line in block)
        # STOP ON A WHOLE STATEMENT AND SAY SO. This render is not bounded by
        # `limit`: limit caps the number of STATEMENTS, and the provenance and
        # echo lines under each are unbounded, so an unfiltered call produced
        # 52,565 characters and overran the caller's tool-result limit outright
        # (measured 2026-09-04). Overrunning it loses the whole answer, and a
        # silent cut loses the reader's ability to tell a short list from a
        # clipped one, so the answer ends early, on a boundary, and names what
        # it left out and how to ask for it.
        if shown and used + size > _REPEATS_MAX_CHARS:
            left = max(0, int(result["repeat_total"]) - shown)
            out.append(f"({left} more repeated statement(s) are not shown: the "
                       "rest would not fit in one answer. Narrow with `query`, "
                       "or a `since` window, or a smaller `limit`, to read "
                       "them.)")
            break
        out.extend(block)
        used += size
        shown += 1
    head = (f"{shown} of {result['repeat_total']} repeated statement(s)"
            f"{window}, most-repeated first "
            f"[{result['considered']} messages considered]{note}")
    return "\n".join([head, "", *out])


def _run_where_is() -> str:
    """The orientation map for this chat's scope. Single source of truth is
    services.orientation, shared with the injected L1 manifest."""
    scope = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    from services import orientation

    # Computed from what THIS server will actually answer for, not defaulted.
    # The manifest names a tool only where the grant is real, and the default
    # said "you have spawn_background" to every caller including the ones with
    # no chat behind them at all.
    #
    # This is the server's own offer, which the turn's allowlist can narrow
    # further and never widen. The copy that matters is the one injected into
    # the prompt (chat_context.orientation_manifest); that one is computed from
    # the chat's real allowlist and is exact.
    offered = {t["name"] for t in _tool_list()}
    text = orientation.manifest_text(
        scope,
        background=SPAWN_TOOL_NAME in offered,
        wakeup=WAKE_TOOL_NAME in offered,
        notify=NOTIFY_TOOL_NAME in offered,
    )
    return f"{text}\n\n{_corpus_lines(scope)}"


def _corpus_lines(scope_raw: str) -> str:
    """How big the searchable record is, and how much of it this chat reaches.

    HOW BIG THE CORPUS IS BELONGS WITH WHERE THINGS ARE. An agent could already
    be told that search finds past decisions; it could not find out whether
    there were twelve of them or twelve thousand until after a search came back
    empty, and an empty result means opposite things at those two sizes. It goes
    here rather than into a tool of its own because this tool already takes no
    arguments and is already the one the map points at.

    The map is computed live and is not part of the injected orientation block:
    these are counts for THIS installation at THIS moment, which is a fact about
    one workshop and not product doctrine, so it is answered when asked for
    rather than pasted into every prompt.
    """
    _pin_owner()
    from services import semantic_index

    from_scope = _caller_scope_key(scope_raw)
    try:
        m = semantic_index.corpus_map(scopes=_reach_scopes(from_scope),
                                      from_scope=from_scope)
    except Exception as e:  # noqa: BLE001
        return f"=== WHAT IS IN THE RECORD ===\n(the corpus map is unavailable: {e})"

    out = ["=== WHAT IS IN THE RECORD (so an empty search means something) ==="]
    if not m["chunks"]:
        why = m["embedder_error"] or "it has not been built yet"
        out.append(f"Nothing is indexed yet: {why}. A search will find nothing, "
                   "and that says nothing about what has been written.")
        return "\n".join(out)
    out.append(
        f"{m['reachable_documents']} of {m['documents']} documents "
        f"({m['reachable_chunks']} of {m['chunks']} passages) are searchable "
        f"from this chat's scope, written between {m['oldest']} and {m['newest']}."
    )
    for kind in ("note", "doc", "chat", "ticket", "vault"):
        row = m["by_kind"].get(kind)
        if not row:
            continue
        out.append(f"- {kind}: {row['reachable_documents']} of {row['documents']} "
                   f"({row['reachable_passages']} of {row['passages']} passages)")
    if m["stale"]:
        out.append("Something has changed since this was built; the next search "
                   "rebuilds it in the background and answers from what is on "
                   "disk meanwhile.")
    out.append("An empty search over this many documents is an answer. Over a "
               "few, it is mostly a statement about the corpus.")
    return "\n".join(out)


def _run_list_attachments() -> str:
    """Files uploaded to THIS chat: id, name, kind, size."""
    _pin_owner()
    from services import chat_attachments
    thread = _bound_thread()
    recs = chat_attachments.list_records()
    if thread:
        recs = [r for r in recs if (r.get("thread_id") or "") == thread]
    if not recs:
        return "No files have been uploaded to this chat."
    lines = [f"{len(recs)} file(s) in this chat:"]
    for r in recs:
        size = r.get("size")
        human = f"{size} bytes" if isinstance(size, int) else "unknown size"
        lines.append(f"- id {r.get('id')}: {r.get('name')}  ({r.get('kind')}, {human})")
    lines.append("Read one with get_file, by its id.")
    return "\n".join(lines)


def _run_get_file(args: dict) -> str:
    """One attachment's text, or its on-disk path for a binary."""
    _pin_owner()
    from services import chat_attachments
    att_id = (args.get("id") or "").strip()
    if not att_id:
        return "error: an attachment id is required (see list_attachments)."
    rec = chat_attachments.get(att_id)
    if rec is None:
        return f"No attachment with id {att_id} in this chat."
    name = rec.get("name") or att_id
    kind = rec.get("kind") or ""
    head = f"{name} (id {att_id}, {kind}, {rec.get('size')} bytes)"
    # A converted binary (pdf, docx) carries a text rendering: prefer it.
    derived = chat_attachments.derived_path(rec)
    if derived is not None:
        try:
            text = derived.read_text("utf-8", errors="replace")
            return _render_text(head, text)
        except Exception as e:  # noqa: BLE001
            return f"{head}\n(could not read the text rendering: {e})"
    blob = chat_attachments.blob_for(rec)
    if blob is None:
        return f"{head}\n(the file's bytes are missing from the store.)"
    if kind == "text":
        try:
            return _render_text(head, blob.read_text("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return f"{head}\n(could not read as text: {e})"
    # A binary with no text rendering (a zip, an image): hand back the real path
    # so a tool-capable chat can open it. This is a live tool result, not static
    # agent-facing copy, and there is no managed address for "operate on these
    # bytes with Bash".
    return (
        f"{head}\n"
        f"This is a binary file with no text rendering. Its path on disk is:\n"
        f"{blob}\n"
        "Open it with your own tools (for a zip, unzip it; for an image, view it)."
    )


def _render_text(head: str, text: str) -> str:
    if len(text) > _GET_FILE_MAX_CHARS:
        text = text[:_GET_FILE_MAX_CHARS] + "\n...(truncated)"
    return f"{head}\n\n{text}"


def _run_open_ref(args: dict) -> str:
    """The body behind a ref, bounded and labelled, for a caller that may see it."""
    _pin_owner()
    from services import refs, search_log, semantic_index

    ref_str = (args.get("ref") or "").strip()
    if not ref_str:
        return ("error: a ref is required. Search prints one on every hit, "
                "like workshop:doc/<path> or workshop:note/<scope>/<id>.")
    try:
        part = int(args.get("part") or 1)
    except (TypeError, ValueError):
        part = 1

    caller_raw = os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop").strip() or "workshop"
    from_scope = _caller_scope_key(caller_raw)
    scopes = _reach_scopes(from_scope)

    def _log(**fields) -> None:
        search_log.record(OPEN_REF_TOOL_NAME, ref=ref_str, scope=from_scope,
                          turn=_bound_turn(), thread=_bound_thread(), **fields)

    doc = refs.read_ref(ref_str, part=part)
    if doc is None:
        _log(refused="unresolvable")
        return (f"Nothing resolves for {ref_str}. Either it is not a ref this "
                f"tool reads ({', '.join(refs.READABLE_KINDS)}), or what it "
                "named is gone. Search again rather than guessing a path.")

    # THE SAME BOUNDARY A SEARCH RESPECTED. A tool that reads a document in full
    # is a way around the scope and share filters if it answers for a document
    # the searcher was never shown, so the decision is `_keep_mask`'s own, made
    # by the same function.
    if not semantic_index.reachable_item(doc["kind"], doc["scope"], doc["item_ref"],
                                         scopes=scopes, from_scope=from_scope):
        _log(refused="out_of_reach")
        return (f"{ref_str} is outside what this chat's scope reaches, so it is "
                "not readable from here. This is the same boundary search "
                "applies; it is not a failure to retry.")

    _log(part=doc["part"], parts=doc["parts"], total_chars=doc["total_chars"],
         kinds=[doc["kind"]], empty=not doc["total_chars"])

    where = f" \u00b7 scope {doc['scope']}" if doc["scope"] else ""
    stamp = f" \u00b7 {doc['ts'][:10]}" if doc["ts"] else ""
    flag = _STATUS_FLAGS.get(doc["status"], "")
    head = f"{doc['kind']}: {doc['title']} ({ref_str}{where}{stamp}{flag})"
    if not doc["total_chars"]:
        return f"{head}\n\n(this one has no body text.)"

    start = doc["offset"] + 1
    end = doc["offset"] + len(doc["text"])
    # SAY WHAT WAS OMITTED. A part that does not state its own range reads
    # exactly like a whole document, and a reader that cannot tell the
    # difference will summarise a fifth of a design as the design.
    if doc["parts"] > 1:
        span = (f"part {doc['part']} of {doc['parts']} "
                f"(characters {start} to {end} of {doc['total_chars']})")
        nxt = ("" if doc["part"] >= doc["parts"] else
               f"\n\n(continues: call open_ref again with part {doc['part'] + 1}.)")
        return f"{head}\n{span}\n\n{doc['text']}{nxt}"
    return f"{head}\n{doc['total_chars']} characters, complete.\n\n{doc['text']}"


def _bound_thread() -> str | None:
    """The chat this server instance belongs to, if any. Pinned at turn-start as
    WORKSHOP_QUERY_THREAD (app_registry.set_current_query_thread). Absent for a
    background/CLI spawn with no chat behind it, which is why spawn_background is
    only offered when it is present."""
    t = (os.environ.get("WORKSHOP_QUERY_THREAD") or "").strip()
    return t or None


def _bound_turn() -> str | None:
    """The TURN this server instance was spawned for, if any. Pinned at the same
    turn-start seam as the scope, owner and thread (WORKSHOP_QUERY_TURN).

    A retrieval log keyed only by chat cannot tell one turn's search from the
    next turn's, so it cannot answer whether the passage a turn found was the
    one it then read. That question is the whole reason the log exists."""
    t = (os.environ.get("WORKSHOP_QUERY_TURN") or "").strip()
    return t or None


# --- Declared `command` tools ride on this server ----------------------------
#
# Stage 4 of docs/platform-redesign/tools-are-declared-2026-08-23.md: a tool a
# user declared has to be REACHABLE, and this is how it is reached. Every
# `command` tool in `services/tool_registry` is listed below beside this
# server's own five and dispatched to `services/command_tools`, which validates
# its typed inputs, applies the unattended deny list to the resolved argv, and
# spawns it through `services/scoped_exec` -- the same confinement as the turn.
#
# EXTENDING THIS SERVER RATHER THAN ADDING A SECOND ONE. A second server would
# need its own launch spec in both MCP resolvers, its own entry in every
# `--mcp-config` this turn might get, and its own reason to be spawned -- four
# new places for a tool to exist in one and not the other, which is the exact
# shape of the four-uncoordinated-sources problem the design is undoing. Riding
# here means a command tool's CLI name is `mcp__workshop__<id>`, an ordinary MCP
# tool name on a server the turn already has, so it arrives at an agent through
# the argv `llm_router.tool_flags` already builds and through nothing else.
#
# The names below WIN a collision: a declared tool called `search` does not get
# to be this server's `search`. Precedence, not a refusal, because tools.json is
# an ordinary file and the platform's own surface must not depend on what is in
# it (`tool_registry.catalog` takes the same position).
_BUILTIN_TOOL_NAMES = (
    TOOL_NAME, SEARCH_TOOL_NAME, OPEN_REF_TOOL_NAME, SPAWN_TOOL_NAME,
    WHERE_IS_TOOL_NAME, LIST_ATTACHMENTS_TOOL_NAME, GET_FILE_TOOL_NAME,
    WAKE_TOOL_NAME, LIST_WAKEUPS_TOOL_NAME, CANCEL_WAKEUP_TOOL_NAME,
    NOTIFY_TOOL_NAME,
)

# The tools that only mean anything with a chat behind them, listed once so
# `_tool_list` cannot offer one of them and forget another.
_CHAT_BOUND_TOOLS = (
    (SPAWN_TOOL_NAME, SPAWN_TOOL_DESC, SPAWN_TOOL_SCHEMA),
    (WAKE_TOOL_NAME, WAKE_TOOL_DESC, WAKE_TOOL_SCHEMA),
    (LIST_WAKEUPS_TOOL_NAME, LIST_WAKEUPS_TOOL_DESC, LIST_WAKEUPS_TOOL_SCHEMA),
    (CANCEL_WAKEUP_TOOL_NAME, CANCEL_WAKEUP_TOOL_DESC, CANCEL_WAKEUP_TOOL_SCHEMA),
)


def _can_notify() -> bool:
    """Is there actually somewhere to send an operator notification?

    Offered only where it can be delivered. A tool that is listed and always
    fails is worse than an absent one: the model spends a call on it and then
    has to tell the operator that telling the operator did not work. An
    installation that has declared no messaging channel simply does not have
    this tool, and the prompt layers say so rather than hedging.
    """
    try:
        return operator_notify.configured()
    except Exception as e:  # noqa: BLE001
        print(f"workshop mcp: notify availability unknown: {e}", file=sys.stderr)
        return False


def _command_tools() -> list:
    """The declared `command` tools, or [] if the catalog cannot be read.

    Never raises: this runs inside `tools/list`, and a malformed tools.json must
    cost the turn its user tools, not its search."""
    try:
        from services import command_tools
        return [t for t in command_tools.declared() if t.id not in _BUILTIN_TOOL_NAMES]
    except Exception as e:  # noqa: BLE001
        print(f"workshop mcp: could not read declared command tools: {e}", file=sys.stderr)
        return []


def _scope_root() -> str:
    """The directory a command tool runs in: this process's own cwd.

    The server is spawned as a child of the turn's `claude` process, so inside a
    confined turn that cwd IS the scope root at `scoped_exec.MOUNTPOINT` -- a
    command tool gets the same filesystem view as the turn that called it, never
    a wider one."""
    return os.getcwd()


def _tool_list() -> list:
    tools = [
        {"name": TOOL_NAME, "description": TOOL_DESC, "inputSchema": TOOL_SCHEMA},
        {"name": SEARCH_TOOL_NAME, "description": SEARCH_TOOL_DESC,
         "inputSchema": SEARCH_TOOL_SCHEMA},
        # Beside search rather than anywhere else in this list: it is the second
        # half of one capability, and a caller reading the list in order meets
        # "read the whole of it" immediately after "find it".
        {"name": OPEN_REF_TOOL_NAME, "description": OPEN_REF_TOOL_DESC,
         "inputSchema": OPEN_REF_TOOL_SCHEMA},
        {"name": WHERE_IS_TOOL_NAME, "description": WHERE_IS_TOOL_DESC,
         "inputSchema": WHERE_IS_TOOL_SCHEMA},
        {"name": LIST_ATTACHMENTS_TOOL_NAME, "description": LIST_ATTACHMENTS_TOOL_DESC,
         "inputSchema": LIST_ATTACHMENTS_TOOL_SCHEMA},
        {"name": GET_FILE_TOOL_NAME, "description": GET_FILE_TOOL_DESC,
         "inputSchema": GET_FILE_TOOL_SCHEMA},
    ]
    # Only a real chat can spawn a background agent for itself, or arm an alarm
    # on itself. With no thread bound there is nothing to post back to and
    # nothing to wake.
    if _bound_thread():
        for name, desc, schema in _CHAT_BOUND_TOOLS:
            tools.append({"name": name, "description": desc, "inputSchema": schema})
        # Reaching the operator needs a chat AND somewhere to send: the message
        # links back to this conversation, and an installation that has declared
        # no messaging channel has nobody to send to.
        if _can_notify():
            tools.append({"name": NOTIFY_TOOL_NAME, "description": NOTIFY_TOOL_DESC,
                          "inputSchema": NOTIFY_TOOL_SCHEMA})
    try:
        from services import command_tools
        for tool in _command_tools():
            tools.append({
                "name": tool.id,
                "description": tool.description or tool.title or tool.id,
                "inputSchema": command_tools.input_schema(tool),
            })
    except Exception as e:  # noqa: BLE001
        print(f"workshop mcp: could not describe command tools: {e}", file=sys.stderr)
    return tools


def _run_command_tool(name: str, args: dict) -> str:
    """Run one declared command tool. Refusals (bad inputs, the deny list, a
    sandbox that could not be built) come back as exceptions and are rendered by
    the caller as a failed tool call, which is what they are."""
    from services import command_tools
    tool = command_tools.get(name)
    if tool is None:  # raced with a delete between tools/list and tools/call
        raise ValueError(f"no declared command tool named {name}")
    return command_tools.run(tool, args, workdir=_scope_root())


def _run_spawn(args: dict) -> str:
    """Launch a background agent for the bound chat. Returns a confirmation."""
    thread = _bound_thread()
    if not thread:
        return "Cannot spawn a background agent: this tool is not bound to a chat."
    owner_raw = (os.environ.get("WORKSHOP_QUERY_OWNER") or "").strip()
    try:
        owner_id = int(owner_raw)
    except ValueError:
        return "Cannot spawn a background agent: no owner is bound to this chat."
    scope = (os.environ.get("WORKSHOP_QUERY_SCOPE") or "workshop").strip() or "workshop"
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return "A background agent needs a prompt describing what to do."
    label = (args.get("label") or "").strip() or None
    from services import background_runs
    job_id = background_runs.launch(
        thread_id=thread, owner_id=owner_id, scope=scope, prompt=prompt, label=label)
    return (
        f"Background agent started (id {job_id}"
        + (f", '{label}'" if label else "")
        + "). It is running now; its result will appear in this chat when it "
        "finishes, and the chat will continue on its own. Tell the operator you "
        "have kicked it off, in one line, and carry on."
    )


def _run_wake(args: dict) -> str:
    """Arm a wake-up on the bound chat."""
    thread = _bound_thread()
    if not thread:
        return "Cannot arm a wake-up: this tool is not bound to a chat."
    _pin_owner()
    from services import installation, wakeups

    try:
        when = wakeups.resolve_when(in_minutes=args.get("in_minutes"),
                                    at=args.get("at"))
        record = wakeups.arm(thread, reason=(args.get("reason") or ""), due_at=when)
    except ValueError as e:
        # A refusal the model can act on, not a stack trace. The whole value of
        # this tool is that the alarm goes off when it was meant to, so a time
        # that could not be read is worth one sentence saying so.
        return f"No wake-up was armed: {e}."
    local = when.astimezone(installation.local_tzinfo())
    return (
        f"Wake-up armed (id {record['id']}) for "
        f"{local.strftime('%Y-%m-%d %H:%M %Z').strip()}. A new turn will start "
        "in this chat then, carrying your reason. It survives this turn ending "
        "and a restart. Tell the operator when you will be back, in one line, "
        "and carry on."
    )


def _run_list_wakeups() -> str:
    thread = _bound_thread()
    if not thread:
        return "Cannot list wake-ups: this tool is not bound to a chat."
    _pin_owner()
    from services import wakeups

    armed = wakeups.read(thread)
    if not armed:
        return "No wake-ups are armed on this chat."
    return (f"{len(armed)} wake-up(s) armed on this chat:\n"
            + "\n".join(wakeups.render(w) for w in armed)
            + "\nCancel one with cancel_wakeup, by its id.")


def _run_cancel_wakeup(args: dict) -> str:
    thread = _bound_thread()
    if not thread:
        return "Cannot cancel a wake-up: this tool is not bound to a chat."
    _pin_owner()
    from services import wakeups

    wake_id = (args.get("id") or "").strip()
    if not wake_id:
        return "error: a wake-up id is required (see list_wakeups)."
    record = wakeups.cancel(thread, wake_id)
    if record is None:
        return (f"No wake-up with id {wake_id} is armed on this chat. It may "
                "have already fired or been cancelled.")
    return f"Cancelled {wake_id}. It will not fire."


def _run_notify(args: dict) -> str:
    """Send one operator notification and report what the transport answered.

    The return is deliberately not "sent" on anything less than a delivery. This
    tool exists because the operator is somewhere else; an assistant that says
    "I let you know" over a failed send has produced the exact silence the tool
    was added to end, and has told the operator it did not happen.
    """
    thread = _bound_thread()
    if not thread:
        return "Cannot notify the operator: this tool is not bound to a chat."
    _pin_owner()
    message = (args.get("message") or "").strip()
    if not message:
        return "Nothing was sent: a notification needs a message."
    # The chat's own context key, read rather than guessed: the scope this
    # server is bound to is a knowledge scope, not the key the chat route
    # addresses a conversation by, and a link built from the wrong one opens
    # somebody else's conversation.
    try:
        context_key = ((chat_log.read_meta(thread) or {}).get("context_key") or "")
    except Exception:  # noqa: BLE001 - a missing link never costs the message
        context_key = ""
    result = operator_notify.send(
        message, link=operator_notify.chat_link(thread, context_key))
    if result.get("ok"):
        return ("Delivered to the operator, with a link back to this chat. They "
                "may not read it immediately; do not wait on a reply unless you "
                "were going to wait anyway.")
    return (f"NOT delivered: {result.get('error', 'unknown error')}. The operator "
            "has not been told. Say so plainly rather than implying word was sent.")


def _handle(msg: dict) -> dict | None:
    """Return a JSON-RPC response dict, or None for a notification (no reply)."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or DEFAULT_PROTOCOL
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"tools": _tool_list()},
        }
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        # A declared command tool is dispatched only after the built-ins, the
        # same precedence `_tool_list` advertises.
        is_command_tool = name not in _BUILTIN_TOOL_NAMES and any(
            t.id == name for t in _command_tools())
        if name not in _BUILTIN_TOOL_NAMES and not is_command_tool:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            if is_command_tool:
                text = _run_command_tool(name, params.get("arguments") or {})
            elif name == SEARCH_TOOL_NAME:
                text = _run_search(params.get("arguments") or {})
            elif name == OPEN_REF_TOOL_NAME:
                text = _run_open_ref(params.get("arguments") or {})
            elif name == SPAWN_TOOL_NAME:
                text = _run_spawn(params.get("arguments") or {})
            elif name == WHERE_IS_TOOL_NAME:
                text = _run_where_is()
            elif name == LIST_ATTACHMENTS_TOOL_NAME:
                text = _run_list_attachments()
            elif name == GET_FILE_TOOL_NAME:
                text = _run_get_file(params.get("arguments") or {})
            elif name == WAKE_TOOL_NAME:
                text = _run_wake(params.get("arguments") or {})
            elif name == LIST_WAKEUPS_TOOL_NAME:
                text = _run_list_wakeups()
            elif name == NOTIFY_TOOL_NAME:
                text = _run_notify(params.get("arguments") or {})
            elif name == CANCEL_WAKEUP_TOOL_NAME:
                text = _run_cancel_wakeup(params.get("arguments") or {})
            else:
                text = _build_digest()
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"error: {e}"}],
                               "isError": True}}
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": text}], "isError": False}}
    # Unknown method with an id -> method-not-found; notification -> ignore.
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> None:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = _handle(msg)
        except Exception as e:  # noqa: BLE001 - never die on one bad message
            resp = ({"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32603, "message": str(e)}}
                    if msg.get("id") is not None else None)
        if resp is not None:
            out.write(json.dumps(resp) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
