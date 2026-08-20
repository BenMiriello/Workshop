"""Provider-agnostic compaction seam.

Workshop's chat backend drives the Claude Code CLI headless today and will
eventually also speak the raw Claude API. Both providers need the same
user-facing operation ("Compact" shrinks the window, keeps the transcript,
nothing is deleted) but the CLI and the API implement it through genuinely
different mechanisms: the CLI has no reachable native compaction from headless
mode at all (see the docstring on `routes.chat.compact_thread`), so Workshop's
own fold stands in for it; the API's own `compact_20260112`/
`clear_tool_uses_20250919` edits are real provider primitives once the API
backend exists. This module is the interface that lets the route layer call
one thing regardless of which provider is live, per
`docs/platform-redesign/compaction-both-paths-plan-2026-08-20.md`.

Only one adapter is implemented here: `ClaudeCLIAdapter`, a thin wrapper
around the fold/detach functions that already live in `routes.chat`. It takes
those functions as injected callables rather than importing `routes.chat`
directly, because `routes.chat` is the module that will import THIS one to
build the adapter -- importing back would be circular. The Claude API adapter
described in the plan doc is future work, gated on the API backend existing at
all, and is deliberately not stubbed out here: a `NotImplementedError` class
would be exactly the committed-stub pattern this repo doesn't allow, so it
simply doesn't exist yet.
"""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

from services import chat_context


class CompactionCapabilities(t.TypedDict):
    """Static facts about what a provider's adapter can actually do, so the
    route layer (and, later, a UI that wants to explain itself) can ask a
    question instead of branching on `if provider == "cli"`."""

    # Can this adapter ask the provider itself to run a real compaction?
    native_manual: bool
    # Does the provider ever compact on its own, uncontrolled by Workshop?
    native_auto: bool
    # Can old tool output be evicted surgically, short of full summarization?
    can_evict_tool_output: bool
    # Does compacting avoid invalidating the provider's prompt cache prefix?
    preserves_cache_prefix: bool


@dataclass
class CompactionResult:
    """Mirrors the `(folded, summary, folded_through, error)` tuple
    `_fold_summary` already returns, named so callers stop counting
    positions."""

    folded: int
    summary: str
    folded_through: int
    error: str = ""


@dataclass
class ClearResult:
    session_reset: bool


@dataclass
class EvictionResult:
    """`evicted=0` is a real, honest answer on an adapter whose capabilities()
    reports `can_evict_tool_output=False` -- there is no primitive to call, so
    it is a no-op, not an unfinished method."""

    evicted: int = 0


class CompactionAdapter(t.Protocol):
    """One implementation per provider. The route layer calls only this
    interface; nothing above it should need to branch on which provider is
    live."""

    def capabilities(self) -> CompactionCapabilities: ...

    async def compact(self, thread_id: str, thread: dict) -> CompactionResult: ...

    async def clear(self, thread_id: str, thread: dict) -> ClearResult: ...

    async def evict_tool_output(self, thread_id: str, thread: dict) -> EvictionResult: ...


class ClaudeCLIAdapter:
    """The provider Workshop actually runs today. Every method delegates to
    the existing fold/detach functions in `routes.chat`, passed in at
    construction so this module never imports that one.

    `capabilities()` reports the true CLI facts established by the
    2026-08-12 measurement in `routes.chat.compact_thread`'s docstring: the
    manual `/compact` command is confirmed dead in headless mode (a leading
    slash over stream-json is parsed into a local-command block nothing
    executes), there is no control-protocol request for it either, and
    automatic threshold-triggered compaction inside a `--resume`d session is
    a genuinely open question -- not confirmed to happen, not confirmed not
    to -- so `native_auto` stays `False` until that question is settled
    rather than being reported as a fact this adapter doesn't have.
    """

    def __init__(self, fold_summary, land_fold, detach_live_session):
        self._fold_summary = fold_summary
        self._land_fold = land_fold
        self._detach_live_session = detach_live_session

    def capabilities(self) -> CompactionCapabilities:
        return {
            "native_manual": False,
            "native_auto": False,
            "can_evict_tool_output": False,
            "preserves_cache_prefix": False,
        }

    async def compact(self, thread_id: str, thread: dict) -> CompactionResult:
        """Compute half only, matching `_fold_summary`'s own split: the
        landing (writing the summary, moving the cut, detaching the session)
        stays the route handler's job, because it has to serialize against a
        live turn and a cancel request the same way it does today."""
        fold_n, summary, folded_through, error = await self._fold_summary(thread_id, thread)
        return CompactionResult(fold_n, summary, folded_through, error)

    async def clear(self, thread_id: str, thread: dict) -> ClearResult:
        provider = chat_context.get_provider(thread.get("context_key"))
        return ClearResult(
            session_reset=self._detach_live_session(thread_id, thread, provider))

    async def evict_tool_output(self, thread_id: str, thread: dict) -> EvictionResult:
        # No CLI primitive does this. A real answer, not a placeholder.
        return EvictionResult(evicted=0)
