"""L1 orientation manifest.

The small, fixed "how to reach things" map every tool-capable chat and agent is
given, so nothing has to grep the tree to discover where uploads, notes, docs,
tickets or runs live. That rediscovery, an agent narrating "the uploads are in
the content-addressed blob store, extracting the zip" after finding it by hand,
is the exact failure this removes.

It is TOOL-CENTRIC on purpose: it names the verb that reaches each kind of
thing, never a raw filesystem path. Raw OS paths in agent-facing text are a
reviewer-gated violation (scripts/check_no_raw_paths.py), and pointing at a tool
is also what actually stops the grep. This module is the single source of truth
shared by the prompt injection (chat_context.orientation_manifest) and the
`where_is` MCP tool (backend/mcp/workshop_query_server.py).
"""
from __future__ import annotations


def _scope_line(scope_key: str | None) -> str:
    key = (scope_key or "workshop").strip() or "workshop"
    if key in ("workshop", "general", "global"):
        return (
            "This chat is at the workshop root: it can reach every app and "
            "project in this account, but not another account's data."
        )
    if key == "development":
        return "This chat is the development scope: it works on the Workshop platform itself."
    if key.startswith("project:") or key.startswith("app:"):
        return (
            f"This chat is scoped to {key}: its notes, tickets and runs are that "
            "one place's, and search stays inside it."
        )
    if key.startswith("agent:"):
        who = key.split(":", 1)[1]
        return f"This chat is agent {who}: it reads and writes that agent's own memory."
    if key.startswith("role:"):
        return f"This chat carries the {key} layer: procedural memory for that kind of work."
    return f"This chat's scope is {key}."


