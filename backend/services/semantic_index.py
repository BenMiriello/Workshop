"""Semantic search over the workshop's own writing: notes, docs and past chats.

THE POINT OF THIS MODULE IS THAT NOTHING IT PRODUCES IS INJECTED. An agent asks
a question and gets ranked passages back, with the body and where it came from.
That is the whole contract, and it replaces a mechanism that did the opposite.

WHAT IT REPLACES, so nobody rebuilds it
---------------------------------------
Auto-recall used to run `knowledge.search` on every message and paste the top 3
notes into the prompt. That search scored a note as `sum(hay.count(t))`: raw term
frequency, no stopword list, no IDF, no length normalisation. Longer notes
contain more of every common word, so the ranking was really a length sort.
Probed live on 2026-08-12, a query of pure stopwords ("the a of to and is in")
returned the same three notes as a real question, and those three were the three
largest files in the scope. Meanwhile no agent could READ a note at all: the one
agent-facing tool listed titles and discarded its query parameter.

So the system pushed what it guessed and refused what was asked for. This
inverts that, and it is the only retrieval path that should exist.

WHY OLLAMA AND NOT AN API
-------------------------
It is already running on this box, `llm_router` already speaks to it, and
`nomic-embed-text` measured 39ms for a query and 6ms/doc batched. Local means no
per-query cost and no egress for the operator's own notes, which matters more
here than model quality does at this corpus size.

WHY NO VECTOR DATABASE
----------------------
The whole corpus is about 8,000 chunks. A float32 matrix of that shape is 24MB,
a brute-force dot product over it is sub-millisecond, and numpy is already in the
venv. A vector database would be a service to run, monitor and back up, in
exchange for nothing measurable until this is 100x bigger. Storage is two files.

PROVENANCE IS RECORDED EVEN WHERE NOTHING READS IT YET
------------------------------------------------------
Every chunk carries `kind`, `ref`, `title`, `scope` and `ts`. The operator asked
for this specifically: the write side of memory is being redesigned (ticket
31b0e762) and the point is that what exists now stays reconstructable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from services import entity_kinds
from wsplatform import paths

log = logging.getLogger("workshop.semantic_index")

# Embeddings run through services/embeddings.py, an in-process local ONNX model
# (bge-small-en-v1.5, 384-d) with no daemon, no network, no GPU dependency. This
# replaced the Ollama /api/embed backend on 2026-08-20. The provider records its
# own name; this module tags the index with it and rebuilds on a model change
# (see _index_model / load), which is what stops two models' vectors from ever
# sharing one matrix.

# ~1200 characters is about 300 tokens: long enough to carry an argument, short
# enough that a hit points at a paragraph rather than a document. The overlap
# stops a fact that straddles a boundary from being unfindable from either side.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

# A chat log is mostly short messages, so they are grouped up to the same size
# rather than embedded one per message: 8,000 one-line chunks would rank noise
# above substance and cost more to build.
#
# `ticket` joins the corpus so "have we decided X" or "is there already a
# ticket for Y" can actually find the work, not just the discussion and the
# design doc (design doc sec 8: "kinds: [ticket] is how documentation and
# tickets integrate without merging the stores: separate stores, separate
# lifecycles, one query surface, one ref format").
# The platform's own corpora. An app contributes more through
# `AppSpec.index_collectors`; a personal Markdown vault used to be listed here
# and is now declared by the app that owns it.
PLATFORM_KINDS = entity_kinds.PLATFORM_KINDS

# Indexed on every build. A corpus an app marks opt-in (its id absent from
# `AppSpec.index_collectors_default`) is indexed only when asked for by name, so
# a search about routing code never surfaces a personal note. See the decision in
# docs/memory-and-transcript-simplification-2026-08-12.md.
# Equal to PLATFORM_KINDS today: every platform corpus is indexed by default.
# A derivation rather than a second copy, so the two cannot silently disagree.
# Give it its own literal at the moment a corpus becomes opt-in.
PLATFORM_DEFAULT_KINDS = PLATFORM_KINDS


def _app_collectors() -> tuple[dict, set[str]]:
    try:
        from services import app_registry

        return app_registry.installed_index_collectors()
    except Exception:
        return {}, set()


def collectors() -> dict:
    """kind -> collector, platform first then installed apps. Resolved per call:
    installing an app makes its corpus searchable without a restart."""
    app, _ = _app_collectors()
    return {**_COLLECTORS, **app}


def kinds() -> tuple[str, ...]:
    return tuple(collectors())


def default_kinds() -> tuple[str, ...]:
    _, app_default = _app_collectors()
    extra = tuple(k for k in kinds() if k in app_default and k not in PLATFORM_DEFAULT_KINDS)
    return PLATFORM_DEFAULT_KINDS + extra


def __getattr__(name):
    """`KINDS` and `DEFAULT_KINDS` were module constants and are read by the MCP
    query server and the tests. They are now derived, so they are served as
    module attributes rather than renamed at every call site."""
    if name == "KINDS":
        return kinds()
    if name == "DEFAULT_KINDS":
        return default_kinds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Below this cosine score, a hit is not a hit and the search returns nothing.
#
# THE FLOOR IS THE POINT. Cosine similarity from this model has a high,
# uninformative baseline, so a search with no threshold ALWAYS returns five
# confident-looking results. That is the failure mode of the thing this
# replaces, rebuilt with better maths, and it is worse than saying nothing
# because the passages read as relevant.
#
# RE-MEASURED FOR BGE ON 2026-08-23, against the real 12,532-chunk index.
# Commit 7ce3cae swapped nomic for BAAI/bge-small-en-v1.5 on 2026-08-20 and its
# own message said "MIN_SCORE was tuned for nomic and needs re-measuring for
# bge (noted)"; it never was, and at the inherited 0.58 fifteen of eighteen
# nonsense queries cleared the floor. "banana helicopter tuesday" scored 0.647.
# The tool's contract that an empty result is an answer was simply false.
#
# What the 48-query probe found, and it is not the clean gap nomic had:
#
#   full natural-language questions with an answer here  0.740 - 0.858 (n=18)
#   real but SHORT or oblique questions                  0.657 - 0.792 (n=12)
#     eight of those twelve landed BELOW the 0.740 above: "bwrap" 0.718,
#     "cursor not-allowed" 0.713, "512 token window" 0.703, "Plaid" 0.700,
#     "why did we stop using nomic" 0.657 -- and that last one retrieved the
#     right doc, so it is a real answer sitting in the nonsense band
#   off-corpus questions in plain English                0.543 - 0.683 (n=14)
#   degenerate token strings and pure stopwords          0.705 - 0.733 (n=4)
#     ("xxxx yyyy zzzz wwww vvvv", "zqxjv mmmm plrt gggh")
#
# THE BANDS OVERLAP. There is no value that admits every real query and rejects
# every nonsense one, because bge scores a two-word real query about the same
# as a sentence about wombats. 0.735 would separate all 36 of the first probe's
# queries and reject seven real ones from the second. So the floor is set at
# 0.65: the highest value that rejected NOTHING real in 30 real queries. It
# admits 7 of 18 nonsense queries, against 15 of 18 at the inherited 0.58.
#
# The direction of the remaining error is deliberate. A hit below the floor is
# INVISIBLE -- the agent is told nothing has been written about it, which is
# the exact failure that cost this repo its whole document corpus on
# 2026-08-23 -- while a weak hit above the floor arrives with its score and its
# source and can be dismissed by the reader. Err toward the readable failure.
#
# Two alternatives were tried on nomic and rejected on measurement, and neither
# re-opens with bge. A z-score over the corpus is actively wrong: a nonsense
# query drags the mean down, so "banana helicopter tuesday" scored z=5.49,
# higher than two real questions. And task prefixes moved the margin by +0.001
# for the cost of a full re-embed.
MIN_SCORE = 0.65

# ---------------------------------------------------------------- ranking
#
# THREE RANKING SIGNALS, ALL RANKING-ONLY. None of them touches admission: the
# floor below is always tested against the RAW cosine, so nothing here can make
# a passage invisible, only reorder it. That split is the whole safety property
# of this section and every adjustment added later must preserve it.
#
# THE DEFECT THEY EXIST TO FIX, measured 2026-09-01 on the live 12,912-chunk
# index. `CLAUDE.md` names one document in bold as the only current source for
# how to apply a change. Asked "how do I restart the backend", search returned
# three CHATS discussing the procedure above the document that IS the
# procedure. Similarity alone cannot tell those apart: a conversation about a
# thing and the thing itself are about equally similar to a question about it.
# So authority and kind became signals, alongside the supersession penalty that
# was already here.
#
# ALL THREE ARE SIZED AGAINST ONE MEASUREMENT: the spread between the first and
# eighth hit of a real question on this index is 0.015 to 0.077, mean 0.038
# (measured 2026-08-23, re-checked 2026-09-01). An adjustment BIGGER than that
# band stops reordering near-ties and starts overriding relevance, which is the
# failure the supersession penalty already had at 0.05 and was resized down
# from. So every value below is a fraction of 0.038, and the ordering between
# two documents that are NOT in each other's competitive band never changes.

# How far a superseded doc drops in the RANKING (never in admission; see
# `search`). Sized against the measured spread, not picked round.
#
# RESIZED FOR BGE, 2026-08-23, same probe as MIN_SCORE above. It had to move:
# 0.05 was sized against nomic's 0.09-wide informative band, and bge's
# competitive band is much TIGHTER -- the spread between the first and eighth
# hit of a real question measured 0.015 to 0.077, mean 0.038 over eight
# questions. A 0.05 penalty is larger than that whole spread, so it stopped
# demoting a superseded chunk and started burying it, which is not what this is
# for.
#
# 0.03 is checked against both behaviours it has to preserve, on the live index:
#
#   - "is an app the same thing as a project": docs/workshop-v1/00-overview.md
#     (superseded) scores 0.788 RAW and beats app-is-the-entity-2026-08-20.md
#     at 0.787 -- the original defect, still there. Demoted to 0.758 it sits
#     behind the model of record and behind two other current chunks.
#   - "what did the v1 workshop design say about assistants and builders":
#     the same superseded doc scores 0.777 against a best current chunk of
#     0.745. Demoted to 0.747 it is still first, which is right, because that
#     question's answer IS the history. At 0.05 it would have fallen behind
#     three current chunks and the question would have gone unanswered.
_SUPERSEDED_PENALTY = 0.03

# What a document's declared lifecycle is worth in the ranking. Added 2026-09-01
# with the two positive values; `superseded` is the penalty above, restated here
# so one table is the whole story.
#
#   authoritative  the installation's own agent-rules file NAMES this document
#                  (`_authoritative_docs`). That is the strongest statement of
#                  authority available anywhere in the tree, because it is the
#                  operator writing down which document settles a question, and
#                  it is the same declaration the platform already reads for the
#                  agent's project-local rules. +0.05 is above the mean 0.038
#                  band ON PURPOSE and it is the only value here that is: the
#                  point is that a named source of record wins its competitive
#                  band outright, which is exactly what "the ONLY current
#                  source" is asking for. It still cannot promote a document
#                  that did not score in the band at all.
#   current        the document's own banner says it is current (`doc_status`).
#                  Weaker, because it is the document's opinion of itself, so it
#                  breaks ties and nothing more.
#
# There is deliberately no value for "accepted", "built", "shipped" or
# "decided", which are the other words `Status:` lines in this corpus carry.
# Those describe the state of a DECISION, not whether the document still
# describes the system; a plan can be fully built and completely superseded.
# Reading them as currency would be a judgment, and this module ranks on
# declarations rather than on judgments (same line `services/repetition.py`
# draws: counting and citing is indexing, concluding is not).
_STATUS_ADJUST = {
    "authoritative": +0.03,
    "current": +0.015,
    "": 0.0,
    "superseded": -_SUPERSEDED_PENALTY,
}

# A BONUS ONLY ACTS INSIDE THE COMPETITIVE BAND: a passage more than this far
# below the best hit for a query gets no bonus at all, however authoritative it
# is. Penalties are not gated; they apply wherever they land.
#
# This is the difference between "authority breaks a tie" and "authority
# overrides relevance", and the ungated version really did the second. Measured
# 2026-09-01 with a flat +0.05 and no gate, on queries with no strong match:
#
#   a question about one installed app -> app-is-the-entity-2026-08-20.md, raw
#      0.6529, promoted over the master spec for that subject at 0.6963
#   a question about an integration's auth -> final-plan-agent-generalize...,
#      raw 0.7431, promoted over the ticket that answers it at 0.7846
#   "what is the design system for the frontend" -> two chunks of the apply
#      procedure, raw 0.7087/0.7062, promoted over the note that answers it
#
# In each case a named document with nothing to say about the question beat the
# document that answered it, because the bonus was larger than the gap. Gated,
# all three fall out and the right answer comes back to the top.
#
# 0.05 rather than the 0.038 mean spread: at 0.038 the motivating query put its
# answer 0.0005 inside the band, which is not a margin, it is a coincidence.
_AUTHORITY_BAND = 0.05

# What KIND of answer a passage is, as a ranking signal. One distinction only.
#
# A chat is a record of DISCUSSING something; a doc, a note and a ticket are
# records of deciding it, being it, or committing to it. At equal similarity the
# authored artifact is the better answer to "how do I X", and the transcript is
# the better answer to "did we decide X" -- but the second case is self-
# correcting, because when the answer only exists in the transcript every
# competing hit is also a chat and a uniform offset among them changes nothing.
# The offset can only ever act at the boundary BETWEEN kinds, which is where the
# measured defect was.
#
# Half the mean competitive band, so it breaks near-ties and cannot outweigh a
# chat that is genuinely more relevant. There is deliberately no ordering among
# doc, note and ticket: no measurement here distinguishes them, and inventing a
# preference would be exactly the unmeasured judgment the paragraph above
# refuses.
_KIND_ADJUST = {"chat": -0.02}

# At most this many chunks from one document in a single answer. Adjacent
# chunks of a file are near-identical in embedding space, so without a cap one
# plan can take three of five slots (measured 2026-08-22).
_MAX_PER_REF = 2

# Candidates to consider per requested hit, so the per-ref cap has something to
# promote in place of what it skips.
_OVERSCAN = 6


@dataclass
class Chunk:
    kind: str     # note | doc | chat | ticket | vault
    ref: str      # repo-relative path, or a thread id
    title: str
    scope: str    # knowledge scope or chat context_key; "" where there is none
    ts: str       # ISO8601, best effort
    text: str
    # "" (nothing known) or "superseded". Set for docs from their own banner.
    # A default, so no collector has to change and an index written before this
    # field existed still loads (a missing key reads as "").
    status: str = ""

    def digest(self) -> str:
        """Identity of the CONTENT, which is what lets a rebuild skip work.

        Keyed on the text plus its source, so the same sentence in two files is
        embedded once per file rather than collapsing into one hit."""
        h = hashlib.sha1()
        h.update(f"{self.kind}\0{self.ref}\0".encode())
        h.update(self.text.encode())
        return h.hexdigest()


# ---------------------------------------------------------------- embedding

def _index_model() -> str:
    """The provider identity to tag this index with. A change here means the
    stored vectors are from a different model and must be discarded (load()
    enforces that)."""
    from services import embeddings
    return embeddings.provider_name()


def embed(texts: list[str]) -> np.ndarray:
    """L2-normalised embeddings, one row per text, via the local provider.

    Normalised here rather than at query time so the ranking is a plain dot
    product and the stored matrix is directly usable. Idempotent when the
    provider already returns unit vectors."""
    from services import embeddings
    if not texts:
        return np.zeros((0, embeddings.provider_dim()), dtype=np.float32)
    m = np.asarray(embeddings.embed_documents(list(texts)), dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    # A zero vector would divide to NaN and poison every later comparison.
    norms[norms == 0] = 1.0
    return m / norms


# How long a health check is trusted before it is repeated. Cheap enough to
# pay per search (one tiny embed, ~0.1s warm) but there is no reason to pay it
# on every one when nothing about the embedder changes between two searches a
# second apart.
_AVAILABILITY_TTL_S = float(os.environ.get("WORKSHOP_EMBED_PROBE_TTL_S", "30"))
_availability_cache: tuple[float, tuple[bool, str]] | None = None


def available() -> tuple[bool, str]:
    """Whether embedding can actually run right now, and why not when it can't.

    Cached for `_AVAILABILITY_TTL_S` so a burst of calls pays for one probe.
    Callers surface the reason instead of failing opaquely: an index that
    cannot build because a model was never pulled is a fixable, nameable
    condition, and so is one that cannot build because the runtime holding the
    model has no memory to run it in."""
    global _availability_cache
    now = time.monotonic()
    cached = _availability_cache
    if cached is not None and now - cached[0] < _AVAILABILITY_TTL_S:
        return cached[1]
    result = _probe_embedder()
    _availability_cache = (now, result)
    return result


def _probe_embedder() -> tuple[bool, str]:
    """The uncached check: whether the local provider can actually embed right
    now. It is a real (tiny) embed call, which for the in-process ONNX model
    also forces the one-time model load, so a missing dependency or a failed
    model download surfaces here as a nameable reason."""
    from services import embeddings
    return embeddings.available()


# ---------------------------------------------------------------- chunking

def _split(text: str) -> list[str]:
    """Paragraph-aware split to about CHUNK_CHARS.

    Splitting on blank lines first keeps a markdown list or a code block whole
    where it fits, which matters because the retrieved passage is what the model
    reads. An oversized single paragraph is hard-cut rather than dropped."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        while len(p) > CHUNK_CHARS:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - CHUNK_OVERLAP:]
        if not cur:
            cur = p
        elif len(cur) + len(p) + 2 <= CHUNK_CHARS:
            cur = f"{cur}\n\n{p}"
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- collection

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(paths.workshop_root()))
    except Exception:
        return str(p)


