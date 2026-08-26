"""refs -- the one ref format (memory-and-documentation-model-2026-08-14.md
sec 10, sec 12 Phase 3).

`workshop:<kind>/<...>` is how one entity in this system names another WITHOUT
a file path or a copyable URL -- the acceptance test for design doc sec 10 is
"nothing renders a raw path or a copyable URL". This module is the single
parser/serializer (`parse_ref`/`serialize_ref`), the extractor that finds refs
inside free text at write time (`extract_refs`, feeding `services.backlinks`),
and the resolver that turns a ref into `{kind, title, href, status}` or
refuses (`resolve_ref`).

    workshop:note/<scope>/<id>
    workshop:doc/<repo-relative-path>
    workshop:ticket/<id>
    workshop:chat/<thread-id>#<message-id>
    workshop:run/<id>
    workshop:project/<slug>
    workshop:agent/<slug>
    workshop:app/<name>

A note ref's `<scope>` is validated against `scope_tree.parse`, which is why
this module lives beside `scope_tree.py` rather than under `routes/`: refs
carry scope keys and must agree with the tree on what a valid scope is.

REFUSAL, NOT A DEAD CHIP. `resolve_ref` returns None for anything it cannot
positively confirm still exists: a bad shape, a deleted note, a path escaping
docs/, a ticket/thread/run/project/agent/app id that is not there. A dead link
that looks alive is worse than a visibly dead one (design doc sec 10); the
frontend's `<Ref>` renders a None as plain text.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("workshop.refs")

VALID_KINDS = ("note", "doc", "ticket", "chat", "run", "project", "agent", "app", "file")

_REF_RE = re.compile(r"^workshop:([a-z]+)/(.+)$")


@dataclass(frozen=True)
class Ref:
    """A parsed ref. `key` is everything after `workshop:<kind>/`, kept as the
    raw string (not split into fields) because what it means depends on
    `kind`; `parse_ref` already validated its shape before construction."""
    kind: str
    key: str


def parse_ref(text: str) -> Ref:
    """Parse `text` into a `Ref`, or raise ValueError. Never returns a Ref for
    a shape it cannot round-trip through `serialize_ref`."""
    raw = (text or "").strip()
    m = _REF_RE.match(raw)
    if not m:
        raise ValueError(f"not a workshop ref: {text!r}")
    kind, key = m.group(1), m.group(2)
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown ref kind: {kind!r}")
    if not key:
        raise ValueError(f"ref has no key: {text!r}")
    if kind == "note":
        scope, _, note_id = key.partition("/")
        if not scope or not note_id or "/" in note_id:
            raise ValueError(f"note ref needs <scope>/<id>: {text!r}")
    elif kind == "chat":
        thread_id, sep, message_id = key.partition("#")
        if not sep or not thread_id or not message_id:
            raise ValueError(f"chat ref needs <thread-id>#<message-id>: {text!r}")
    elif kind == "doc":
        if not _clean_doc_path(key):
            raise ValueError(f"doc ref must be a clean repo-relative path: {text!r}")
    elif kind == "file":
        # A file ref addresses a chat attachment by its opaque id. The id never
        # contains a separator, so a key with one is a path trying to escape the
        # store — refused the same way _clean_doc_path refuses `..`.
        if "/" in key:
            raise ValueError(f"file ref key must be an opaque attachment id: {text!r}")
    return Ref(kind=kind, key=key)


def serialize_ref(ref: Ref) -> str:
    return f"workshop:{ref.kind}/{ref.key}"


# --- convenience constructors, one per kind (used by writers) -----------------
def note_ref(scope: str, note_id: str) -> str:
    return serialize_ref(Ref("note", f"{scope}/{note_id}"))


def doc_ref(path: str) -> str:
    return serialize_ref(Ref("doc", path))


def ticket_ref(ticket_id: str) -> str:
    return serialize_ref(Ref("ticket", ticket_id))


def chat_ref(thread_id: str, message_id: str) -> str:
    return serialize_ref(Ref("chat", f"{thread_id}#{message_id}"))


def run_ref(run_id: str) -> str:
    return serialize_ref(Ref("run", run_id))


def project_ref(slug: str) -> str:
    return serialize_ref(Ref("project", slug))


def agent_ref(slug: str) -> str:
    return serialize_ref(Ref("agent", slug))


def app_ref(name: str) -> str:
    return serialize_ref(Ref("app", name))


def file_ref(attachment_id: str) -> str:
    return serialize_ref(Ref("file", attachment_id))


# --- doc path containment ------------------------------------------------------
# Same containment shape as routes/docs_index.read_doc (the precedent this
# module was told to follow): resolve against docs/ and require the resolved
# path to still sit inside it. `..`, a leading `/`, and a symlink that escapes
# all fail the same way here as there.
def _clean_doc_path(rel: str) -> Path | None:
    """A `workshop:doc/<rel>` key resolved to a markdown file anywhere in the
    tree (not only `docs/`), so a root-level doc like PROJECT.md or TODO.md is a
    real managed link. Containment is the repo root: `..`, an absolute path
    outside it, and a symlink resolving outside all fail the `relative_to`
    check. The `.md`-only test lives in the caller."""
    from wsplatform import paths
    root = paths.workshop_root().resolve()
    try:
        candidate = (root / rel).resolve()
        candidate.relative_to(root)
    except (ValueError, OSError):
        return None
    return candidate


# --- extraction (write-time, feeds services.backlinks) ------------------------
_TOKEN_RE = re.compile(r"workshop:[a-z]+/[^\s)\]\"'<>]+")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")


def extract_refs(text: str, *, note_scope: str | None = None) -> list[str]:
    """Every syntactically valid, currently-parseable ref named in `text`,
    normalized (parsed then re-serialized so two spellings of the same ref
    collapse to one) and de-duplicated, order preserved.

    A candidate that fails to parse (a typo, a truncated match) is silently
    dropped: writing an unparseable string into the backlink index is worse
    than missing an edge, because it would be extracted once at write time and
    never checked again.

    `note_scope`, when given, ALSO resolves `[[Title]]` wikilinks against the
    knowledge notes in that scope -- the wikilink-convergence rule (design doc
    sec 12 Phase 3 / sec D of the ticket): a note that links `[[Other Note]]`
    is extracted as `workshop:note/<note_scope>/<id>` through the SAME title
    lookup `resolve_ref` would use, not a second link language. This is scoped
    to knowledge notes on purpose -- the `~/notes` vault's `[[wikilinks]]`
    resolve by vault-relative PATH over a frozen, unrelated corpus (design doc
    sec 9), and forcing them through a scope+id lookup would resolve nothing
    real. See `frontend/src/pages/apps/notes/wikilinks.ts`'s own docstring for
    the vault's resolution rule; this one is deliberately different."""
    if not text:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()

    def _add(norm: str) -> None:
        if norm not in seen_set:
            seen_set.add(norm)
            seen.append(norm)

    for m in _TOKEN_RE.finditer(text):
        candidate = m.group(0).rstrip(".,;:!?")
        try:
            ref = parse_ref(candidate)
        except ValueError:
            continue
        _add(serialize_ref(ref))

    if note_scope:
        from services import knowledge
        titles: dict[str, str] | None = None
        for wm in _WIKILINK_RE.finditer(text):
            target = wm.group(1).strip()
            if not target:
                continue
            if titles is None:
                try:
                    titles = {
                        n["title"].strip().lower(): n["id"]
                        for n in knowledge.list_notes(note_scope)
                    }
                except Exception:
                    titles = {}
            note_id = titles.get(target.lower())
            if note_id:
                _add(note_ref(note_scope, note_id))

    return seen


