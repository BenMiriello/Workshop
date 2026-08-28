"""Keeping `workshop:doc/<path>` refs pointing at a document that moved.

THE DEFECT THIS EXISTS FOR (measured 2026-08-27, written up in
docs/platform-redesign/memory-page-and-file-viewer-2026-08-27.md section 1.6).

A doc ref is the repo-relative path of a markdown file, resolved against
`paths.workshop_root()`. The backlink index stores the RESOLVED ref as a
target, and `services.backlinks` has exactly three mutators, all of them keyed
by the SOURCE. Nothing anywhere rewrites a stored target. So a file that moves
leaves every ref to it naming a path that is not there, and the failure is
delayed and silent: the backlink row survives until the source that wrote it is
next saved and re-extracted, at which point the edge disappears with no error.

`services.knowledge` fixed exactly this for a note's TITLE on the same day, and
this is the same fix on the other half of the store: read the inbound sources
from the index rather than scanning the corpus, rewrite the text in each, and
REPORT what could not be rewritten instead of swallowing it. A half-updated
link graph the operator does not know about is worse than one they do.

WHAT IS REWRITTEN, and what deliberately is not. Only NOTES: `services.tasks`
and `services.chat_store` both extract refs from bodies this code has no write
path for, and a ticket body is the operator's text in a different store. Those
come back as `stale` so the caller can say so.
"""
from __future__ import annotations

import logging

log = logging.getLogger("workshop.doc_refs")


def repoint(old_path: str, new_path: str) -> tuple[list[str], list[str]]:
    """Rewrite `workshop:doc/<old_path>` to name `new_path` everywhere it is
    referenced from. Returns `(rewritten_source_refs, stale_source_refs)`.

    Best effort by contract: the file has already moved when this runs, and
    refusing the rename after the fact is not something a caller can act on.
    Everything it could not do comes back in the second list.
    """
    from services import backlinks, knowledge, refs

    old_ref = refs.doc_ref(old_path)
    new_ref = refs.doc_ref(new_path)
    rewrote: list[str] = []
    stale: list[str] = []

    for source in backlinks.referenced_by(old_ref):
        # `parse_ref` raises on anything it cannot round-trip, and the index
        # holds ticket and chat refs too.
        try:
            parsed = refs.parse_ref(source)
        except ValueError:
            stale.append(source)
            continue
        if parsed.kind != "note":
            # A ticket body and a chat receipt are not this module's to edit.
            stale.append(source)
            continue
        scope, _, note_id = parsed.key.partition("/")
        if not scope or not note_id:
            stale.append(source)
            continue
        try:
            body = knowledge.get_note(scope, note_id)
            if body is None:
                stale.append(source)
                continue
            # Both spellings a body can carry: the ref token itself, and a
            # markdown link whose target is the bare repo-relative path.
            rewritten = body.replace(old_ref, new_ref).replace(f"]({old_path})", f"]({new_path})")
            if rewritten == body:
                # Referenced by the index but not by the current text: the body
                # changed without the index being rewritten. Not this module's
                # to repair, and not a failure of this rename.
                continue
            knowledge._put_note_sync(
                scope, note_id,
                knowledge.note_meta(scope, note_id).get("title", note_id),
                rewritten,
                hook=knowledge.note_meta(scope, note_id).get("hook", ""),
            )
            rewrote.append(source)
            log.info("doc rename: repointed %s -> %s in %s/%s",
                     old_path, new_path, scope, note_id)
        except Exception:
            log.warning("doc rename: could not repoint %s in %s", old_ref, source,
                        exc_info=True)
            stale.append(source)

    return rewrote, stale