def _iso(p: Path) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        return ""


def _first_heading(body: str, fallback: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or fallback
        if s and not s.startswith("<!--"):
            return s[:80]
    return fallback


def collect_notes() -> list[Chunk]:
    """Every knowledge note, across every scope directory that exists.

    The docstring said this already and the code did not. It walked the shared
    root plus that directory's IMMEDIATE subdirectories, non-recursively, so
    `projects/*/knowledge`, `apps/*/knowledge`, `.runtime/agents/*/knowledge` and
    `roles/<role>/` were never reached: measured 2026-08-13, all 21 indexed note
    refs came from one directory. `knowledge.all_scope_dirs()` is now the single
    enumeration, and it is the inverse of `knowledge.scope_dir()` rather than a
    second guess at the layout.
    """
    from services import knowledge
    out: list[Chunk] = []
    try:
        roots = knowledge.all_scope_dirs()
    except Exception as e:
        log.warning("knowledge scopes unavailable: %s", e)
        roots = []
    # The walk is RECURSIVE, because the store became path-aware on 2026-08-28
    # and every note moved into a `system/` or `user/` subfolder; a one-level
    # glob left 195 of 196 notes out of the index entirely (measured 2026-08-29).
    #
    # But SCOPE DIRECTORIES NEST. The knowledge root is the workshop scope and
    # its `development`, `system`, `user` and `roles/reviewer` subdirectories all
    # sit inside it. A recursive walk from the parent therefore claims the
    # child's notes a SECOND time, under the WRONG scope -- which is not merely
    # duplication, because scope drives the reach mask, so a workshop-scope
    # search would reach development notes it must not see. Attribute each file
    # to the DEEPEST scope root that contains it.
    resolved = [(scope, d.resolve()) for scope, d in roots]

    def _nested_under(parent: Path) -> list[Path]:
        return [o for _, o in resolved if o != parent and parent in o.parents]

    for scope, d in roots:
        inner = _nested_under(d.resolve())
        for f in sorted(d.rglob("*.md")):
            fr = f.resolve()
            if any(n == fr or n in fr.parents for n in inner):
                continue  # belongs to a nested scope; that scope indexes it
            try:
                raw = f.read_text()
            except Exception:
                continue
            _, body = knowledge.split_author(raw)
            title = _first_heading(body, f.stem)
            for c in _split(body):
                out.append(Chunk("note", _rel(f), title, scope, _iso(f), c))
    return out


# A doc that says it has been replaced. MEASURED against the real corpus
# (2026-08-22) rather than guessed, and the discrimination below is the whole
# point of the pattern.
#
# THE TRAP: 34 docs in this tree contain the word "supersedes" in their opening
# because they REPLACE something else ("Supersedes the architecture in
# design.md", "Supersedes nothing"). Those are the CURRENT documents. A naive
# /supersede/ match demotes exactly the docs a search should surface first, and
# inverts the ranking it was added to fix. So this matches the PASSIVE forms
# only -- "superseded by", "superseded on <date> by", "Status: superseded",
# "historical reference" -- and never the active "supersedes".
# Judged PER LINE, because whether a sentence is this document's status or a
# remark about some other document is a property of the sentence.
_SUPERSEDED_LINE_RE = re.compile(
    r"superseded\s+(?:on\s+\S+\s+)?(?:for\s+reading\s+purposes\s+)?by"
    r"|status\s*:\s*\**\s*superseded"
    r"|superseded\s*\(historical\s+reference\)",
    re.IGNORECASE,
)

# Lines that LOOK like a supersession banner and are not one. Each was a real
# false positive on this corpus (2026-08-22), and each would have demoted a
# document a search should surface:
#
#   partly/partially  entity-model-2026-07-18.md says "PARTLY SUPERSEDED": one
#                     SECTION was replaced. CLAUDE.md still calls the document
#                     the authoritative model and says "start there". A doc
#                     that is partly superseded is current.
#   section/§         same shape, scoped to a part rather than the whole.
#   supersedes /      docs/decisions/0000-template.md matched its own FIELD
#   "/ superseded by" LABEL, "Supersedes / superseded by: (optional, ...)".
#                     A template is describing the field, not filling it in.
_NOT_SUPERSEDED_RE = re.compile(
    r"partly|partially|section|§|supersedes\s*/|/\s*superseded\s+by\s*:",
    re.IGNORECASE,
)

# A SUPERSESSION BANNER NAMES ITS REPLACEMENT. "Superseded by" with no document
# after it is not this document's fate being declared; it is the PHRASE being
# defined. Both index files in this corpus were demoted by that (measured
# 2026-09-01, and they are the two documents whose whole job is to say which
# other document to read):
#
#   INDEX-docs-2026-08-26.md            "SUPERSEDED BY <path>"   (a placeholder)
#   INDEX-redesign-docs-2026-08-26.md   "**SUPERSEDED BY x**"    (a gloss)
#
# Same family as the ADR-template trap above, and the general form of it: a
# banner cites, a definition does not. Only the "...by" wording is tested this
# way; "Status: superseded" is a complete declaration on its own and names
# nothing by design.
#
# The citation must be RIGHT AFTER the "by" -- a banner reads "superseded by
# `x.md`", so the name is the NEXT THING SAID and every real banner in this
# corpus puts it within a few characters. Searching the rest of the block
# instead finds any backtick anywhere in it, which is how the first version of
# this guard still demoted both index files: one of them says "marked
# `(judged)`" four sentences later, and it was inside a 60-character window as
# soon as the sentence between them was shortened.
_SUPERSESSION_TARGET_RE = re.compile(r"`[^`]+`|\[[^\]]+\]\(|[\w][\w./-]*\.md\b")
_TARGET_WINDOW = 24
# How far into a file a banner counts. Supersession is declared up top, next to
# the title; a "superseded by" deep in the body is usually a sentence ABOUT some
# other document's fate, not this one's.
_BANNER_LINES = 20

# A whole doc SET can be historical without every file repeating the banner:
# docs/workshop-v1 and docs/agent-chat-redesign are both declared historical in
# CLAUDE.md, and only their overview files carry the banner. Rather than name
# those paths in platform source (the rule this repo enforces elsewhere: a thing
# declares what it is and the platform reads the declaration), a directory
# declares itself by containing this file, and it applies to everything beneath.
_DIR_STATUS_FILE = ".index-status"

# The only statuses a chunk may carry. A marker file with anything else in it
# reads as "nothing declared" rather than as a status nothing knows how to rank:
# `_STATUS_ADJUST` is keyed on these and an unknown key would be a silent
# KeyError-shaped hole in the ranking.
_STATUSES = ("superseded", "authoritative", "current")


def _dir_status(d: Path, root: Path, cache: dict[Path, str]) -> str:
    """The status a directory declares over its subtree, nearest wins."""
    if d in cache:
        return cache[d]
    marker = d / _DIR_STATUS_FILE
    try:
        if marker.is_file():
            body = marker.read_text().strip()
            val = body.lower().split()[0] if body else ""
            if val not in _STATUSES:
                val = ""
            cache[d] = val
            return val
    except OSError:
        pass
    val = "" if d == root or d.parent == d else _dir_status(d.parent, root, cache)
    cache[d] = val
    return val


# A `Status:` line and the first words after it. Nearly every document in this
# corpus opens with one, and the words it carries are the document's own account
# of whether it still describes the system. Only two vocabularies are read; the
# rest ("design", "proposal", "plan", "research", "accepted", "built", ...) mean
# the document declared something this module does not rank on, which is not the
# same as declaring nothing and is treated the same way.
_STATUS_FIELD_RE = re.compile(r"\bstatus\s*:\s*(.{0,80})", re.IGNORECASE)

# Retired. Read only at the START of the status value, so "Status: research ...
# Historical note (do not follow ...)" -- a research doc remarking that one
# section is history -- is not read as the whole document being history.
_RETIRED_STATUS_RE = re.compile(
    r"^(historical|obsolete|retired|archived|deprecated|superseded)\b",
    re.IGNORECASE,
)

# Current. Deliberately short; see `_STATUS_ADJUST` for why "accepted" and
# "built" are not in it.
_CURRENT_STATUS_RE = re.compile(
    r"^(current|canonical|authoritative"
    r"|(?:model|plan|source)\s+of\s+record)\b",
    re.IGNORECASE,
)

# Emphasis and blockquote punctuation is stripped before the two patterns above
# are applied, because a banner is written as `> **Status:** CURRENT` at least
# as often as `Status: current` and the marks are decoration, not content.
_DECORATION = str.maketrans("", "", "*_>#`")


def _status_field(flat: str) -> str:
    """"superseded" | "current" | "" from a flattened banner block's `Status:`
    field. Every match in the block is considered, retired winning over current,
    so a doc that says "Status: design. Status: superseded by X" reads as
    retired rather than as whichever came first."""
    plain = flat.translate(_DECORATION)
    found = ""
    for m in _STATUS_FIELD_RE.finditer(plain):
        value = m.group(1).strip()
        if _RETIRED_STATUS_RE.match(value):
            return "superseded"
        if _CURRENT_STATUS_RE.match(value):
            found = "current"
    return found


def doc_status(body: str) -> str:
    """What the doc's own banner declares: "superseded", "current", or "".

    Judged per BLOCK (a run of non-blank lines), which is the unit a banner is
    actually written in. Matching the whole header as one blob let a qualifier
    from an unrelated paragraph suppress a real banner, and matching one line at
    a time split banners that wrap:

        > **PARTLY SUPERSEDED, 2026-08-20.** The App-vs-Project split this
        > document defines ... is superseded by app-is-the-entity...

    "PARTLY" and "is superseded by" are three lines apart in
    entity-model-2026-07-18.md, so per-line demoted the document CLAUDE.md calls
    the authoritative model and says to start from. A block keeps a claim and
    its qualifier together, which is the only unit where the question has an
    answer.
    """
    found = ""
    for block in re.split(r"\n\s*\n", "\n".join(body.splitlines()[:_BANNER_LINES])):
        flat = " ".join(block.split())
        m = _SUPERSEDED_LINE_RE.search(flat)
        if m and not _NOT_SUPERSEDED_RE.search(flat):
            names_a_target = _SUPERSESSION_TARGET_RE.search(
                flat[m.end():m.end() + _TARGET_WINDOW])
            if not m.group(0).rstrip().lower().endswith("by") or names_a_target:
                return "superseded"
        # The `Status:` field is read second and can only ever CONFIRM a
        # retirement the prose form missed. A block that says both is retired:
        # a supersession banner outranks a stale currency claim, because the
        # banner is the later edit by construction.
        field = _status_field(flat)
        if field == "superseded":
            return "superseded"
        if field == "current":
            found = "current"
    return found


# ------------------------------------------------------- declared authority
#
# WHICH DOCUMENT SETTLES A QUESTION IS A DECLARATION, AND THE DECLARATION ALREADY
# EXISTS. Every checkout keeps agent-instruction files, and this one's names a
# document in bold as the only current source for how to apply a change. That
# sentence was already the answer to the query that motivated this; nothing read
# it. `provider.persona()` reads the same files for the agent's project-local
# rules, so this is not a new convention, it is a second reader of an existing
# one.
#
# Rejected: a hand-kept list of authoritative paths in this module. It would be
# a fact about one checkout living in platform source -- the thing this repo
# forbids everywhere else -- and it would go stale the first time a doc was
# renamed without anyone editing this file. A rules file that names a document
# it no longer has is a broken link the operator sees; a stale list here is
# invisible.
#
# Rejected: treating every markdown link in the corpus as a vote (PageRank over
# docs). The corpus is 282 documents of which about half link each other for
# argument rather than for authority ("supersedes X", "corrects the banner of
# Y"), so the highest in-degree nodes measured here are the SUPERSEDED ones --
# a doc gets cited most when it is being retired. Link-counting would invert the
# exact ranking this is meant to fix.

# Any `.md` path or bare filename, as written in prose or in a markdown link.
_DOC_MENTION_RE = re.compile(r"[\w][\w./-]*\.md\b")


def _rules_files() -> list[Path]:
    """The installation's agent-instruction files: one per directory, the repo
    root and its immediate subdirectories.

    The FILENAMES are not redefined here. `chat_context._RULES_FILES` is the
    single list of what an agent-instructions file is called and in what
    preference order, and it is imported rather than copied so the two readers
    cannot drift -- the same reason `stat_fingerprint` is a public seam."""
    from services.chat_context import _RULES_FILES as names

    root = paths.workshop_root()
    try:
        dirs = [root] + sorted(p for p in root.iterdir()
                               if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    out: list[Path] = []
    for d in dirs:
        for n in names:
            f = d / n
            try:
                if f.is_file() and f.read_text(errors="ignore").strip():
                    out.append(f)
                    break
            except OSError:
                continue
    return out


def _authoritative_docs() -> set[str]:
    """Repo-relative paths of the docs the agent-instruction files name.

    A bare filename resolves only when exactly one document in the tree carries
    it. An ambiguous name is dropped rather than guessed: promoting the wrong
    one of two same-named files is worse than promoting neither, and this is a
    ranking bonus, not a lookup -- nothing is lost by declining."""
    root = paths.workshop_root()
    docs_root = root / "docs"
    try:
        rels = {_rel(p) for p in docs_root.rglob("*.md")}
    except OSError:
        return set()
    by_name: dict[str, list[str]] = {}
    for rel in rels:
        by_name.setdefault(Path(rel).name, []).append(rel)

    found: set[str] = set()
    for f in _rules_files():
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in _DOC_MENTION_RE.finditer(text):
            token = m.group(0)
            if token in rels:
                found.add(token)
                continue
            same = by_name.get(Path(token).name)
            if same and len(same) == 1:
                found.add(same[0])
    return found


def collect_docs() -> list[Chunk]:
    root = paths.workshop_root() / "docs"
    out: list[Chunk] = []
    dir_cache: dict[Path, str] = {}
    named = _authoritative_docs()
    for f in sorted(root.rglob("*.md")):
        try:
            body = f.read_text()
        except Exception:
            continue
        title = _first_heading(body, f.stem)
        # The file's own banner wins; otherwise inherit whatever its directory
        # declares, so a historical doc SET is marked once at its root.
        status = doc_status(body) or _dir_status(f.parent, root, dir_cache)
        # RETIREMENT BEATS BEING NAMED. An instructions file names documents for
        # several reasons, and one of them is to say "this set is historical,
        # do not treat it as current". A document that declares itself retired
        # stays retired however prominently it is cited, so being named can only
        # ever upgrade a doc that declared nothing or declared itself current.
        if status != "superseded" and _rel(f) in named:
            status = "authoritative"
        for c in _split(body):
            out.append(Chunk("doc", _rel(f), title, "", _iso(f), c, status))
    return out


def collect_chats() -> list[Chunk]:
    """Past conversations, grouped into passages in log order.

    This is the corpus that makes distillation unnecessary: the thing a summary
    was standing in for is right here, verbatim and attributable."""
    from services import chat_log
    out: list[Chunk] = []
    try:
        ids = chat_log.list_thread_ids()
    except Exception as e:
        log.warning("chat logs unavailable: %s", e)
        return out
    for tid in ids:
        meta = {}
        try:
            meta = chat_log.read_meta(tid) or {}
        except Exception:
            pass
        title = meta.get("title") or "Untitled chat"
        scope = meta.get("context_key") or meta.get("scope") or ""
        try:
            events = chat_log.read_events(tid)
        except Exception:
            continue
        buf, buf_ts = "", ""
        for ev in events:
            if ev.get("kind") != "message":
                continue
            content = (ev.get("content") or "").strip()
            if not content:
                continue
            who = "Operator" if ev.get("role") == "user" else "Assistant"
            line = f"{who}: {content}"
            ts = ev.get("ts") or ""
            # A MESSAGE LONGER THAN A CHUNK IS SPLIT, NOT TRUNCATED. This
            # collector used to flush the buffer and then keep only
            # `line[:CHUNK_CHARS]`, dropping the rest of the message on the
            # floor -- measured 2026-08-23 at 7,094,963 chat characters on
            # disk against 1,357,375 in the index (19.1%), 779 messages cut,
            # the worst losing 572,856 characters. What it ate was the LONG
            # messages, which are the designs, the decisions and the
            # operator's direction, while `routes/chat.py` told the model
            # compacted messages "are still in the transcript, and they are
            # searchable". `_split` is the same paragraph-aware splitter
            # every other collector uses (`collect_tickets`'s docstring:
            # "split like every other collector"), and it BOUNDS each piece
            # at CHUNK_CHARS -- which matters twice over, because the 512-
            # token window of the embedding model made the 27 pre-existing
            # oversized chunks (up to 19,870 characters) semantically dead
            # past their first ~2,000.
            if len(line) > CHUNK_CHARS:
                if buf:
                    out.append(Chunk("chat", tid, title, scope, buf_ts, buf))
                    buf, buf_ts = "", ""
                for piece in _split(line):
                    out.append(Chunk("chat", tid, title, scope, ts, piece))
                continue
            if len(buf) + len(line) + 2 > CHUNK_CHARS and buf:
                out.append(Chunk("chat", tid, title, scope, buf_ts, buf))
                buf, buf_ts = "", ""
            buf = f"{buf}\n\n{line}" if buf else line
            if not buf_ts:
                buf_ts = ts
        if buf:
            out.append(Chunk("chat", tid, title, scope, buf_ts, buf))
    return out


def collect_markdown_tree(kind: str, root) -> list[Chunk]:
    """Chunk every markdown file under `root` as `kind`. The platform-owned half
    of a corpus collector: an app that has a folder of notes declares WHERE it is
    (`AppSpec.index_collectors`) and gets the chunking, titling, hidden-file skip
    and relative-ref rules from here rather than reimplementing them.

    This was `collect_vault`, which named one app's corpus in platform code and
    imported that app's module to find it.
    """
    out: list[Chunk] = []
    if root is None:
        return out
    for f in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in f.parts):
            continue
        try:
            body = f.read_text()
        except Exception:
            continue
        try:
            ref = str(f.relative_to(root))
        except Exception:
            ref = str(f)
        for c in _split(body):
            out.append(Chunk(kind, ref, _first_heading(body, f.stem), "", _iso(f), c))
    return out


def collect_tickets() -> list[Chunk]:
    """Every ticket, across every scope that has a task store.

    Enumeration mirrors `routes.tasks_all.scope_roots()`: the sweep that
    already covers development, workshop, every project, every registry
    assistant scope and every build agent, deduped by resolved tree so a build
    agent sharing a project's tree is not double-counted. It is the closest
    thing to `knowledge.all_scope_dirs()` for tickets (no bulk enumerator
    existed before it; see the design doc sec 8 and Phase 2's own note about
    the gap). Imported lazily, matching `services/refs.py`'s existing
    `from routes import tasks_all` (routes -> services is the normal
    direction; this one call goes the other way on purpose, same as refs.py's
    ticket resolver, because the sweep genuinely lives with the route).

    Every scope `scope_roots()` returns (`development`, `workshop`,
    `project:<slug>`, `agent:<slug>`) is a real scope_tree key and needs no
    translation. It used to also emit `build:<slug>`, which `reach()` and
    `memory_policy` had never heard of and which had to be translated here; that
    key is deleted, so the translation is too.

    A ticket's `ref` is its bare id (matching `refs.ticket_ref` / the
    `workshop:ticket/<id>` format `services/refs.py` already resolves), and its
    text is title + body, split like every other collector."""
    from routes import tasks_all
    from services import tasks
    out: list[Chunk] = []
    try:
        roots = tasks_all.scope_roots()
    except Exception as e:
        log.warning("ticket scopes unavailable: %s", e)
        return out
    for scope, root in roots:
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            tid = t.get("id")
            if not tid:
                continue
            title = t.get("title") or tid
            text = f"{title}\n\n{t.get('body') or ''}".strip()
            ts = t.get("updated") or t.get("created") or ""
            for c in _split(text):
                out.append(Chunk("ticket", tid, title, scope, ts, c))
    return out


# The PLATFORM's collectors. An app's are unioned in by collectors(); tests that
# monkeypatch this dict still control the platform half exactly as before.
_COLLECTORS = {
    "note": collect_notes,
    "doc": collect_docs,
    "chat": collect_chats,
    "ticket": collect_tickets,
}


# ---------------------------------------------------------------- storage

def index_dir() -> Path:
    """Per owner, exactly like the chat log, because it indexes the chat log."""
    from services import ownership
    d = ownership.user_runtime_dir() / "semantic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path() -> Path:
    return index_dir() / "index.meta.jsonl"


def _stale_path() -> Path:
    return index_dir() / "index.stale"


def _fingerprints_path() -> Path:
    return index_dir() / "index.fingerprints.json"


def _vecs_path() -> Path:
    return index_dir() / "index.vecs.npy"


def _model_path() -> Path:
    return index_dir() / "index.model"


def load() -> tuple[list[dict], np.ndarray]:
    """The index as it stands, or empty. Never raises: a corrupt or half-written
    index must degrade to "no results", not break the tool that reads it.

    A model change makes every stored vector meaningless (two models' vectors do
    not share a space), so an index tagged with a different provider than the one
    running now, OR an untagged legacy index from the Ollama era, is treated as
    empty. That forces a one-time full rebuild onto the new model rather than
    ever mixing dimensions or spaces."""
    mp, vp = _meta_path(), _vecs_path()
    if not mp.exists() or not vp.exists():
        return [], np.zeros((0, 0), dtype=np.float32)
    try:
        stored_model = _model_path().read_text().strip() if _model_path().exists() else ""
    except Exception:
        stored_model = ""
    if stored_model != _index_model():
        log.info("semantic index model changed (%r -> %r); rebuilding from scratch",
                 stored_model or "(untagged)", _index_model())
        return [], np.zeros((0, 0), dtype=np.float32)
    try:
        # `split("\n")`, NEVER `splitlines()`. Python also splits on U+2028,
        # U+2029, U+0085, \v, \f and \x1c-\x1e, and JSON escapes none of them.
        # One chunk carrying a U+2028 therefore cut a valid record in half and
        # the whole index read as corrupt. JSONL is newline-delimited, exactly.
        meta = [json.loads(l) for l in mp.read_text().split("\n") if l.strip()]
        vecs = np.load(vp)
    except Exception as e:
        log.warning("semantic index unreadable, treating as empty: %s", e)
        return [], np.zeros((0, 0), dtype=np.float32)
    if len(meta) != vecs.shape[0]:
        log.warning("semantic index rows disagree (%d meta, %d vecs); treating as empty",
                    len(meta), vecs.shape[0])
        return [], np.zeros((0, 0), dtype=np.float32)
    return meta, vecs


def _save(meta: list[dict], vecs: np.ndarray) -> None:
    """Written to a temp file and renamed, so a reader never sees a half-index."""
    d = index_dir()
    mp, vp = _meta_path(), _vecs_path()
    tmp_m, tmp_v = d / "index.meta.tmp", d / "index.vecs.tmp.npy"
    tmp_m.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in meta))
    np.save(tmp_v, vecs)
    os.replace(tmp_m, mp)
    os.replace(tmp_v, vp)
    # Tag the index with the provider that produced these vectors, so load()
    # discards them if the model ever changes. Written last: a reader that sees
    # the vectors already sees a matching (or, briefly, an older) tag, never a
    # newer tag over old vectors.
    _model_path().write_text(_index_model())