# --- resolution -----------------------------------------------------------
def resolve_ref(ref_str: str) -> dict | None:
    """`{kind, title, href, status}` for `ref_str`, or None when it cannot be
    positively confirmed to still resolve. Never raises."""
    try:
        ref = parse_ref(ref_str)
    except ValueError:
        return None
    resolver = _RESOLVERS.get(ref.kind)
    if resolver is None:
        return None
    try:
        return resolver(ref)
    except Exception:
        log.debug("ref resolution failed for %r", ref_str, exc_info=True)
        return None


_EXCERPT_MAX = 220


def _excerpt(text: str | None) -> str | None:
    """A short, plain-text lead-in for an entity's body: the first lines with
    markdown lightly stripped, collapsed to one run and truncated. Returned in
    `preview.excerpt` and shown by the ref card when there is no thumbnail.
    None for an empty body, so the card shows nothing rather than a blank line."""
    if not text:
        return None
    s = re.sub(r"<!--.*?-->", " ", text, flags=re.S)   # author markers, comments
    s = re.sub(r"```.*?```", " ", s, flags=re.S)         # fenced code
    lines: list[str] = []
    for raw in s.splitlines():
        t = raw.strip()
        if not t:
            continue
        t = re.sub(r"^\s*(#{1,6}\s+|[-*+]\s+|>\s+|\d+\.\s+)", "", t)  # block marks
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)                # links -> text
        t = re.sub(r"[`*_]{1,3}", "", t)                             # emphasis/code
        lines.append(t)
        if sum(len(x) for x in lines) >= _EXCERPT_MAX:
            break
    out = " ".join(lines).strip()
    if not out:
        return None
    return out[:_EXCERPT_MAX].rstrip() + "…" if len(out) > _EXCERPT_MAX else out


