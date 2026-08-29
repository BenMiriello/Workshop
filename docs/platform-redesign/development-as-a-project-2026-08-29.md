# Development belongs in a project, not the home scope

- Date: 2026-08-29
- Status: design only, no migration started
- Ticket: `cefff92679f541168fd132d6236a23a4`
- Relates to: `app-is-the-entity-2026-08-20.md`, `dev-mode-and-development-scope-2026-08-09.md`,
  `entity-model-2026-07-18.md`

---

## 1. The operator's direction

> "we need a development project. development on workshop needs to go in a
> project. only in development env/mode of course. nothing of the development
> app/code/capability should ever serve in production. instead of doing the
> 'development' work on workshop IN the workshop/home scope, this would make
> more sense."

Two things this doc must answer, in order of load-bearing importance:

1. What entity the development project maps to in the existing model.
2. What mechanism enforces that none of it serves in production, and how
   that mechanism is measured.

The real deliverable is section 3: what is dev-only today and ships anyway,
enumerated from the tree, not from memory.

---

## 2. What the development project is in the existing model

No new entity. `app-is-the-entity-2026-08-20.md` retired "project" as a
peer of App and said an App is the one container the operator names. A
"project" in the new model is the build view of an app. Development is an
App.

### 2.1 What kind of App

The entity model (`app` table, §4.1 of `app-is-the-entity-2026-08-20.md`)
has a `kind` field: `'app' | 'agent'`. Development needs a third value or
a boolean flag. The simplest addition is a `dev_only` boolean on the `app`
record, defaulting to `false`. A `dev_only` app is:

- visible only when `dev_mode_on()` is true (see `wsplatform/dev_mode.py`)
- excluded from `GET /apps` on a production installation
- excluded from the app catalog emitted to any chat's prompt
- prevented from running tickets against any working tree by the existing
  `assert_run_target` guard in `wsplatform/dev_mode.py`

The development app's record in the `app` table would be:

```
slug:      development
name:      Workshop (development)
kind:      app
dev_only:  true
repo_id:   <the platform repo row>
parent_id: null   (root, not nested under anything)
```

Its scope key stays `development`. Scope becomes derived from `app_id`
under the migration in `app-is-the-entity-2026-08-20.md` §4.3, so
`development` resolves to this row and the five alternate spellings
(`project:development`, `app:development`, etc.) all collapse to it.

### 2.2 Changes in scope resolution

Currently `scope_tree.py` treats `development` as a special case hardwired
into `TREE_KINDS`, `DEVELOPMENT_SCOPE`, and `parse()`. After the migration:

- `DEVELOPMENT_SCOPE` stays as a constant (it is used in 30+ call sites
  and changing the key itself would migrate data)
- `parse("development")` goes from a hard-coded branch to a lookup in the
  `app` table, gated on `dev_mode_on()`
- `TREE_KINDS` loses `"development"` as a literal; "development" resolves
  as `kind == "app"` with `dev_only == True`
- `RELATIONAL_SCOPES = ("development", "workshop")` in `task_scope.py`
  loses `"development"`; the development app's ticket queue is an app queue,
  not a root-level relational one

Nothing moves in the data. The scope key `development` still exists; it
just resolves through the app table instead of a hard-coded branch.

### 2.3 Changes in the app registry

Today the development app is not in `installed-apps.json` at all. It is
assembled in `chat_context.py` by the `DevelopmentChatProvider` class
(line ~1995), which reads the self-project from `.runtime/projects.json`
and hard-codes the context key to `"development"`.

After migration:

- The development app is a row in the `app` table with `dev_only = True`
- `installed-apps.json` (and later the `app` table) carries it, gated
  behind dev mode at read time, not at write time
- `DevelopmentChatProvider` becomes a regular `AppChatProvider` that reads
  from the app record; the special class is deleted

The `self: true` flag in `.runtime/projects.json` (currently 9 references
in `dev_mode.py`, `task_scope.py`, `chat_context.py`) is superseded by
the `dev_only` field on the app record.

### 2.4 Changes to the four prompt layers

The four layers are L0 (kernel), L1 (orientation manifest), L2 (scope/app
persona), L3 (operator custom instructions).

**L1 (orientation.py, line 26-27):** currently has a hard-coded branch:
```python
if key == "development":
    return "This chat is the development scope: it works on the Workshop platform itself."
```
This is an installation fact in a platform layer, which `check_prompt_layers.py`
now flags. After migration, the development app's L2 persona carries this
description in the app record's persona field (or in an `AGENTS.md` in the
development scope directory), and L1's branch is deleted.

**L2 (`chat_context.py`, `DevelopmentChatProvider`):** already scoped to
the development context. The persona text it assembles (the long prompt
block the operator sees as "Project-local agent rules") should stay at L2
for the development app. After migration it is the development app's persona,
loaded from the app record or `AGENTS.md`, not from a hard-coded Python class.