# -------------------------------------------------------------- staleness
#
# THE INDEX HEALS ITSELF, AND IT IS NOT ON A SCHEDULE. A note written today was
# unfindable until somebody remembered to run the build by hand, which is the
# same as unfindable. The fix is two small functions and no new process: a write
# leaves a marker, and the next search that sees the marker rebuilds first.
#
# NOT A CRON ENTRY. The operator turned cron off deliberately
# (infra/cron/workshop.crontab), and a scheduler is the wrong shape anyway: it
# rebuilds when nothing changed and lags when something did. This costs nothing
# when nothing was written and is exact when something was.
#
# The rebuild is INCREMENTAL by content digest (see `build`), so the common case
# after one note is a handful of new chunks embedded locally, not a full re-embed
# of the corpus.
#
# A MARKER FILE, not a module global. The writer is the backend process and the
# reader is very often the MCP server, which is a separate stdio subprocess
# spawned per turn. An in-memory flag would be invisible across that boundary,
# which is the boundary that matters. It lives in the owner's index dir, so it is
# per-owner exactly as the index is.
#
# THE MARKER IS ONE OF TWO SIGNALS. It covers every kind with a store that can
# actually stamp it on every write. `doc` cannot be reached that way -- see the
# "ON-DISK FINGERPRINTING" block further down, by `_fs_is_stale` -- so
# `is_stale()` is the OR of this marker and that on-disk check.