def _resolve_note(ref: Ref) -> dict | None:
    from services import knowledge, scope_tree
    scope, _, note_id = ref.key.partition("/")
    scope_tree.parse(scope)  # raises ValueError -> caught by resolve_ref
    body = knowledge.get_note(scope, note_id)
    if body is None:
        return None
    title = note_id
    for n in knowledge.list_notes(scope):
        if n["id"] == note_id:
            title = n["title"]
            break
    return {
        "kind": "note",
        "title": title,
        "href": f"/memory?scope={scope}&note={note_id}",
        "status": "current",
        "preview": {"excerpt": _excerpt(body)},
    }


def _resolve_doc(ref: Ref) -> dict | None:
    from wsplatform import paths
    candidate = _clean_doc_path(ref.key)
    if candidate is None or candidate.suffix != ".md" or not candidate.is_file():
        return None
    title = candidate.stem
    body = ""
    try:
        raw = candidate.read_text()
        # Drop a leading H1 so the excerpt does not just repeat the title.
        body = re.sub(r"^\s*#\s+.+?\n", "", raw, count=1)
        for line in raw.splitlines():
            m = re.match(r"^\s*#\s+(.+?)\s*$", line)
            if m:
                title = m.group(1).strip()
                break
    except OSError:
        pass
    rel = candidate.relative_to(paths.workshop_root().resolve())
    return {
        "kind": "doc",
        "title": title,
        "href": f"/memory?tab=docs&path={rel}",
        "status": "current",
        "preview": {"excerpt": _excerpt(body)},
    }


def _resolve_ticket(ref: Ref) -> dict | None:
    from routes import tasks_all
    from services import tasks
    for scope, root in tasks_all.scope_roots():
        task = tasks.get_task(root, ref.key)
        if task is not None:
            return {
                "kind": "ticket",
                "title": task.get("title") or ref.key,
                "href": f"/tickets/{scope}/{ref.key}",
                "status": task.get("status") or "todo",
                "preview": {"excerpt": _excerpt(task.get("body"))},
            }
    return None


def _resolve_chat(ref: Ref) -> dict | None:
    from services import chat_store
    thread_id, _, message_id = ref.key.partition("#")
    thread = chat_store.get_thread(thread_id)
    if thread is None:
        return None
    found = any(m.get("id") == message_id for m in (thread.get("messages") or []))
    if not found:
        return None
    context_key = thread.get("context_key") or "general"
    status = "archived" if thread.get("archived_at") else "active"
    opening = next(
        (str(m.get("text") or m.get("content") or "")
         for m in (thread.get("messages") or []) if m.get("role") == "user"),
        "",
    )
    return {
        "kind": "chat",
        "title": thread.get("title") or "Chat",
        "href": f"/chat/{context_key}?thread={thread_id}#msg-{message_id}",
        "status": status,
        "preview": {"excerpt": _excerpt(opening)},
    }


