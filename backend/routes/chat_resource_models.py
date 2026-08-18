"""Response models for the resources a chat REACHES, as opposed to what a chat
IS (ADR 0023 clause 6). Sibling of `chat_models.py`, which keeps the
conversation itself: the thread document, its event log, its branches, drafts,
prefs, work and the results of operating on it.

WHY THE SPLIT. `chat_models.py` crossed `check_module_layout.py`'s 800-line
big-file threshold when the thread-state maps were given real types. That check
describes itself as "the mechanical trigger for that review, not a line-count
opinion", so this is the review: the models below were the ones that are not
about the conversation at all. A chat's context report, the MCP servers it may
run with, the file index it can attach from and the agents it can mention are
four separate resources that a chat merely points AT. None of them is part of a
thread's state, none is written by `chat_store`, and nothing in the
conversation's own models refers to them, so the seam costs no import.

`AttachmentRef` deliberately did NOT move. It is the reference a queued message
or a saved draft carries inline, so it belongs with those; `AttachmentRow` here
is the index entry, which is a different and much larger thing. That the two
were ever confused is why the wire said `list[dict[str, Any]]`.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ThreadContext(BaseModel):
    """The context meter. OUR estimate, measured over the prompt a turn would
    actually send -- summary included -- because an estimate over a trimmed
    history while turns send the whole thing is how a chat read 12% while
    re-sending every message ever written in it."""

    used_tokens: int
    window_tokens: int
    pct: int
    model: str | None = None
    source: str


class ContextDetail(BaseModel):
    """The breakdown EXPLAINS the meter; it must never quietly replace it. Our
    `used_tokens` stays the headline number, and the CLI's own reading arrives
    beside it as `cli_used_tokens` against `cli_window_tokens`, computed on a
    different window.

    `available: false` with a `reason` is what an unreadable breakdown looks
    like. The CLI-derived lists stay open: `/context` is parsed out of another
    process's markdown and its section set belongs to that CLI."""

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    window_tokens: int
    used_tokens: int
    available: bool
    # Epoch seconds, and it is when the CLI actually RAN, not when the cache was
    # read: reporting the moment of a cache hit would be a lie about freshness.
    checked_at: float | None = None
    cached: bool
    reason: str | None = None
    cli_used_tokens: int | None = None
    cli_window_tokens: int | None = None
    categories: list[dict[str, Any]] | None = None
    mcp: list[dict[str, Any]] | None = None
    agents: list[dict[str, Any]] | None = None
    memory: list[dict[str, Any]] | None = None
    skills: list[dict[str, Any]] | None = None


# ------------------------------------------------------------------------ mcp

class McpServer(BaseModel):
    """One row of `claude mcp list`, annotated by `mcp_registry`.
    `spec_available` false means the server cannot be re-declared, so a turn
    running a subset cannot include it -- saying so is the whole point, since the
    alternative is a switch that appears to work and silently removes a server's
    tools."""

    model_config = ConfigDict(extra="allow")

    name: str
    spec_available: bool | None = None
    scope: str | None = None
    source: str | None = None
    shadowed: bool | None = None


class McpHealth(BaseModel):
    """`available: false` with a `reason` is what an unrunnable CLI looks like.
    There is no fallback list, because a made-up server list would report health
    for things that may not exist. `enabled` is this chat's own selection, null
    for 'all'."""

    available: bool
    reason: str | None = None
    servers: list[McpServer] | None = None
    counts: dict[str, Any] | None = None
    checked_at: float | None = None
    cached: bool
    enabled: list[str] | None = None


class McpSelection(BaseModel):
    """Null means ALL. Sending every server the machine currently has is stored
    as 'all' rather than as that frozen list."""

    enabled: list[str] | None = None


# ---------------------------------------------------------------- attachments

class AttachmentUploaded(BaseModel):
    id: str
    name: str
    kind: str
    size: int
    created_at: str | None = None


class AttachmentRow(BaseModel):
    """`missing` says the index has a row whose blob is gone, which the list
    shows rather than hiding: a file that cannot be opened should say so where
    you would go looking for it."""

    id: str
    name: str
    kind: str
    size: int
    scope: str
    thread_id: str
    created_at: str
    missing: bool


class AttachmentList(BaseModel):
    files: list[AttachmentRow]
    count: int
    scopes: list[str]


class AttachmentDeleted(BaseModel):
    deleted: str


# ----------------------------------------------------------------- mentionable

class MentionableAgents(BaseModel):
    """Server-resolved because the rule is a policy, not a display preference: an
    agent is addressable only if it opted in AND this chat's scope reaches it.
    Computing it on the client would put the policy in two places."""

    scope: str
    agents: list[dict[str, Any]]