def mark_stale(reason: str = "") -> None:
    """Record that the corpus changed under the index. Never raises.

    Best-effort on purpose: this is called from the tail of a write that has
    already succeeded, and failing a note write because a marker could not be
    touched would be a strictly worse outcome than a stale index.

    DEBOUNCED: skips the write entirely when the marker is already down. A
    chat message, a ticket heartbeat, and a note edit all funnel through this
    on their hot write paths, so between one rebuild and the next this can be
    called many times for one meaningful staleness -- the first caller's write
    is the one that matters, and every caller after it until the marker is
    cleared is a no-op `exists()` check rather than a second file write. Costs
    nothing when nothing was stale (the common case, one `Path.exists()`), and
    the reason recorded on disk is therefore the FIRST cause since the last
    rebuild rather than the most recent one -- nothing reads it back, it is
    debugging text for a human running the CLI.
    """
    try:
        if _stale_path().exists():
            return
        _stale_path().write_text((reason or "changed").strip() + "\n")
    except Exception:
        log.debug("could not mark the semantic index stale", exc_info=True)


def is_stale() -> bool:
    """True when EITHER the explicit marker above says so, OR the on-disk
    fingerprint check below does. See that check's own comment for why a
    second, independent signal exists at all."""
    try:
        if _stale_path().exists():
            return True
    except Exception:
        pass
    return _fs_is_stale()