def manifest_text(scope_key: str | None = None, *, background: bool = True,
                  wakeup: bool = False, notify: bool = False) -> str:
    """The orientation block. `scope_key` is the chat's knowledge scope
    ("workshop", "development", "project:<slug>", "app:<name>", "agent:<id>").

    `background` says whether this chat is actually granted `spawn_background`,
    `wakeup` whether it is granted `wake_me`, and `notify` whether it can
    actually reach the operator away from the app. Naming a tool the chat cannot
    call is the told-but-absent error this manifest exists to prevent, not
    commit, so each line is present exactly where its grant is real. Neither is
    ever hedged: a map that says a road "may exist" is not a map."""
    return (
        "=== WHERE THINGS ARE (orientation; reach by tool, don't grep) ===\n"
        + _scope_line(scope_key) + "\n"
        "You do not need to search the filesystem to find the Workshop's own "
        "things. Reach each kind by its tool:\n"
        "- Files uploaded to THIS chat: `list_attachments` lists them; `get_file` "
        "returns one file's text, or its on-disk path for a binary (a zip, an "
        "image) so you can open it directly. Don't hunt the filesystem for an "
        "upload, and don't assume where it lives.\n"
        "- Past decisions, notes, docs, and prior chats: `search` finds them by "
        "meaning. An empty result means there is genuinely no prior art, not that "
        "the search failed. Narrow it to a window with `since` ('30d', '2w', a "
        "date) when the question is about lately rather than ever.\n"
        # COUNTING, WHICH RANKING CANNOT DO. A thing said six times is six
        # ordinary passages competing for one result slot, and the per-source
        # cap actively suppresses the repetition -- so an agent that only ever
        # ranks will read a standing complaint as a passing remark. This line
        # is unconditional because it rides on `search`, which every chat has.
        "- What has been said MORE THAN ONCE: `search` with `repeats: true` "
        "counts instead of ranking. It returns the operator's own repeated "
        "statements, each with how many separate occasions it was said on, when "
        "it was first and last said, every message it was said in, and where the "
        "same thing is already written down as a doc, note or ticket. Use it "
        "before treating something as newly raised, and whenever the question is "
        "what keeps coming up. It reports counts and dates; what a repetition "
        "means is yours to judge from the messages it cites.\n"
        # FINDING IS NOT READING. A hit is a passage the chunker cut, bounded,
        # with at most a couple per document, so an agent that stops at the hit
        # is answering from a slice and cannot tell that it is one. This line is
        # unconditional for the same reason the repeats line is: it rides on
        # `search`, which every chat has.
        "- The WHOLE of something search found: `open_ref`, with the ref printed "
        "on the hit. Search returns a slice of a document, never the document; "
        "read it before treating a passage as the last word on anything. Long "
        "bodies come back in numbered parts and each one says how much is left.\n"
        "- How much there is to search at all: `where_is` reports how many "
        "documents and passages this scope reaches. An empty search means "
        "something very different over a dozen documents than over thousands.\n"
        "- What is going on now (open tickets, running and recent runs, the note "
        "titles in scope): `query`.\n"
        # DISPATCH, the one kind of thing this map never had a line for. Every
        # other line answers "where do I find X"; none answered "where does work
        # GO", so a chat had a route to every object in the system and none to
        # the two places work outlives a conversation. Added 2026-09-17, when
        # three prompt blocks were found answering that question differently and
        # the ordered rule that replaced them (chat_context.DELEGATION_RULE) had
        # no counterpart here. Deliberately names no tool: filing and opening
        # are not tool calls today, and naming one would be the told-but-absent
        # error this file exists to prevent.
        "- Where work GOES when it does not belong in this reply: a standing "
        "actor of its own means opening a THREAD (its own name, scope and "
        "permissions); a reviewable diff and a retry if it fails means filing a "
        "TICKET and assigning it, since one assigned to nobody is never picked "
        "up. Neither is a tool call.\n"
        "- When you cite a doc, note or ticket, write it as a managed link "
        "(`workshop:doc/<path>`, `workshop:note/<scope>/<id>`, "
        "`workshop:ticket/<id>`) so it renders as a chip, not raw text.\n"
        # THE ONE TOOL THAT ANSWERS "HOW DO I RUN SOMETHING IN THE BACKGROUND".
        # Added 2026-08-31. A chat's subagents run inline (a backgrounded one is
        # killed with the turn), and the guard that enforces that used to say
        # "backgrounding is not available here" without naming the tool that
        # provides it -- so a caller who wanted out-of-band work read a refusal
        # instead of a redirect. This manifest exists so nothing has to
        # rediscover where a capability lives; the capability was missing from it.
        + ("- Work that should outlive this turn and come back as a LATER turn: "
           "`spawn_background`. A subagent (the Agent tool) belongs to THIS turn "
           "instead, foreground or background, and the turn stays open until it "
           "reports.\n" if background else
           "- A subagent (the Agent tool) belongs to THIS turn, foreground or "
           "background, and the turn stays open until it reports. No work you "
           "start runs on past the end of this turn.\n")
        # THE ALARM CLOCK. A chat had no durable way to wake itself at all
        # until 2026-09-01, so nothing in this map answered "how do I come back
        # to this later" -- the honest answer was that you could not, and the
        # dishonest one was to promise the operator you would.
        + ("- Something that has to happen at a TIME rather than now: `wake_me` "
           "arms a durable alarm on this chat and a new turn starts here when "
           "it comes due, carrying the reason you gave. `list_wakeups` and "
           "`cancel_wakeup` manage what is armed.\n" if wakeup else "")
        # REACHING A PERSON WHO IS NOT HERE. Everything else on this map is a
        # way to find something; this is the only way to be found. Added
        # 2026-09-01, when jobs and scripts could reach the operator and a chat
        # could not, so the answer to "they asked for this and left" was to
        # write into a thread nobody was looking at.
        + ("- Telling the operator something when they are NOT in the app: "
           "`notify_operator` reaches them directly and reports whether it "
           "arrived. For a request that is finished or stuck, not for progress.\n"
           if notify else "")
        +
        "- `where_is` restates this map any time you need it."
    )
