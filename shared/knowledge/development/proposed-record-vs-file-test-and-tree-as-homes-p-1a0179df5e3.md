<!-- author: self; chat: 8903f54d6d834fbcbbf7ca21f5fe86d0 -->
Proposal from the 2026-08-18 nav/structure discussion, NOT yet ratified.

Core principle: every resource has one address (`workshop:<kind>/<key>`, refs.py) and one home scope (`paths.scope_root`). The tree shows homes; every page is a lens (a query over addresses). The filesystem is one backing store, not the organizing idea.

**The record/file test**: would a version upgrade need to rewrite this? Yes -> record (Postgres, typed surface only). No -> file (under scope_root, agent-editable prose). Assigns: chats/tickets/runs/agents/grants/deploy-config/views = records; docs/code/memory notes/skills/personas = files. Cited precedent: Home Assistant ADR-0010 moved integration config to UI config entries specifically so the platform can guarantee cross-version migration.

**Scope is ownership plus a default filter, not containment.** Standing in project X, a page defaults to `scope in reach(project:X)` and widens in place. This is what makes global-vs-scoped agents and cross-scope chats stop being special cases. reach()/govern() in scope_tree.py already compute it.

**Do not virtualize files; project resources.** A scope node's children are typed collections (Chats, Tickets, Memory, Agents, Deploys) plus exactly one directory-shaped child (Files = scope_root). Only Files has mv/rename/create-any semantics. Evidence against the other direction: VS Code required a per-extension `capabilities.virtualWorkspaces` flag because extensions broke when 'files' were not files.

**Pages are a closed set; views are unlimited.** Reject user-created folders-as-tabs (an untyped node no lens can render). Use saved views (stored query + display mode), pinnable into the tree. Linear/Notion/Jira/Smart Folders precedent.

**Sync line = record/file line.** Records sync (Linear/Figma sync-engine shape: local store, server total ordering, server timestamp wins). Files stay per-machine, reconnected by scope, git already solves it. Business model falls out: desktop free + BYO key (Conductor's model), cloud paid for continuity (Cursor cloud agents, Obsidian Sync).

**Read-only platform floor** is a convention today and only becomes enforceable via the bwrap scoped-exec work (SEC-A1); ordering is confinement first, then the floor is a mount flag.