def _clear_stale() -> None:
    try:
        _stale_path().unlink(missing_ok=True)
    except Exception:
        log.debug("could not clear the semantic index stale marker", exc_info=True)


# ---------------------------------------------------- on-disk fingerprinting
#
# WHY THIS EXISTS ALONGSIDE THE MARKER ABOVE. `mark_stale` is a write-path
# signal: whoever accepts a write leaves a marker so the next search rebuilds
# first. That is complete for note (services/knowledge.py), chat
# (services/chat_log.py) and ticket (services/tasks.py) -- every note, chat
# message and ticket is written through exactly the one store that stamps the
# marker, with no other way in. `doc` has no such choke point:
# routes/docs_index.py marks the index when the in-app editor writes one, but
# the overwhelming majority of docs are written straight to `docs/*.md` by an
# editor or an agent's filesystem tools, none of which import this module or
# know the marker exists. That path leaves nothing behind, so the doc corpus
# in the index could drift from what is actually on disk indefinitely, healed
# only by accident the next time an unrelated note/chat/ticket write happens
# to flip the shared marker.
#
# The fix is a second, independent staleness signal that does not depend on
# being told: a cheap fingerprint of the doc TREE (which files exist, and
# each one's size and mtime -- never a file's body, see `_stat_fingerprint`),
# compared against the fingerprint the last successful build actually
# observed. A mismatch means the disk moved since the index was built, full
# stop, regardless of which tool moved it.
#
# ONLY `doc` GETS THIS. `note`, `chat` and `ticket` are also read from
# storage on every build, but each already has a reliable write-path signal
# (above) covering every way into that store, so a second signal would only
# add a stat walk with nothing new to catch. `vault` has the same filesystem
# gap `doc` does (no writer marks anything), but is deliberately left out: it
# is opt-in only (an app-declared kind outside index_collectors_default) and is
# never part of a self-heal rebuild
# unless a caller already asked for it by name, so the failure mode this
# closes -- the DEFAULT corpus silently drifting -- does not apply to it. If
# vault ever needs this too, add `"vault": _vault_fingerprint` to
# `_FS_FINGERPRINTERS` below; nothing else changes.
#
# NO BASELINE MEANS SILENT, ON PURPOSE. `is_stale()` runs on every search, so
# a kind with no prior fingerprint (never built, or built before this
# existed) must not manufacture staleness out of nothing -- that is what the
# explicit marker and the ordinary first-build path are for. Once one
# successful build records a baseline for a kind, drift from THAT baseline is
# what gets caught, forever, with no further write-path wiring required.