def _resolve_run(ref: Ref) -> dict | None:
    from services import jobs
    rec = jobs.get(ref.key)
    if rec is None:
        return None
    title = rec.get("agent_id") or rec.get("project_slug") or f"{rec.get('trigger', 'manual')} run"
    return {
        "kind": "run",
        "title": str(title),
        "href": f"/runs/{ref.key}",
        "status": rec.get("status") or "queued",
    }


def _resolve_project(ref: Ref) -> dict | None:
    from routes import projects as projects_routes
    data = projects_routes._load_registry()
    proj = data.get("projects", {}).get(ref.key)
    if not proj:
        return None
    return {
        "kind": "project",
        "title": proj.get("title") or ref.key,
        "href": f"/projects/{ref.key}",
        "status": proj.get("state") or "active",
    }


def _resolve_agent(ref: Ref) -> dict | None:
    from services import agents_registry
    rec = agents_registry.get(ref.key)
    if rec is None:
        return None
    return {
        "kind": "agent",
        "title": rec.get("name") or rec.get("slug") or ref.key,
        "href": f"/agents/{ref.key}",
        "status": "retired" if rec.get("retired") else "active",
    }


def _resolve_app(ref: Ref) -> dict | None:
    from services import app_registry
    for spec in app_registry.installed_specs():
        card = spec.card or {}
        route = str(card.get("route") or "")
        slug = route.rsplit("/", 1)[-1] if route else ""
        if ref.key in (spec.id, slug):
            return {
                "kind": "app",
                "title": card.get("title") or ref.key,
                "href": route or f"/apps/{ref.key}",
                "status": "active",
            }
    return None


def _resolve_file(ref: Ref) -> dict | None:
    """A chat attachment addressed by id. `href` is the in-app route that serves
    the bytes (never a filesystem path); `preview` is a descriptor the <Ref>
    dropdown renders. Refuses (None) when the attachment is gone, like every
    other resolver. An image is its own thumbnail — the API serves the bytes —
    while a pdf/text rendering is a Layer 3 follow-up, so `thumb_url` is null
    there rather than a path."""
    from services import chat_attachments
    att_id = ref.key
    rec = chat_attachments.get(att_id)
    if rec is None:
        return None
    kind = rec.get("kind") or ""
    if kind == "image":
        icon = "image"
    elif kind == "pdf":
        icon = "pdf"
    elif rec.get("converter"):
        icon = "zip" if rec.get("converter") in ("zip", "archive") else "generic"
    elif kind == "text":
        icon = "text"
    else:
        icon = "generic"
    mime = rec.get("media_type") or (
        "image/png" if kind == "image"
        else "application/pdf" if kind == "pdf"
        else "text/plain")
    href = f"/api/attachments/{att_id}"
    # An image and a pdf both get a lazily-rendered, cached raster thumbnail from
    # the /thumb endpoint (small bytes, not the whole file). Text and converted
    # binaries have no raster; the card shows the kind icon and, later, an excerpt.
    thumb_url = f"{href}/thumb" if kind in ("image", "pdf") else None
    return {
        "kind": "file",
        "title": rec.get("name") or att_id,
        "href": href,
        "status": "current",
        "preview": {
            "mime": mime,
            "size_bytes": rec.get("size"),
            "icon": icon,
            "thumb_url": thumb_url,
        },
    }


_RESOLVERS = {
    "note": _resolve_note,
    "doc": _resolve_doc,
    "ticket": _resolve_ticket,
    "chat": _resolve_chat,
    "run": _resolve_run,
    "project": _resolve_project,
    "agent": _resolve_agent,
    "app": _resolve_app,
    "file": _resolve_file,
}
