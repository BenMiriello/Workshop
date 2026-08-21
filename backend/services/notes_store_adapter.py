"""The notes app's Store adapter: the Markdown vault behind type "Note".

This was the vault half of `stores.NoteAdapter`, which served two substrates and
chose between them with `if store.owner_app == "knowledge"` -- the platform's
generic data door branching on which app it was talking to. The knowledge half
stays in the platform (it is the platform's own note store); this half moves to
the app that owns the folder, and is contributed through
`AppSpec.store_adapters`, so it is present exactly while the notes app is.

The store DECLARATION has deliberately not moved with it. The vault is declared
as `workshop://store/notes/vault` in `stores.register_builtin_stores`, and that
URI is written into the live `.runtime/store-policies.json`; declaring it from
the AppSpec would re-key it to `workshop://store/notes-app/vault` and silently
drop the policy that names it. That is a data migration, and it belongs with the
extraction stage rather than here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.stores import AccessDenied, Store, StoreAdapter


class VaultNoteAdapter(StoreAdapter):
    """Type "Note", vault-backed: arbitrary folders of Markdown that a real
    Obsidian opens untouched.

    THE SEARCH LEAK RULE lives here, because here is where a snippet would be
    produced: `search` WITHOUT `read` returns path/title/match counts and no
    snippet, since a snippet is a body and would leak exactly what `read` was
    withheld for. `search` WITH `read` may return snippets. Mechanical; the
    broker is not consulted and cannot waive it.
    """

    type = "Note"

    def claims(self, store: Store) -> bool:
        # Every Note store EXCEPT the platform's knowledge scopes, which have
        # their own adapter and their own write door.
        return (store.type or "") == "Note" and store.owner_app != "knowledge"

    def selectors(self) -> "list[str]":
        return ["query", "name", "folder"]

    def _items(self, path: str, selector: dict[str, Any], verbs: "list[str]",
                     verb: str) -> "list[dict[str, Any]]":
        from services import notes_vault as vault
        prefix = (path or selector.get("folder") or "").strip("/")

        if verb == "read":
            name = selector.get("name") or prefix
            if not name:
                raise ValueError("read needs selector.name (or a path-suffixed uri)")
            note = vault.read_note(str(name))
            return [note]

        if verb == "list":
            notes = vault.list_notes()
            return [n for n in notes if not prefix or n["path"].startswith(prefix + "/")
                    or n["path"] == prefix]

        # search
        query = str(selector.get("query") or "")
        if not query:
            raise ValueError("search needs selector.query")
        hits = vault.search(query, limit=200)
        if prefix:
            hits = [h for h in hits if h["path"].startswith(prefix + "/") or h["path"] == prefix]
        if "read" in verbs:
            return hits
        # THE SEARCH LEAK RULE: no snippets without `read`. Collapse to counts.
        by_path: dict[str, int] = {}
        for h in hits:
            by_path[h["path"]] = by_path.get(h["path"], 0) + 1
        titles = {n["path"]: n["title"] for n in vault.list_notes()}
        return [
            {"path": p, "title": titles.get(p, p), "matches": n}
            for p, n in sorted(by_path.items())
        ]

    def resolve(self, store, path, selector, verbs, verb):
        return self._items(path, selector, verbs, verb)

    def write(self, store, path, content, *, verb, selector):
        """Create, overwrite, or append a note.

        The platform door used to hold this branch and the reason for it. The
        reason still holds and travels with the code: a knowledge scope keeps
        its own write door (`PUT /api/knowledge/{scope}/notes/{id}`, which owns
        the MEMORY.md index write too), so duplicating it here would give that
        index two writers. It is now expressed as the knowledge adapter simply
        not implementing `write`, rather than as this door naming that case.
        """
        from services import notes_vault as vault

        name = str(selector.get("name") or path or "")
        if not name:
            raise ValueError("write needs selector.name (or a path-suffixed uri)")
        existing = Path(vault.safe_path(name)).is_file()
        if verb == "append" and existing:
            current = vault.read_note(name)["content"]
            return vault.update_note(name, current + content)
        if existing:
            return vault.update_note(name, content)
        return vault.create_note(name, content)