def _stat_fingerprint(root: Path, pattern: str) -> str:
    """A cheap identity for a directory tree of files: which files exist,
    and each one's size and mtime. NEVER a file's body -- a glob plus
    `stat()` per match is O(number of files) and runs on the `is_stale()`
    path (so on every search); reading every file to confirm its body
    actually changed would be O(total bytes) and defeat the point of a check
    meant to be cheap enough to pay unconditionally.

    Sorted by relative path first so the result does not depend on directory
    iteration order, which the filesystem gives no guarantee about."""
    h = hashlib.sha1()
    try:
        entries = sorted(root.rglob(pattern))
    except OSError:
        return ""
    for f in entries:
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(_rel(f).encode())
        h.update(f"\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def stat_fingerprint(root: Path, pattern: str) -> str:
    """Public seam for `_stat_fingerprint`, so another store over the same
    files can share one definition of "this tree changed" instead of guessing
    at a second one that drifts."""
    return _stat_fingerprint(root, pattern)


# Bumped whenever the STATUS a doc chunk is stored with can change for an
# unchanged file: new vocabulary, a fixed false positive, a new source of
# authority. It rides in the doc fingerprint, so an index built by an older
# build re-collects once and rewrites its meta.
#
# THIS IS NOT OPTIONAL BOOKKEEPING, it is the difference between the change
# taking effect and appearing to do nothing. Statuses are stored per chunk at
# BUILD time and read at SEARCH time, so a running process with the previous
# module loaded rebuilds the index and silently strips them, and the next
# search ranks as if the change had never shipped. That happened on 2026-09-01,
# minutes after the change landed, and the ranking measured as unchanged.
#
# Cheap: chunk identity is content, not status, so every vector is reused. The
# whole 12,918-chunk re-collect measured 2.4s.
#
# THE ACCEPTED COST, stated because it looks like a bug while it lasts. Only the
# long-lived process rebuilds (see `owns_maintenance`), and until it restarts
# onto this code it computes the fingerprint WITHOUT the version, so the two
# disagree and each rebuild flips the baseline back. The index re-collects on
# every maintenance pass for that window: 2.4s, zero embeddings, no wrong
# answers, and it ends at the next restart. The alternative -- keeping the
# version in its own key, which older code preserves untouched -- does not flap,
# but it lets an older rebuild strip the statuses and then assert the index is
# current, which is the failure this constant exists to make impossible.
_RANK_SCHEMA = 2


def _doc_fingerprint() -> str:
    """Drift in the doc corpus, INCLUDING the agent-instruction files.

    Those files are not indexed, but they decide which documents are
    authoritative (`_authoritative_docs`), so editing one changes the ranking of
    documents that did not themselves change. Without them in the fingerprint
    that edit would take effect only at the next unrelated doc write, which is
    the kind of "it did not take" that costs a session."""
    h = hashlib.sha1()
    h.update(f"rank-schema={_RANK_SCHEMA}\n".encode())
    h.update(_stat_fingerprint(paths.workshop_root() / "docs", "*.md").encode())
    for f in _rules_files():
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{_rel(f)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


_FS_FINGERPRINTERS = {"doc": _doc_fingerprint}


def _load_fingerprints() -> dict:
    try:
        return json.loads(_fingerprints_path().read_text())
    except Exception:
        return {}


def _save_fingerprints(fps: dict) -> None:
    """Written to a temp file and renamed, same as `_save`, so a reader never
    sees a half-written file."""
    try:
        d = index_dir()
        tmp = d / "index.fingerprints.tmp.json"
        tmp.write_text(json.dumps(fps))
        os.replace(tmp, _fingerprints_path())
    except Exception:
        log.debug("could not persist semantic index fingerprints", exc_info=True)


def _refresh_fingerprints(kinds) -> None:
    """Record the on-disk state this build actually just collected, per
    fingerprinted kind. Only for kinds THIS call covered: a build restricted
    to e.g. `("chat",)` never re-ran `collect_docs`, so it must not overwrite
    the doc baseline as if nothing had changed since -- that baseline is only
    trustworthy right after a build that actually looked at the doc tree."""
    to_update = [k for k in kinds if k in _FS_FINGERPRINTERS]
    if not to_update:
        return
    fps = _load_fingerprints()
    for k in to_update:
        fps[k] = _FS_FINGERPRINTERS[k]()
    _save_fingerprints(fps)


def _fs_is_stale() -> bool:
    """The on-disk half of `is_stale()`. Best-effort like the marker: a
    fingerprint that cannot be computed must not make every search look stale
    forever, so a failure here reads as "no drift detected", not "stale"."""
    try:
        saved = _load_fingerprints()
    except Exception:
        return False
    for kind, fp_fn in _FS_FINGERPRINTERS.items():
        baseline = saved.get(kind)
        if baseline is None:
            continue  # never built; the marker and the first build own this
        try:
            if fp_fn() != baseline:
                return True
        except Exception:
            log.debug("could not compute the %s fingerprint; skipping", kind,
                      exc_info=True)
    return False


def rebuild_kinds() -> tuple[str, ...]:
    """Which kinds a self-healing rebuild must cover.

    NOT the caller's `kinds` filter, and this is the trap. `build` REPLACES the
    index with exactly what it collected, so rebuilding with the kinds of the
    query in hand (`kinds=("note",)` from a note-only search) would delete every
    doc and chat from the index as a side effect of one search. It is the
    defaults, plus anything already indexed — so an opted-in `vault` survives a
    rebuild it did not ask for.
    """
    all_kinds = kinds()
    dflt = default_kinds()
    present = {m.get("kind") for m in load()[0] if m.get("kind") in all_kinds}
    return tuple(k for k in all_kinds if k in present or k in dflt)


def rebuild_if_stale() -> dict | None:
    """Rebuild when a write has marked the index stale. Returns the build result,
    or None when there was nothing to do.

    The marker is cleared by `build` on success only. A rebuild that fails
    because the embedder is down leaves the marker in place, so the next search
    tries again instead of silently accepting a stale index forever.
    """
    if not is_stale():
        return None
    log.info("semantic index is stale; rebuilding before this search")
    res = build(rebuild_kinds())
    if not res.get("ok"):
        log.warning("stale semantic index could not be rebuilt: %s", res.get("error"))
    return res


# ------------------------------------------------------ background rebuild
#
# `rebuild_if_stale` above runs INSIDE the caller (still used directly by the
# CLI and a couple of tests that want a synchronous rebuild). `search()` must
# NOT do that: on CPU a cold build is about 8 minutes, and a search is a
# user-facing request. This is the same self-healing trigger, moved off the
# request path into a single background thread per owner's index, so a search
# that arrives mid-rebuild answers from what is on disk right now instead of
# blocking on it or returning nothing.
#
# Keyed by index_dir() rather than one global thread, so two owners rebuilding
# their own (separate) indexes never contend on each other's lock; single-
# flight is scoped to "the index this call would rebuild," which is the thing
# that actually cannot run twice at once.
_rebuild_state_lock = threading.Lock()
_rebuild_threads: dict[str, threading.Thread] = {}

# WHICH PROCESS OWNS THE REBUILD. Off by default, and the backend turns it on
# for itself in its lifespan.
#
# The rebuild used to start in whatever process happened to call `search()`
# first, and the process that calls `search()` is almost always a per-turn MCP
# server (backend/mcp/workshop_query_server.py) — a short-lived child of the
# chat turn's `claude`. So an 8-minute, multi-gigabyte batch job ran inside a
# turn's process group and was charged to that turn.
#
# Measured 2026-08-22: a chat turn called `search`, found the index empty,
# started the rebuild in-process, and was KILLED by the turn memory ceiling at
# 9.2 GB for the crime of searching. fastembed runs ONNX Runtime in-process, and
# its arena over the whole corpus is the balloon; a healthy query server is
# 11 MB. The index is platform state and rebuilding it is a platform background
# job, so it belongs in the long-lived process that already runs the other
# maintenance loops, not in a turn's child.
#
# A process that does not own maintenance still SEES that a rebuild is needed
# and says so; it just does not do the work. That is why `search` keeps
# answering from what is on disk with `rebuilding` set, which is what it did
# before.
_owns_maintenance = False


def enable_maintenance() -> None:
    """Declare THIS process the owner of index rebuilds. Called once from the
    backend lifespan. Idempotent."""
    global _owns_maintenance
    _owns_maintenance = True


def owns_maintenance() -> bool:
    return _owns_maintenance


async def maintenance_loop(interval: float = 300.0) -> None:
    """Keep the index fresh from the long-lived process, forever.

    This is the whole reason a rebuild no longer has to happen on the request
    path: something else is already keeping it current, so a search that finds
    a stale index degrades for a few minutes instead of paying for the rebuild.

    A failed pass is logged and the loop continues; a rebuild that fails leaves
    the stale marker in place, so the next pass retries rather than accepting a
    stale index forever.
    """
    enable_maintenance()
    pin_owner()
    log.info("semantic index maintenance loop started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(_maintenance_pass)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("semantic index maintenance pass failed")
        await asyncio.sleep(interval)


def _maintenance_pass() -> None:
    pin_owner()
    res = rebuild_if_stale()
    if res is not None:
        log.info("semantic index rebuilt: %s", res)
    # The repetition store rides this timer rather than starting a second loop.
    # It reads the same chat log off the same stat fingerprint, so a pass that
    # found the log unchanged has already proved this store fresh too, and one
    # that rebuilt the index is exactly when this one is stale.
    try:
        from services import repetition
        repetition.maintenance_pass()
    except Exception:
        log.warning("repetition maintenance pass failed", exc_info=True)


def _background_rebuild_if_stale() -> bool:
    """Kick off a background rebuild if the index is stale and none is already
    running for this owner. Returns whether a rebuild is in flight (just
    started, or already running) so the caller can say so. Never blocks."""
    if not is_stale():
        return False
    if not _owns_maintenance:
        # Not this process's job. Report that the index needs catching up so the
        # caller still labels its hits "from what is on disk right now", and
        # leave the work to whoever owns maintenance. See `_owns_maintenance`:
        # doing it here is what got a chat turn killed at a 9.2 GB peak.
        return True
    key = str(index_dir())
    with _rebuild_state_lock:
        existing = _rebuild_threads.get(key)
        if existing is not None and existing.is_alive():
            return True

        from services import ownership
        owner_id = ownership.owner_or_none()

        def _run(oid=owner_id) -> None:
            # A background thread does not inherit the calling thread's
            # ownership contextvar, so the owner captured above (in the
            # thread that already had it pinned) is set explicitly here.
            # `pin_owner()` is the same operator fallback every other
            # background pass in this module uses when there was no owner to
            # capture (see its own docstring).
            if oid is not None:
                ownership.set_current_owner(oid)
            else:
                pin_owner()
            try:
                res = build(rebuild_kinds())
                if not res.get("ok"):
                    log.warning("background semantic index rebuild failed: %s",
                                res.get("error"))
            except Exception:
                log.warning("background semantic index rebuild crashed", exc_info=True)

        t = threading.Thread(target=_run, name="semantic-index-rebuild", daemon=True)
        _rebuild_threads[key] = t
        t.start()
        return True


# ---------------------------------------------------------------- build

def build(kinds: tuple[str, ...] | list[str] | None = None,
          *, log_fn=None) -> dict:
    """Rebuild the index, re-embedding only what is new.

    Identity is the content digest, so this is incremental without tracking
    mtimes: an unchanged note keeps its vector, an edited one gets a new digest
    and is re-embedded, and a deleted one simply stops being collected. That
    also makes the operation idempotent, which a mtime cache is not."""
    # Resolved at call time, not as a default argument: which kinds are default
    # now depends on which apps are installed.
    if kinds is None:
        kinds = default_kinds()
    say = log_fn or (lambda s: None)
    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}

    chunks: list[Chunk] = []
    for k in kinds:
        c = collectors()[k]()
        say(f"collected {len(c)} chunks from {k}")
        chunks.extend(c)

    old_meta, old_vecs = load()
    cache = {m["digest"]: i for i, m in enumerate(old_meta) if "digest" in m}

    meta: list[dict] = []
    rows: list[np.ndarray | None] = []
    todo: list[int] = []
    for ch in chunks:
        dg = ch.digest()
        row = {**asdict(ch), "digest": dg}
        meta.append(row)
        hit = cache.get(dg)
        if hit is not None and hit < old_vecs.shape[0]:
            rows.append(old_vecs[hit])
        else:
            rows.append(None)
            todo.append(len(rows) - 1)

    say(f"{len(chunks)} chunks, {len(todo)} to embed, {len(chunks) - len(todo)} reused")
    if todo:
        vecs = embed([meta[i]["text"] for i in todo])
        for n, i in enumerate(todo):
            rows[i] = vecs[n]

    # Cleared only once the index is actually on disk, and only by a build that
    # covered every default kind: `build(("chat",))` re-collects no notes, so
    # letting it clear a marker a note write left would lose that note until the
    # next unrelated write. An `ok: False` return above (embedder down) never
    # reaches here at all, so the marker survives and the next search retries.
    covered = set(default_kinds()).issubset(set(kinds))

    if not rows:
        _save([], np.zeros((0, 0), dtype=np.float32))
        if covered:
            _clear_stale()
        _refresh_fingerprints(kinds)
        return {"ok": True, "chunks": 0, "embedded": 0, "reused": 0}

    matrix = np.vstack([r for r in rows if r is not None]).astype(np.float32)
    _save(meta, matrix)
    if covered:
        _clear_stale()
    _refresh_fingerprints(kinds)
    return {"ok": True, "chunks": len(meta), "embedded": len(todo),
            "reused": len(meta) - len(todo), "dim": int(matrix.shape[1])}


# ------------------------------------------------------------ lexical fallback
#
# BM25 over the SAME chunk corpus the vector index embeds, not a second store.
# `index.meta.jsonl` already carries every chunk's `text`, so lexical search
# needs no embedder at all: even a stale index still answers. Chosen over the
# simpler term-count scorer in `knowledge._plaintext_search` (raw
# `sum(hay.count(t))`, no IDF, no length normalisation) because that scorer's
# actual failure was measured, not theoretical: a query of pure stopwords
# returned the three largest files in the scope, because longer documents
# contain more of every common word and the "ranking" was really a length
# sort. BM25's IDF term downweights words that are common across the corpus
# and its length normalisation stops a long chunk from winning on bulk alone.
#
# Cached against the meta file's (size, mtime), same pattern as
# `scope_tree._load_overrides`: a cheap `stat()` per call, a full retokenise
# only when the index actually changed. Tokenising is only paid on the
# fallback path (the embedder is down), never on a healthy search.

_BM25_K1 = 1.5
_BM25_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


_lexical_cache: tuple[tuple, dict] | None = None


def _bm25_state(meta: list[dict]) -> dict:
    """Postings, document lengths and the corpus average, built once per index
    generation. `postings[term]` is `{doc_index: term_frequency}`, so scoring a
    query only ever touches documents that actually contain one of its terms
    rather than looping the whole corpus per term."""
    global _lexical_cache
    mp = _meta_path()
    try:
        st = mp.stat()
        key = (str(mp), st.st_size, st.st_mtime_ns)
    except OSError:
        key = (str(mp), None, None)
    cached = _lexical_cache
    if cached is not None and cached[0] == key:
        return cached[1]

    postings: dict[str, dict[int, int]] = {}
    doc_len = [0] * len(meta)
    for i, m in enumerate(meta):
        toks = _tokenize(m.get("text", ""))
        doc_len[i] = len(toks)
        for t in toks:
            bucket = postings.setdefault(t, {})
            bucket[i] = bucket.get(i, 0) + 1
    avgdl = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
    state = {"postings": postings, "doc_len": doc_len, "avgdl": avgdl, "n": len(meta)}
    _lexical_cache = (key, state)
    return state


def _bm25_scores(query: str, meta: list[dict]) -> np.ndarray:
    """BM25 score for `query` against every chunk in `meta`, one float per row
    (0.0 where nothing matched). Full-corpus, like the vector path: the caller
    masks it, rather than this function taking a candidate subset."""
    state = _bm25_state(meta)
    n = state["n"]
    scores = np.zeros(n, dtype=np.float32)
    if n == 0:
        return scores
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return scores
    avgdl = state["avgdl"] or 1.0
    doc_len = state["doc_len"]
    postings = state["postings"]
    for t in q_tokens:
        docs = postings.get(t)
        if not docs:
            continue
        df = len(docs)
        # log(1 + ...), never the bare Robertson-Sparck-Jones form: that
        # variant goes negative for a term present in most of the corpus,
        # which would let a document's score fall by matching MORE of the
        # query. This variant is always >= 0.
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for i, tf in docs.items():
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len[i] / avgdl)
            scores[i] += idf * (tf * (_BM25_K1 + 1)) / denom
    return scores


# ------------------------------------------------------------- scope masking
#
# A chat chunk's `scope` field is really a chat `context_key` ("general",
# "budget", "development", ...), not a `scope_tree` scope_key: chats and
# knowledge notes are two different namespaces (design doc sec 7.1), and
# unifying them is a further migration this phase does not own. Only chats
# need the translation HERE; a note's `scope` is already a real scope_key (it
# came straight off `knowledge.all_scope_dirs()`) and a doc's is always "". A
# ticket's `scope` is a real scope_key too: every key `scope_roots()` returns is
# one, so nothing ticket-shaped needs a case here.
#
# A chunk with no scope at all (every doc) is never excluded by a scope
# filter: docs are cross-cutting reference material with no place in the
# tree, not something a narrower scope has failed to reach.

def _resolve_chunk_scope(kind: str, raw_scope: str) -> str:
    if kind != "chat" or not raw_scope:
        return raw_scope
    try:
        from services import knowledge
        return knowledge.scope_for_context(raw_scope)
    except Exception:
        return raw_scope


_SINCE_RELATIVE = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.I)


