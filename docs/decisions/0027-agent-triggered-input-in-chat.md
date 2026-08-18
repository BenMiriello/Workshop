# 0027 - Agent-triggered interactive input in chat

Status: accepted (2026-08-18)

## Context

An agent sometimes needs the operator to answer a question before it can go on:
pick between two designs, confirm a name, choose a dependency. Claude Code's
built-in `AskUserQuestion` tool does this in an interactive terminal: it renders
a prompt with options and blocks the turn until the human answers.

In this async web chat it does not work, and worse, it fails silently. The chat
runs `claude -p`, which has no terminal to render the question. Under the
permission channel the tool call surfaces only as a permission chip
("Allowed · AskUserQuestion"): the operator can approve the *call*, but the
question itself, with its options, never renders as anything he can answer.
Approving grants permission to ask and returns no answer, so the model reads an
empty result as "they didn't pick" and proceeds, choosing for the operator a
question he was never shown. Observed three times on 2026-08-17, once past a
permanent dependency decision.

The first fix denied the tool. That stopped the silent-proceed but deleted the
capability, which was wrong: the operator wants structured questions, he wants
them to *work*. This ADR defines the mechanism that makes them work, and
generalizes it to any agent-triggered input, not just a multiple-choice
question.

## Decision

Agent-triggered input is a **ui-card**, not a blocking tool.

The chat already has a first-class primitive for "custom interactive UI the
agent emits inline": the `ui-card`. It renders from the design system, it is
never auto-applied, it persists in the transcript, and it survives a reload or a
device switch. We add one card kind, `prompt`, whose purpose is to *collect
input and return it to the conversation*:

1. The agent emits a `prompt` card (a question, plus `choice`/`select` options
   or free-text `text`/`textarea` fields) and **ends its reply**.
2. The card renders as real answerable controls in the transcript.
3. The operator answers and submits. The submit handler composes the answer and
   sends it back into the chat as the operator's own message.
4. The agent resumes on that message (the session continues via `--resume`), now
   with the answer, and carries on.

This generalizes past a single question: a `prompt` card carries arbitrary
fields, so it is equally "pick one of these", "confirm this name", or "fill in
these three values". It is the async-native shape of `AskUserQuestion`.

`AskUserQuestion` stays denied (ADR-adjacent, `llm_router.BASELINE_DISALLOWED_TOOLS`):
it is the terminal-only tool that cannot render here. The `prompt` card replaces
it.

## Why not block the turn within a single turn

Matching the terminal exactly would mean the turn stays running, blocked, while
the operator takes minutes or hours to answer. That couples the answer to a
fragile long-lived process: the `claude -p` child, its MCP servers, and the SSE
socket all have to survive the whole wait, and a restart, a dropped socket or a
closed laptop loses the pending question. The chat's own design principle
(durable state lives in the store, turns end and resume) argues the other way.

Ask-and-resume keeps the question as a durable card and the answer as a durable
message. Nothing has to stay blocked. It survives everything the transcript
survives. The only visible difference from the terminal is that the agent's
"continue" is the next turn rather than the same one, which is seamless because
the session resumes with full context.

## Consequences

- One new card kind (`prompt`) and one new field type (`choice`, rendered as the
  kit's `Segmented` for tap-friendly options). Both ride the existing card
  rendering, persistence, and the `resolveCardActions` fallback.
- The card context gains one capability, `answer(text)`, which posts the
  operator's answer as a message through the normal send pipeline.
- The agent is told, in the ui-card protocol, to emit a `prompt` card and stop
  when it needs an answer, and that the answer arrives as the next message.
- No new blocking infrastructure, no dependence on undocumented CLI behavior.