**L0 and L3:** no changes. L0 is installation-agnostic by design. L3 is the
operator's own custom instructions per scope.

---

## 3. What is dev-only today and ships in the tracked tree

This is the real deliverable. Every item below is verified with `git ls-files`
and is present in what a `git clone` hands anyone. "Ships in production" is
defined as "present in a fresh clone."

### 3.1 Platform quality gates (scripts/)

These exist to enforce correctness of the platform source itself. They have
no meaning to an operator who is not developing Workshop.

```
scripts/check_design.sh
scripts/check_dev_bundle.sh
scripts/check_duplicate_helpers.py
scripts/check_fresh_clone.sh
scripts/check_frontend_state.py
scripts/check_generated_client.py
scripts/check_module_layout.py
scripts/check_no_app_names.py
scripts/check_no_hardcoding.sh
scripts/check_no_process_globals.py
scripts/check_no_raw_paths.py
scripts/check_no_raw_storage.py
scripts/check_no_state_in_source.py
scripts/check_one_definition.py
scripts/check_opaque_fields.py
scripts/check_pluggable_apps.sh
scripts/check_prompt_layers.py
scripts/check_ratchets_not_loosened.py
scripts/check_response_model.py
scripts/check_routes_answer.py
scripts/check_undefined_css.py
scripts/dev_mode_gate.sh
```

22 files. All gate the platform's own source quality. None are read by the
running app; they are invoked manually or by `reviewer.sh`. A fresh clone
includes them and they pass on a clean tree.

### 3.2 Autonomous worker runner and completion gate (scripts/)

```
scripts/ralph.sh          (the autonomous loop runner)
scripts/project_ralph.sh  (project-scoped variant)
scripts/reviewer.sh       (the completion gate)
```

These implement the development loop: ralph submits tickets, reviewer
approves or rejects them. Both are meaningless without the development
project's ticket queue and the platform repo as the run target.

### 3.3 Dev scope handling in platform code

These are platform source files that contain hard-coded branches for
`"development"`:

```
backend/wsplatform/dev_mode.py            (the predicate itself)
backend/services/scope_tree.py            (DEVELOPMENT_SCOPE, TREE_KINDS)
backend/services/task_scope.py            (RELATIONAL_SCOPES, 3 branches)
backend/services/chat_context.py          (DevelopmentChatProvider, ~50 lines)
backend/services/orientation.py           (L1 branch for key == "development")
frontend/src/components/chat/chatScope.ts (ScopeKind "development", 4 branches)
frontend/src/lib/scopePickup.ts           (development scope pickup, 2 branches)
frontend/src/components/chat/uiCardActions.ts (dev scope in card dispatch)
frontend/src/lib/chatSelection.ts         (default scope in developer build)
frontend/src/pages/memory/MemoryTree.tsx  (dev scope display)
```

These ship in every clone and run in every installation. They are the
largest class of violation: platform code that knows specifically about
development. They are not excluded by any current gate.

### 3.4 Tests for the development scope

```
backend/tests/test_development_chat_scope.py
backend/tests/test_one_scope_mapping.py     (33 assertions on "development")
backend/tests/test_chat_work_read.py        (8 assertions on "development")
backend/tests/test_auto_merge.py            (1 assertion)
backend/tests/test_scope_root_and_state_paths.py
```

These test the dev-scope behavior. They run in CI for every installation
even though the behavior they test is only relevant when dev mode is on.

### 3.5 The knowledge store for the development scope

```
shared/knowledge/development/   (30 tracked .md files)
shared/knowledge/development/MEMORY.md
```

These are notes the development chat writes during development work. They
include debugging findings, design decisions, and build gotchas — all
specific to building Workshop itself. They ship in every clone.

### 3.6 The design docs for developer mode

```
docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md
```

This is a design document about the development scope. It ships in every
clone. It is the only one of the docs/ files that is exclusively about
the development workflow rather than about the platform model.

### 3.7 Summary counts (from git ls-files)

| Category | File count |
|---|---|
| Quality gates (scripts/check_*, scripts/reviewer.sh) | 25 |
| Autonomous runner (scripts/ralph.sh, project_ralph.sh) | 2 |
| Dev scope branches in platform source | 10 |
| Dev-scope tests | 5 |
| Development knowledge notes | 31 |
| Dev-mode design doc | 1 |
| **Total** | **74** |

None of these are excluded from a fresh clone. `check_fresh_clone.sh` does
not assert their absence (assertions A4 and A5 are not yet implemented).

---

## 4. The production exclusion mechanism

### 4.1 What "serve in production" means

Three possible readings, narrowest to broadest:

- **Runtime catalog:** the dev app does not appear in `GET /apps` on a
  production install.
- **Backend startup:** dev-scope routes and handlers are not registered.
- **Clone contents:** a production clone contains none of the 74 files above.

The narrowest is already partially implemented (`dev_mode_on()` gates the
scope). The broadest is the real ask: the operator said "nothing of the
development app/code/capability should ever serve in production", and
that reads as the clone-level guarantee.

Recommendation: aim for the catalog guarantee first, measure it, then
decide on the clone guarantee separately (see §5).

### 4.2 Recommended mechanism: `dev_only` flag on the app record, with build exclusion

**Step 1 — runtime gate (already mostly exists).**
The `dev_only` flag on the app record, plus `dev_mode_on()`, ensures
the development app and its scope are invisible at runtime on a production
install. This closes the catalog and route-registration surfaces. The
existing `wsplatform/dev_mode.py` guards (`assert_run_target`,
`is_self_dir`) already protect the run surface.

**Step 2 — build-time exclusion of the gate scripts and knowledge.**
The platform quality gates (`scripts/check_*.sh`, `scripts/reviewer.sh`,
`scripts/ralph.sh`) and the development knowledge notes
(`shared/knowledge/development/`) do not need to be in a production clone.
They are not served (no route serves them, no import includes them), but
they are present and increase the clone size and surface area.

Mechanism: a `.gitattributes` export-ignore rule for the dev-only paths,
triggered by `git archive` (what a release tarball uses). This excludes
them from a produced archive without removing them from a developer's
clone. Example:
```
scripts/check_*.sh export-ignore
scripts/ralph.sh export-ignore
scripts/reviewer.sh export-ignore
shared/knowledge/development/ export-ignore
```

This does NOT help with a plain `git clone`, which always includes all
tracked files. If the guarantee is "a production operator who runs
`git clone` also receives no development tooling," the correct mechanism
is a separate repo (or a separate branch) for development-only content,
not `.gitattributes`.

### 4.3 How to measure the exclusion

The lesson from `check_no_app_names.py` and `check_fresh_clone.sh` is
that a sentence or a count is not evidence; a running assertion is.

The measurement for the catalog guarantee is an extension of
`check_fresh_clone.sh` assertion A5 (currently "NOT IMPLEMENTED"):

> Boot the backend against a throwaway Postgres cluster with dev mode
> off and assert that `GET /apps` returns an empty list with no
> development entry.

This is a real test: it instantiates the app, hits the real endpoint,
and checks the real response. No mock, no inference from code structure.

For the code-in-clone guarantee, the measurement is assertion A4
(currently "NOT IMPLEMENTED"):

> Clone the repo, delete any files in the `dev_only` category, run
> `npm ci && npm run build`, and assert the build succeeds with zero
> imports of development-scoped modules.

Until A4 and A5 are implemented, no claim that the production exclusion
is done should be accepted.

---

## 5. Questions that are the operator's to answer

These are design decisions the doc does not resolve, because each has a
real tradeoff the operator should choose.

**5.1 Does "serve in production" mean the catalog, or also the clone?**

The catalog guarantee (dev app absent from `GET /apps` on prod) is
implementable now and measurable with A5. The clone guarantee (prod
operator receives none of the 74 files) requires either a separate repo,
a separate branch for dev-only content, or accepting that the files are
present but inert. The tradeoff: a separate repo costs a second clone in
the bootstrap and a maintained boundary; `.gitattributes` closes archives
but not clones; accepting inert files costs nothing but leaves dev tooling
on prod machines.

**5.2 Should `shared/knowledge/development/` be excluded?**

The 31 knowledge notes are data, not code. They have no production
surface: no route serves them, no startup process reads them. But they
contain the development project's institutional memory. If a production
operator clones the repo, they receive build gotchas and design decisions
that belong to the development chat. The question is whether that is an
acceptable leak or a clean-up item.

**5.3 Should the platform quality gates ship to a production clone?**

`scripts/check_no_app_names.py`, `scripts/reviewer.sh`, etc. are
genuinely useful to anyone who runs the platform and wants to maintain its
own quality standards. They are not specific to this installation's apps.
An argument exists that they belong in the repo as tools a production
operator can use. The counter-argument is that they only make sense if the
operator is also developing the platform, which is a different population.
The answer determines whether these go under `export-ignore` or stay.

**5.4 Does the development app get its own dedicated repo eventually?**

The entity model says an app has a repo. The development app's repo is the
platform repo itself (`dev_only` app with the self-pointing `repo_id`). That
is coherent with the model but unusual: one app "owns" the code for all the
others. Is that the permanent design, or is the expectation that Workshop
platform development eventually lives in a separate repo that an operator
checks out alongside the platform, the same way the birds rig has its own
checkout?