def parse_since(value: str | None) -> str:
    """A `since` window to an ISO date. Accepts `30d`, `2w`, `3m`, `1y`, or a
    plain `YYYY-MM-DD`.

    Anything unreadable returns "", meaning NO lower bound. A malformed date
    should widen the answer, never silently empty it: a filter that fails
    closed turns a typo into "the workshop has never written about this",
    which is the one wrong answer this corpus must not give."""
    raw = (value or "").strip()
    if not raw:
        return ""
    m = _SINCE_RELATIVE.match(raw)
    if m:
        days = int(m.group(1)) * {"d": 1, "w": 7, "m": 30, "y": 365}[m.group(2).lower()]
        import datetime as _dt
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(days=days)).date().isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    return ""


def resolve_chunk_scope(kind: str, raw_scope: str) -> str:
    """Public seam for `_resolve_chunk_scope`. A chat's stored `context_key` is
    not a `scope_tree` key, and anything that filters chat rows by reach has to
    apply the same translation this module does -- reimplementing it is how two
    filters drift into disagreeing about what a scope contains."""
    return _resolve_chunk_scope(kind, raw_scope)


def _scope_set(scope: str | tuple | list | set | frozenset | None) -> set[str] | None:
    """Normalise the `scope=` argument to a set, or None for "no filter".
    `search()` takes a set of allowed scopes; a bare string is still accepted
    so single-scope callers (existing tests, direct callers) keep working."""
    if scope is None:
        return None
    if isinstance(scope, str):
        return {scope}
    return set(scope)


def _item_ref(kind: str, ref: str, resolved_scope: str) -> str | None:
    """The `share` policy key for a chunk, in the Phase 3 ref vocabulary.

    A chunk's `ref` is whatever its collector recorded: a repo-relative path for
    notes and docs, a thread id for chats. This is the one place that is turned
    back into an addressable ref, so `memory_policy` and the Memory page are
    keyed the same way and a per-item override set in the UI is the one a search
    reads."""
    from services import memory_policy
    if kind == "chat":
        return memory_policy.chat_item_ref(ref)
    if kind == "note":
        note_id = Path(ref).stem
        return f"workshop:note/{resolved_scope}/{note_id}" if resolved_scope else None
    if kind == "doc":
        return f"workshop:doc/{ref}"
    if kind == "ticket":
        from services import refs
        return refs.ticket_ref(ref)
    return None


def _keep_mask(meta: list[dict], kinds, scopes: set[str] | None,
               from_scope: str | None = None) -> np.ndarray:
    """A boolean mask over the whole corpus, computed once and applied to the
    score vector rather than used to fancy-index `vecs` (design doc sec 7.2:
    `vecs[keep] @ q` allocates a submatrix per call; masking a precomputed
    `vecs @ q` does not, and the cost stops depending on how narrow the filter
    is)."""
    n = len(meta)
    mask = np.empty(n, dtype=bool)
    cache: dict[tuple[str, str], str] = {}
    # `from_scope` is the searcher's OWN scope, which `private` needs and plain
    # `reach` does not: "reachable only from inside its own scope" cannot be
    # answered by a set of allowed scopes alone. With no `from_scope` (a direct
    # or test caller that did not say who it is) the policy layer is skipped
    # entirely and this behaves exactly as it did before Phase 5.
    policy = None
    if from_scope:
        from services import memory_policy as policy  # noqa: N813
    for i, m in enumerate(meta):
        if kinds is not None and m.get("kind") not in kinds:
            mask[i] = False
            continue
        raw = m.get("scope") or ""
        kind = m.get("kind")
        if raw:
            key = (kind, raw)
            resolved = cache.get(key)
            if resolved is None:
                resolved = _resolve_chunk_scope(kind, raw)
                cache[key] = resolved
        else:
            resolved = ""
        # Unscoped chunks (docs) are placeless, not unreachable: they sit in no
        # scope, so the tree has nothing to say about them and they stay in.
        in_reach = True if (scopes is None or not resolved) else (resolved in scopes)
        if policy is None:
            mask[i] = in_reach
            continue
        share = policy.resolve(kind, resolved or None,
                               _item_ref(kind, m.get("ref") or "", resolved))
        mask[i] = policy.reachable(share=share, item_scope=resolved,
                                   from_scope=from_scope, in_reach=in_reach)
    return mask


def reachable_item(kind: str, scope: str, item_ref: str | None, *,
                   scopes: str | tuple | list | set | frozenset | None = None,
                   from_scope: str | None = None) -> bool:
    """`_keep_mask`'s decision, for ONE item that is not a chunk.

    Anything that hands a caller a piece of this corpus has to ask the same
    question a search asks, or it becomes a way around the boundary a search
    respected: a tool that reads a document in full is exactly that if it
    answers for a document the searcher was never shown. Two doors, one rule --
    the reach test and the `share` test are written once, here and in
    `_keep_mask`, and both end at `memory_policy.reachable`.

    `scope` is in the `scope_tree` namespace already (`resolve_chunk_scope`
    applied), and "" means placeless, which is reachable from anywhere for the
    same reason an unscoped doc chunk is."""
    allowed = _scope_set(scopes)
    in_reach = True if (allowed is None or not scope) else (scope in allowed)
    if not from_scope:
        return in_reach
    from services import memory_policy
    share = memory_policy.resolve(kind, scope or None, item_ref)
    return bool(memory_policy.reachable(share=share, item_scope=scope,
                                        from_scope=from_scope,
                                        in_reach=in_reach))


def corpus_map(kinds: tuple[str, ...] | list[str] | None = None,
               scopes: str | tuple | list | set | frozenset | None = None,
               from_scope: str | None = None) -> dict:
    """How big the corpus is, and how much of it this caller can reach.

    `status()` answers "is there an index"; this answers "how much is there",
    which is a different question and the one an agent needs BEFORE it searches
    rather than after it fails. A search that finds nothing means one thing
    against twelve documents and the opposite against twelve thousand, and
    until this existed there was no way to tell those apart from inside a chat.

    Counts DOCUMENTS as well as passages, because a passage count is a fact
    about the chunker and a document count is a fact about the workshop. Both
    are reported reachable-first, since the reachable number is the one that
    bounds any answer this caller can get.

        {"chunks", "reachable_chunks", "documents", "reachable_documents",
         "by_kind": {kind: {...}}, "oldest", "newest",
         "model", "embedder_ok", "embedder_error", "stale"}
    """
    meta, _vecs = load()
    keep = _keep_mask(meta, tuple(kinds) if kinds else None,
                      _scope_set(scopes), from_scope)
    by_kind: dict[str, dict] = {}
    docs: set[tuple[str, str]] = set()
    reachable_docs: set[tuple[str, str]] = set()
    reachable_chunks = 0
    oldest = newest = ""
    for i, m in enumerate(meta):
        kind = m.get("kind") or "?"
        ident = (kind, m.get("ref") or "")
        row = by_kind.setdefault(
            kind, {"passages": 0, "reachable_passages": 0,
                   "_docs": set(), "_reachable_docs": set()})
        row["passages"] += 1
        row["_docs"].add(ident)
        docs.add(ident)
        if not keep[i]:
            continue
        reachable_chunks += 1
        row["reachable_passages"] += 1
        row["_reachable_docs"].add(ident)
        reachable_docs.add(ident)
        ts = (m.get("ts") or "")[:10]
        if ts:
            oldest = ts if not oldest or ts < oldest else oldest
            newest = ts if not newest or ts > newest else newest
    for row in by_kind.values():
        row["documents"] = len(row.pop("_docs"))
        row["reachable_documents"] = len(row.pop("_reachable_docs"))
    ok, why = available()
    return {
        "chunks": len(meta),
        "reachable_chunks": reachable_chunks,
        "documents": len(docs),
        "reachable_documents": len(reachable_docs),
        "by_kind": by_kind,
        "oldest": oldest,
        "newest": newest,
        "model": _index_model(),
        "embedder_ok": ok,
        "embedder_error": why,
        # Not "a rebuild is running": the reader of this map is usually a
        # separate short-lived process with no rebuild thread of its own, so
        # thread liveness would report False for every caller that matters.
        # The on-disk staleness signal is the one that crosses processes.
        "stale": is_stale(),
    }


# ---------------------------------------------------------------- search

