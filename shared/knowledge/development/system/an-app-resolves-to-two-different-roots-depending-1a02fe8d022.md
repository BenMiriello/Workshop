<!-- author: self; chat: dbd875271cbe40f888e589ae4b59abac -->
Measured 2026-08-23.

`app:<slug>` means two different directories:

- `services/app_files.py::_resolve_app` -> `app_links.linked_project_slug` -> `projects/<slug>` (the Files tab, the file API)
- `wsplatform/paths.py::scope_root` -> `apps/<slug>` (the knowledge store, the Memory tab)

Consequences measured on the live tree:

- The Birds Files tab serves `projects/birds` (118 files, 976K, empty `src/`) while the Birds app is `apps/birds` (30,150 files, 2.2G). The real app is unreachable from the UI.
- `app:X` and `project:X` are unrelated SIBLINGS in `scope_tree`, so four apps produced eight scope rows. `_default_parent`'s docstring claims apps have no project binding, but `app_links.linked_project_slug` supplies exactly that and `_resolve_app` already uses it.
- `project:commute` holds 2 real operator notes that the Commute app's Memory tab cannot see.

This is `app-is-the-entity-2026-08-20.md`'s split showing through the filesystem. `_resolve_app`'s docstring ("an app's files ARE its linked project's working tree") states the retired model as current.

Separately: `knowledge.scope_dir` ends in `mkdir(parents=True, exist_ok=True)` and every READ path goes through it, so `list_notes` creates the scope. Proven with `list_notes('app:zzprobe')` for a nonexistent app: returned `[]`, created `apps/zzprobe/knowledge/`. This produced a visible `agent:undefined` scope in the Memory tree. `routes/notes_all.py::_scope_roots` claims the opposite invariant.