def search(query: str, *, limit: int = 8,
           kinds: tuple[str, ...] | list[str] | None = None,
           scope: str | tuple | list | set | frozenset | None = None,
           from_scope: str | None = None,
           since: str | None = None,
           min_score: float = MIN_SCORE) -> dict:
    """Ranked passages for a query, hybrid: vector when the embedder answers,
    BM25 lexical always available so a search degrades instead of raising.

    Returns `{"hits": [...], "mode": str, "rebuilding": bool, "searched": int,
    "corpus": int}`. `searched` is how many chunks the filters actually left in
    play and `corpus` is how many are in the index; they differ whenever a
    scope or a `share` policy excluded something, and a caller that reports the
    second as if it were the first tells a model it looked at 6,089 passages
    when the policy let it see 2. `mode` is
    `"semantic"`, `"lexical"`, or `"lexical (embedder unavailable: <reason>)"`
    so a caller (and the model reading the MCP tool's output) knows how much
    to trust the result. `rebuilding` is true when a background rebuild is in
    flight; the hits are still from whatever is on disk right now, never
    withheld while it runs.

    `scope` is the set of scopes the caller may reach (Phase 1's
    `scope_tree.reach`). `from_scope` is the caller's OWN scope, and it is a
    separate argument because the `share` field (Phase 5) needs it: `private`
    means "found only from inside its own scope", which a set of allowed scopes
    cannot express. Omitting it skips the policy layer entirely.

    A hit's BODY is returned, not a title. Being unable to read what was found
    is the defect this module exists to remove."""
    query = (query or "").strip()
    if not query:
        return {"hits": [], "mode": "semantic", "rebuilding": False,
                "searched": 0, "corpus": 0}

    # Self-healing, off the request path (design doc sec 7.2/7.4): a write
    # left a marker, so a background thread catches the index up while THIS
    # search still answers from what is on disk. On CPU a cold build is ~8
    # minutes; a search must never wait on it.
    try:
        rebuilding = _background_rebuild_if_stale()
    except Exception:
        log.warning("could not start a background rebuild; searching the "
                    "existing index", exc_info=True)
        rebuilding = False

    meta, vecs = load()
    if not meta:
        return {"hits": [], "mode": "semantic", "rebuilding": rebuilding,
                "searched": 0, "corpus": 0}

    scopes = _scope_set(scope)
    mask = _keep_mask(meta, kinds, scopes, from_scope)

    # RECENCY IS A FILTER, NEVER A RANKING TERM, and it stayed that way when
    # authority and kind became ranking terms on 2026-09-01. The original
    # reason still holds and the two are not the same request: a scoring bonus
    # for newness would quietly bury the decision that settled a question under
    # the most recent chatter about it, and on this corpus the most recent
    # thing said about any subject is almost always a chat message. What was
    # actually wanted from "make recency count" is that a RETIRED document stop
    # being returned as if current, and a document is retired when it says so
    # or when the tree says so -- not when it is old. Some of the oldest files
    # in this corpus are still exactly right; a plan written last week can be
    # dead. So staleness is read from the declaration
    # (`doc_status`, `_dir_status`), never from the timestamp.
    #
    # A caller asking "lately" is narrowing the corpus, which is a different
    # act, and it is stated in `searched` like every other filter.
    floor_day = parse_since(since)
    if floor_day:
        recent = np.array([(m.get("ts") or "")[:10] >= floor_day for m in meta])
        mask = mask & recent

    searched = int(mask.sum())
    if not searched:
        return {"hits": [], "mode": "semantic", "rebuilding": rebuilding,
                "searched": 0, "corpus": len(meta)}

    mode = "semantic"
    scores: np.ndarray | None = None
    try:
        q = embed([query])[0]
        scores = vecs @ q
    except Exception as e:
        reason = str(e).strip() or "unknown error"
        if len(reason) > 160:
            reason = reason[:157] + "..."
        log.warning("embedder unavailable, falling back to lexical search: %s",
                    reason, exc_info=True)
        mode = f"lexical (embedder unavailable: {reason})"

    if scores is None:
        scores = _bm25_scores(query, meta)
        floor = 0.0  # BM25 has no meaning below zero: nothing matched.
        is_semantic = False
    else:
        floor = min_score
        is_semantic = True

    masked = np.where(mask, scores, -np.inf)

    # RANK on an adjusted score, ADMIT on the real one. Three corrections, all
    # measured against this corpus (docs are 66% of the index):
    #
    # 0. AUTHORITY AND KIND WERE NOT SIGNALS AT ALL, so a conversation ABOUT a
    #    procedure beat the document that IS the procedure. Measured
    #    2026-09-01: "how do I restart the backend" returned three chats above
    #    the one document the instructions file names as the only current
    #    source for exactly that. Both offsets are declared and explained at
    #    `_STATUS_ADJUST` / `_KIND_ADJUST`; both are ranking-only, like the
    #    penalty below, so neither can hide anything.
    #
    # 1. SUPERSEDED DOCS OUTRANKED THE MODEL OF RECORD. "is an app the same
    #    thing as a project" returned docs/workshop-v1/00-overview.md first --
    #    a doc whose own banner says it is historical and which CLAUDE.md says
    #    not to follow -- beating app-is-the-entity-2026-08-20.md by 0.001.
    #    "how do agents get typed, what is the runtime field" gave three of its
    #    five slots to a July migration plan describing a field that no longer
    #    exists. Search was teaching agents the retired model, which is the
    #    single most expensive thing this corpus can do.
    #
    #    Demoted, NOT hidden: the penalty moves a superseded chunk below any
    #    current one that also matched, while the floor test below still runs
    #    on the RAW score, so a question whose only answer is historical still
    #    gets answered. History is often exactly what is being asked for.
    #
    # 2. ONE DOCUMENT ATE THE ANSWER. Adjacent chunks of the same file are
    #    near-identical in embedding space, so a five-hit answer could be three
    #    chunks of one plan. Capping chunks per ref spends the budget on
    #    distinct sources instead. The cap applies to ORDER, never to the
    #    floor, so it can only ever change which passages are shown.
    adjusted = masked.copy()
    if is_semantic:
        # One offset per row: the document's declared lifecycle plus its kind.
        # Computed together rather than in two passes so the total any passage
        # can move is visible in one place, and so an authoritative doc and a
        # superseded one can never both apply.
        #
        # Semantic mode ONLY. BM25 scores are unbounded and corpus-relative, so
        # a constant sized in cosine units means nothing against them; on the
        # fallback path the ranking stays pure lexical rather than pretending
        # to a precision it does not have.
        offset = np.array(
            [_STATUS_ADJUST.get(m.get("status") or "", 0.0)
             + _KIND_ADJUST.get(m.get("kind") or "", 0.0) for m in meta],
            dtype=np.float64,
        )
        # Bonuses are gated to the competitive band, penalties are not. A
        # demoted passage is already losing; a promoted one has to have been in
        # contention to begin with, or the promotion is not a tie-break.
        in_band = masked >= (float(masked.max()) - _AUTHORITY_BAND)
        effective = np.clip(offset, None, 0.0) + np.where(
            in_band, np.clip(offset, 0.0, None), 0.0)
        adjusted = np.where(np.isfinite(adjusted), adjusted + effective, adjusted)

    # Scan deeper than `limit` so the per-ref cap has replacements to promote.
    order = np.argsort(-adjusted)[:max(limit * _OVERSCAN, limit)]
    hits = []
    per_ref: dict[str, int] = {}
    for j in order:
        i = int(j)
        score = float(masked[i])          # the REAL score decides admission
        if not np.isfinite(adjusted[i]):
            break                          # masked out; nothing below matches
        if (score < floor) if is_semantic else (score <= floor):
            continue                       # ordering is by adjusted, so keep scanning
        m = meta[i]
        ref = str(m.get("ref") or "")
        if per_ref.get(ref, 0) >= _MAX_PER_REF:
            continue
        per_ref[ref] = per_ref.get(ref, 0) + 1
        kind = m.get("kind") or ""
        # THE ADDRESSABLE FORM, BESIDE THE RAW ONE. `ref` is whatever the
        # collector recorded (a repo-relative path, a thread id, a bare ticket
        # id) and stays exactly that, because the per-ref cap above and every
        # existing consumer key off it. `item_ref` is the same chunk in the one
        # ref vocabulary -- the thing `refs.py` parses, the thing a citation
        # renders as a chip, and the thing `open_ref` reads. Without it a caller
        # holding a hit could not name the document the hit came from: for a
        # note the two forms do not even share a prefix
        # (`shared/knowledge/development/x.md` against `note/development/x`),
        # so it could not be derived on the far side either.
        hits.append({
            "score": round(score, 4),
            "kind": m.get("kind"), "ref": m.get("ref"),
            "item_ref": _item_ref(kind, ref,
                                  _resolve_chunk_scope(kind, m.get("scope") or "")),
            "title": m.get("title"), "scope": m.get("scope"),
            "ts": m.get("ts"), "text": m.get("text"),
            # Carried into the answer so the READER can see it too. A demotion
            # the agent cannot observe would just be a silent reordering.
            "status": m.get("status") or "",
        })
        if len(hits) >= limit:
            break
    return {"hits": hits, "mode": mode, "rebuilding": rebuilding,
            "searched": searched, "corpus": len(meta)}


def status() -> dict:
    """What is indexed, for a caller that needs to say why a search was empty."""
    meta, vecs = load()
    by_kind: dict[str, int] = {}
    for m in meta:
        by_kind[m.get("kind", "?")] = by_kind.get(m.get("kind", "?"), 0) + 1
    ok, why = available()
    return {"chunks": len(meta), "dim": int(vecs.shape[1]) if vecs.size else 0,
            "by_kind": by_kind, "model": _index_model(),
            "embedder_ok": ok, "embedder_error": why}


# ---------------------------------------------------------------- CLI

def pin_owner(owner_id: int | None = None) -> None:
    """Pin the data owner for a call that has no HTTP request behind it.

    The index reads owner-scoped stores (chat logs, the knowledge root), so a CLI
    build or a background pass has to say whose data it is. Defaulting to the
    operator matches `ownership`'s own rule for background loops, and mirrors
    `workshop_query_server._pin_owner`."""
    from services import ownership

    if owner_id is not None:
        ownership.set_current_owner(owner_id)
    else:
        ownership.pin_operator()


def _main(argv: list[str]) -> int:
    pin_owner()
    verb = argv[1] if len(argv) > 1 else "status"
    if verb == "build":
        kinds = tuple(argv[2].split(",")) if len(argv) > 2 else default_kinds()
        print(json.dumps(build(kinds, log_fn=lambda s: print(f"  {s}")), indent=2))
    elif verb == "search":
        result = search(" ".join(argv[2:]), limit=5)
        print(f"mode: {result['mode']}"
              + ("  (rebuilding in background)" if result["rebuilding"] else ""))
        for h in result["hits"]:
            print(f"[{h['score']:.3f}] {h['kind']}: {h['title']}  ({h['ref']})")
            print("    " + h["text"][:180].replace("\n", " ") + "\n")
    else:
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
