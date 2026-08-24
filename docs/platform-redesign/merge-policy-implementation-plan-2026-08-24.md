# Merge policy: the implementation plan

**Status: current. 2026-08-24.** The executable half of
[`merge-policy-levels-2026-08-24.md`](merge-policy-levels-2026-08-24.md), which
decided the design. This document is what to build, in what order, against the
code as it actually is. It also corrects three things the design doc states
more strongly than the tree supports, and names ten gaps the design did not
cover.

Read the design doc first for WHY. This one is only HOW.

---

## 1. The decision path, end to end, as it really is

A worker finishes. Nine steps later a change has merged or it has not. Every
line number below was read on 2026-08-24.

1. **The worker finishes a ticket in worktree mode.** `scripts/project_ralph.sh`
   reaches `publish_or_park()` (`:463`). On the supervised, local-only leg the
   branch is deliberately NOT merged.

2. **A change record is opened.** `open_change_for_task` calls
   `services/work_review.open_for_task`, which calls `changes.open_change(...,
   status="ready")`. `ready` matters: `auto_merge.MERGEABLE_STATUSES` is
   `("ready", "approved")`, so a change is eligible from the instant it exists.

3. **The reviewer's gates are written.** `record_change_gates` (`project_ralph.sh:414`)
   runs `python3 -m services.change_gates --change-id <id>` over the reviewer
   receipt. Independently, `scripts/reviewer.sh:1050-1064` runs the same module
   whenever `REVIEWER_CHANGE_ID`, or `REVIEWER_GATE_PROJECT` plus
   `REVIEWER_GATE_BRANCH`, is set in its environment.

4. **`change_gates.record_reviewer_run` merges the gate entries onto the row**
   (`change_gates.py:386`), then calls `_consider_auto_merge(written["id"])`
   (`:392`).

5. **`_consider_auto_merge` calls `auto_merge.consider(change_id,
   GATE_NAMES, claim_check.ADVISORY_GATES, trigger="reviewer gates recorded")`**
   (`change_gates.py:421-423`), inside a bare `try/except` that swallows
   everything. Recording a verdict may never change it.

6. **`auto_merge.consider` re-reads the change and calls `assess`**
   (`auto_merge.py:534`). `assess` evaluates five conditions, each wrapped in
   `_safely` so an exception becomes a failed condition:
   - `open_for_merge`: status is `ready` or `approved`.
   - `names_a_branch`: the change names a project and a branch.
   - `pre_approved`: `change.auto_merge` is set, OR `merge_policy.allows_auto_merge(scope_for(change))`.
   - `gates_complete`: every name in `GATE_NAMES` that is not advisory is
     PRESENT on the row. Absent is not green, and an empty required set is a
     refusal.
   - `gates_green`: among non advisory gates, no `fail`, no `running`, no
     unrecognised status, and at least one `pass`. `skipped` neither blocks nor
     counts.

7. **Only if all five hold**, `branch_ready` runs: the branch resolves, is ahead
   of its base, and its head sha still equals `change.head_sha`. A branch that
   moved means the recorded gates judged other code, so it refuses.

8. **Only if all six hold AND the authority is the scope** (not a per change
   pre-approval), and only if `merge_policy.requires_low_risk(scope)`, the
   `low_risk` classifier runs (`is_low_risk`, `auto_merge.py:441`): a non empty
   diff, at most `LOW_RISK_MAX_FILES` files, every file `A` or `M`, no
   dependency manifest, no migration, no secret or auth path, at most
   `LOW_RISK_MAX_CHANGED_LINES` changed lines, and every path inside the
   declared `touches` of every ticket the change carries.

9. **If every condition holds**, `consider` calls
   `work_review.merge_change(change, merged_by=actor, basis=...)`
   (`auto_merge.py:571`), which pins the base sha, runs
   `worktrees.merge_branch`, and records `mark_merged(..., merged_by,
   merge_basis)`. A conflict arrives as `MergeRefused` and is returned as
   `{"merged": False, "reason": "<git's words>"}`. `consider` never raises.

The second entry point is the operator arming a change by hand:
`PATCH /api/changes/{id}` with `auto_merge: true` runs the identical
`auto_merge.consider` on the row as patched (`routes/changes.py:277`), because
the gates may already have been green for an hour.

### 1.1 Where the design doc overstates the tree

- **"`change_gates.py:423` calls `auto_merge.consider(...)` on every reviewer
  run" is too strong.** It is called on every reviewer run **that is addressed
  to a change**. `reviewer.sh` only invokes the module when `REVIEWER_CHANGE_ID`
  or the project/branch pair is in its environment (`reviewer.sh:1050`), so a
  hand run `bash scripts/reviewer.sh` records nothing and considers nothing.
  Inside `record_reviewer_run` there are four earlier returns
  (`change_gates.py:357`, `:378`, `:382`, `:389`) that all bypass
  `_consider_auto_merge`. The accurate sentence is: **the chain is live on
  every sweep leg that opens a change, and on every reviewer run the caller
  addressed to one.** That is enough for the design's argument, but the plan
  should not repeat the stronger claim.

- **"All 3 live changes carry recorded gates" is not verifiable from source.**
  It is a database observation. Re-measure it before quoting it (section 7 has
  the query). Nothing in this plan depends on it.

- **"Delete `auto_when_green` and its branch" implies there is a branch. There
  is not.** No code anywhere reads the literal `"auto_when_green"`. The two
  predicates are `allows_auto_merge` (`policy != DEFAULT_POLICY`) and
  `requires_low_risk` (`policy == "auto_when_green_and_low_risk"`). Deleting
  the middle value removes one string from one tuple and makes those two
  predicates logically identical. That is a smaller change than the doc
  suggests, and it has a consequence worth stating (gap G2).

### 1.2 Nothing changes on day one, and here is what guarantees it

Shipping all three parts changes zero merge behaviour, for four independent
reasons, any one of which is sufficient:

1. **The store does not exist.** `merge_policy._store_path()` points at
   `.runtime/merge-policy.json`. It is not in the tree, it is not created by any
   installer, and `.runtime/` is gitignored. `_read()` returns `{}` on
   `FileNotFoundError`, so every scope reads `manual`.
2. **The deployment default ships absent too.** Part 2 adds
   `.runtime/merge-defaults.json`, also not created, also read fail closed to
   `manual`. Configuration that does not exist cannot raise the floor.
3. **`_pre_approved` fails first for every change.** `change.auto_merge`
   defaults to `False` in the column and in `_shape` (`changes.py:135`), and no
   sweep, route or script sets it. So the third condition refuses before any
   gate is even counted.
4. **The one surviving policy value requires the classifier.** After part 1 the
   only non manual value is `auto_when_green_and_low_risk`, so arming a scope
   still cannot merge anything that is not small, in bounds and green.

The regression test that pins this already exists and must keep passing
unchanged: `test_merge_policy.py::test_the_shipped_tree_has_no_scope_configured`
and `test_auto_merge.py::test_nothing_in_this_tree_arms_a_scope`.

---

## 2. The commit sequence

Five commits, in this order. Each is independently revertable and each leaves
the tree green.

### Commit 1: delete `auto_when_green`

Backend only. No new behaviour, one value fewer.

1. `backend/services/merge_policy.py`
   - `POLICIES = ("manual", "auto_when_green_and_low_risk")`.
   - Add `FAILSAFE_POLICY = "manual"` beside it and use it, not
     `DEFAULT_POLICY`, everywhere a comparison means "is this the safe answer".
     This is preparation for commit 2 and is explained in gap G1. In commit 1
     the two constants are equal, so this is a pure rename with no behaviour
     change.
   - `allows_auto_merge` becomes `get_policy(scope) != FAILSAFE_POLICY`.
   - `configured_scopes` filters on `v != FAILSAFE_POLICY`.
   - `set_policy` drops the key when `policy == FAILSAFE_POLICY`.
   - Rewrite the docstring's policy table to two rows and delete the paragraph
     that says the open question is open. It is answered.
2. **Migration of a stored deleted value.** `get_policy` already answers
   `DEFAULT_POLICY` and logs a warning for any value not in `POLICIES`
   (`merge_policy.py:111-116`), so a file still carrying `auto_when_green`
   reads as `manual` and says so the moment this lands. That is the required
   behaviour and it needs no code. What it needs is a **named test** and a
   **sharper log line**: change the warning to name the retired value
   explicitly when it is the one this build deleted, so the log reads as a
   migration notice rather than as corruption. Never silently rewrite the file
   on read, and never fail the read.
3. `backend/services/auto_merge.py`: docstring only (`:15`, `:45`, `:50`).
   Delete the sentence offering to delete the whole low risk branch if the
   operator says no; that decision is made.
4. `backend/routes/autonomy.py:397`: the `Field(description=...)` becomes
   `"manual | auto_when_green_and_low_risk"`. `:459` docstring reworded.
5. Regenerate the client: `npm run gen:api-types`. `schema.gen.ts` changes at
   `:153` and `:14789`. This is not optional;
   `scripts/check_generated_client.py` is an absolute gate and fires on
   docstring changes too.
6. Tests, listed exactly in section 3.

### Commit 2: the deployment default tier

`merge_policy.DEFAULT_POLICY` stops being a constant and becomes a read.

**Resolution order, in one sentence:** a scope's policy is the value stored for
that scope if it has one, otherwise the deployment default, otherwise `manual`.

Store, mirroring `services/autonomy_override.py` (a single dict at rest) rather
than reusing the per scope file:

    .runtime/merge-defaults.json   {"policy": "auto_when_green_and_low_risk"}

Why a second file and not a reserved key in `merge-policy.json`: every helper in
`merge_policy` treats every key in that file as a scope, so a reserved key would
have to be filtered out of `_read()`, `get_policy`, `set_policy` and
`configured_scopes` independently, and the one that got missed would report
`_defaults` as a configured scope on the settings screen. A separate file has no
such trap and no migration of the existing one.

API in `merge_policy.py`:

```
FAILSAFE_POLICY = "manual"          # never configurable, the floor

def default_policy() -> str         # the deployment default, FAILSAFE on any doubt
def set_default_policy(p) -> str    # drops the key when p == FAILSAFE_POLICY
```

- **Fail closed on every read, all four ways.** Absent file, unreadable file
  (any `OSError`), malformed JSON, not an object, missing key, non string value,
  value not in `POLICIES`: every one answers `FAILSAFE_POLICY` and logs at
  warning. Copy `_read`'s structure from lines 66 to 90 verbatim; do not write a
  second, looser reader.
- **Drop the key on default.** `set_default_policy("manual")` removes `policy`
  from the object and writes the remainder, so the file is only ever a record of
  a deliberate departure, exactly as `set_policy` and `scope_execution.set_mode`
  do.
- `get_policy(scope)` becomes: stored value if it is in `POLICIES`, else warn if
  a value was present, else `default_policy()`.
- `DEFAULT_POLICY` is **deleted as a public name**, not repointed. It is in
  `__all__` and is read by `auto_merge._pre_approved` (`:242`) for a log
  message. Leaving a constant named DEFAULT that no longer means the default is
  how the next reader gets it wrong. Replace the one live read with
  `merge_policy.default_policy()`.

Routes: add to `routes/autonomy.py`

    GET  /agents/autonomy/merge-policy/default   -> {policy, is_failsafe}
    POST /agents/autonomy/merge-policy/default   -> the same

Both `response_model=`, per `check_response_model.py`. Regenerate the client.

**Do not add:** a ceiling, a per agent tier, precedence weights. All three are
rejected in the design and two of them are multi tenant guardrails in a single
operator system.

### Commit 3: the enumerating endpoint and the threshold store

The settings screen needs two things that do not exist yet. They are backend
work and belong in their own commit so the UI commit is purely a UI commit.

1. **`GET /agents/autonomy/merge-policies`** returning the deployment default,
   every scope that has a task store, that scope's effective policy, and whether
   it differs from the default. Build the scope list from the same enumeration
   `GET /tasks/scopes` uses (`routes/tasks_all.py:469`), so the settings screen
   and the ticket filters cannot disagree about which scopes exist.

   ```
   { "default_policy": "manual",
     "failsafe_policy": "manual",
     "scopes": [ {"scope": "development", "policy": "manual",
                  "source": "default" | "scope", "differs": false} ],
     "orphans": ["project:typo"] }
   ```

   `orphans` is any key in the store that no longer resolves to a scope. It
   exists because the POST route accepts them today (gap G9) and the operator
   needs to be able to see and clear one.

2. **Make the two thresholds configuration.** They are module constants today
   (`auto_merge.py:100-101`) and the settings screen is required to render them
   as editable numbers. Put them in the same `.runtime/merge-defaults.json`:

   ```
   {"policy": ..., "low_risk_max_files": 5, "low_risk_max_changed_lines": 100}
   ```

   - Reader lives in `merge_policy` (it is the settings module) and is called by
     `auto_merge.is_low_risk` at classification time, not at import time.
   - The compiled in `LOW_RISK_MAX_FILES = 5` and
     `LOW_RISK_MAX_CHANGED_LINES = 100` stay exactly where they are and become
     the fallback: absent, unreadable, malformed, non integer, zero or negative
     all read as the compiled value. A threshold is the one setting where "fail
     closed" means "fall back to the tighter number", so clamp on the low side
     only and let the operator raise it if he chooses.
   - Keep them exported from `auto_merge` under their current names so the
     existing tests that read `auto_merge.LOW_RISK_MAX_FILES` keep working.
   - Endpoint: fold into the same `POST /agents/autonomy/merge-policy/default`
     body as optional fields, so one screen writes one file in one call.

### Commit 4: the Settings screen

**Where it lives.** A new tab in the existing tabbed Settings, not a new page
and not a section bolted onto `AutonomyControls`.

- `frontend/src/pages/Settings.tsx`: add `"merging"` to the `SettingsTab` union
  and to `TAB_TITLE` as `"Merging"`, and render `<MergePolicySection />` for it.
- `frontend/src/App.tsx`: `<Route path="/settings/merging" element={<Settings tab="merging" />} />`.
- `frontend/src/components/shell/places.ts`: one page row in the `settings`
  place, `{ id: "merging", label: "Merging", to: "/settings/merging", Icon: icons.??? }`,
  placed immediately after `agents`. Add a `merging` entry to the shell icon map
  (`GitMerge` from lucide) rather than reusing another tab's icon.
- The component itself: `frontend/src/pages/settings/MergePolicy.tsx`, lazy
  loaded like its neighbours.

Why a sibling of Agents rather than a row inside it: `AutonomyControls` answers
"may unattended work RUN", this answers "may finished work LAND". The backend
keeps those apart deliberately (`routes/autonomy.py:385-390`) and a screen that
merges them would undo the distinction the whole design rests on.

**What it shows, top to bottom.**

1. `PageHeader` (supplied by `Settings.tsx`) then a `SectionLabel` "Deployment
   default".
2. **The choice, as two labelled states, never as enum values.** `Segmented`
   with exactly two options:
   - **"I review everything"** (`manual`)
   - **"I review anything that is not small and boring"** (`auto_when_green_and_low_risk`)

   Beside the second option, the classifier's real conditions in plain words.
   Not a paraphrase, the actual list, because this is the sentence that makes
   the setting honest:

   > Small and boring means all of these, and anything it cannot classify is
   > not small and boring: every reviewer check recorded and passing, no check
   > still running, the change carries a ticket and every changed path is inside
   > that ticket's declared touches, at most N files added or modified (nothing
   > deleted or renamed), at most M changed lines, no dependency manifest, no
   > database migration, no secret or auth path. The claim to diff check is
   > advisory and is not counted either way.

3. **The two thresholds as editable numbers.** Two `Field` plus `Input
   type="number"` rows, labelled "Most files a small change may touch" and "Most
   changed lines a small change may have", inside the same edit mode as the
   policy above.
4. `SectionLabel` "Scopes", then a list, one row per scope: the scope key, its
   effective policy as a `StatusPill` (`ok` for manual, `warn` for the auto
   value, because auto is the state that deserves a second look), a `Tag`
   reading "default" or "set here" for the source, and a two option `Segmented`
   to change it. Rows that differ from the deployment default sort first and
   carry the "set here" tag; that IS the "which scopes differ" answer, so it
   needs no separate list.
5. An `orphans` block, rendered only when non empty: a `Notice tone="warn"`
   naming each stored scope key that no longer resolves, with a Clear action.
   A policy set on a key nothing resolves to is a control that lies, and the
   screen is where that becomes visible.

**Design system compliance, non negotiable, per `frontend/CLAUDE.md`.**

- Kit primitives from `@workshop/ui` by package name: `SettingsSection` /
  `SettingsRow` (the pattern `Settings.tsx` already uses), `Segmented`, `Field`,
  `Input`, `StatusPill`, `Tag`, `SectionLabel`, `Notice`, `StateBlock`,
  `Skeleton`, `ActionBar`, `Button`. No hand written `.ws-*` markup.
- **Tokens, never hex.** `var(--muted)`, `var(--faint)` for the explanatory
  prose. `check_design.sh` fails the build on a raw hex in a changed `.tsx`.
- **lucide only, never emoji.** `GitMerge` for the tab, `ShieldCheck` beside the
  manual option, `Zap` beside the auto option. 14px in section labels, 16px in
  buttons, stroke 1.8.
- **`useEditMode` plus a sticky `ActionBar`.** This writes config, so it commits
  on explicit Save with Cancel as a ghost button and an "unsaved changes" pill
  while dirty. Do NOT commit on change: the Memory tab's commit on change is
  correct there because nothing existing is at risk, and here the risk is
  precisely that code lands without a person.
- **All four list states on the scope list.** loading: `Skeleton` rows. empty:
  `StateBlock` with an instructional hint ("No scope has a task store yet. File
  a ticket in a project to create one."). error: `StateBlock variant="error"`
  naming `/api/agents/autonomy/merge-policies` with a Retry. overflow: the list
  is bounded by the number of scopes, so `TableScroll` is unnecessary; if it
  ever is not, paginate.
- A failure beside content that is still on screen (a rejected Save) is a
  `Notice` above the ActionBar, not a `StateBlock`.
- **No em-dashes in any string the operator can read.** `check_design.sh`
  enforces this on changed `.tsx`.
- Dark is the default theme; check both.

### Commit 5: close ticket cb311b87

Design doc section 2 records it as stale: the work it blocks on ("attach the
machine gate results to the change") shipped as C3 on 2026-08-23. Close it with
a note pointing at `services/change_gates.py`. One line, no code.

---

## 3. Every call site of the deletion

**Ten non-test locations across five files, plus fourteen lines across two test
files: twenty four lines in seven files.** Not one of them is a code branch: the literal `"auto_when_green"` appears in exactly
one executable expression in the whole tree, the `POLICIES` tuple.

### Backend, executable (1)

| file:line | what it is | change |
|---|---|---|
| `backend/services/merge_policy.py:50` | `POLICIES` tuple | drop the middle element |

### Backend, prose that ships in the API schema (2)

| file:line | what it is | change |
|---|---|---|
| `backend/routes/autonomy.py:397` | `ScopeMergePolicy.policy` Field description | two values |
| `backend/routes/autonomy.py:459` | `get_merge_policy` docstring | reword, the open question is closed |

Both of these reach `frontend/src/api/schema.gen.ts`, so both force a
regeneration.

### Backend, docstrings (5)

| file:line | change |
|---|---|
| `backend/services/merge_policy.py:21` | delete the `auto_when_green` table row |
| `backend/services/merge_policy.py:22` | keep, reword as the only non manual value |
| `backend/services/merge_policy.py:24-32` | the "open question" paragraph is now answered |
| `backend/services/auto_merge.py:15` | the two knob summary lists three values |
| `backend/services/auto_merge.py:45-50` | the "if the operator answers no, delete the branch" paragraph |

### Generated client (2, do not hand edit)

| file:line | change |
|---|---|
| `frontend/src/api/schema.gen.ts:153` | JSDoc copied from the route docstring |
| `frontend/src/api/schema.gen.ts:14789` | JSDoc copied from the Field description |

Regenerate with `npm run gen:api-types`. `scripts/check_generated_client.py`
compares byte for byte against a fresh generation and is an absolute gate, not a
ratchet.

### Tests (9 lines in `test_merge_policy.py`, 5 in `test_auto_merge.py`)

`backend/tests/test_merge_policy.py`: lines 64, 99, 100, 115, 129, 133, 136.
`backend/tests/test_auto_merge.py`: lines 227, 242, 259, 272, 280.

### Documents (historical, do NOT edit)

`docs/platform-redesign/changes-and-review-2026-08-22.md:153-154, 271, 280` and
`HANDOFF-branch-isolation-2026-08-22.md:367` and
`agents-and-memory-reassessment-2026-08-23.md:252` all name the value. They are
dated records of what was decided then. The supersession is carried by
`merge-policy-levels-2026-08-24.md` and by this file. Leave them alone.

### How many tests must change: ten functions, and two of them need new fixtures

**`backend/tests/test_merge_policy.py`, five functions, all one word
substitutions except the last:**

1. `test_a_policy_file_that_is_not_an_object_reads_as_manual` (`:64`) : the
   value appears inside a JSON payload string. Substitute.
2. `test_setting_a_policy_round_trips_and_is_scoped` (`:99-100`) : substitute.
3. `test_setting_it_back_to_manual_removes_the_key` (`:115`) : substitute.
4. `test_a_policy_needs_a_scope` (`:129`) : substitute.
5. `test_configured_scopes_lists_only_deliberate_choices` (`:133-137`) : it sets
   two scopes to two different non default values and there is now only one.
   Rewrite to two scopes carrying the same value.

**`backend/tests/test_auto_merge.py`, five functions, three trivial and two
that need the low risk fixtures:**

6. `test_a_scope_policy_merge_is_recorded_as_the_scope_and_not_the_person`
   (`:227`) asserts `merged is True` on the scope's authority. Under the
   surviving value the classifier now runs, and the default `_Changes` double
   returns an empty diff, which is not low risk. Add
   `diff=_small_diff(), tasks=_Tasks(_ticket())` to the `wired(...)` call, the
   same fixtures `test_a_small_in_bounds_change_is_low_risk` (`:605`) already
   uses.
7. `test_the_scope_key_is_the_one_the_operator_can_set` (`:242`) : same, plus
   the existing `task_scope=` override.
8. `test_a_scope_that_does_not_resolve_has_no_policy` (`:259`) : asserts
   `merged is False` and still will. Substitute only.
9. `test_an_unregistered_project_has_no_policy` (`:272`) : same, substitute only.
10. `test_a_change_flag_outranks_a_scope_policy_in_the_record` (`:280`) : merges
    on the change flag, so `assess` skips the classifier by design
    (`auto_merge.py:519-524`). Substitute only.

**New tests, roughly fourteen, and these are the ones that carry the value:**

- `test_a_stored_retired_value_reads_as_manual_and_says_so` (caplog assertion on
  the warning; this is the migration behaviour and it must be pinned by name).
- `test_the_retired_value_cannot_be_SET` (extend the existing
  `test_an_unknown_policy_cannot_be_SET`).
- Deployment default: absent file, unreadable file, malformed JSON, non object,
  missing key, non string value, unknown value. Seven, each answering `manual`.
- `test_a_scope_with_no_entry_inherits_the_deployment_default`.
- `test_a_scope_with_an_entry_ignores_the_deployment_default`.
- `test_setting_the_default_back_to_manual_drops_the_key`.
- `test_raising_the_deployment_default_does_not_hide_a_configured_scope`
  (this is gap G1 made mechanical).
- Thresholds: absent reads the compiled constant, a zero or negative reads the
  compiled constant, a raised value is honoured.

Frontend: `frontend/src/pages/settings/MergePolicy.tsx` has no DOM harness
available (`cd frontend && npm test` is vitest over pure logic modules only), so
extract the sort-and-label logic into `frontend/src/lib/mergePolicy.ts` and test
that, the way `lib/autonomy.ts` and `lib/scopePickup.ts` already do.

---

## 4. What the design missed

Ten gaps, each with a recommendation. G1 is the one that would have shipped a
bug.

### G1. The new tier silently breaks three functions that compare against `DEFAULT_POLICY`

**The defect.** `allows_auto_merge` is `get_policy(scope) != DEFAULT_POLICY`
(`merge_policy.py:139`). `configured_scopes` filters `v != DEFAULT_POLICY`
(`:157`). `set_policy` drops the key when `policy == DEFAULT_POLICY` (`:127`).
All three are correct only while `DEFAULT_POLICY` is the constant `"manual"`.
The moment part 2 makes it a configured value, an operator who sets the
deployment default to `auto_when_green_and_low_risk` gets:

- `allows_auto_merge` returning **False** for every scope that inherits the
  default, so the tier he just turned on does nothing;
- and simultaneously **True** for a scope explicitly pinned to `manual`, because
  `"manual" != default` is now true. That is a fail OPEN, and it is the exact
  failure the module docstring promises can never happen.

**Recommendation.** Introduce `FAILSAFE_POLICY = "manual"`, a fixed constant
that is never configurable, and re-point all three comparisons at it in
**commit 1**, before the tier exists. Then delete the name `DEFAULT_POLICY`
entirely rather than repointing it at the reader, so no caller can keep a stale
mental model of it. Pin it with
`test_raising_the_deployment_default_does_not_hide_a_configured_scope`.

Related, and genuinely a design choice rather than a bug: after this,
`set_policy(scope, "manual")` drops the key, which means the scope now FOLLOWS
the deployment default rather than being pinned to manual. Recommend accepting
that (it keeps the file an audit list of deliberate departures, which is the
stated purpose) and making the settings screen say it: the per scope control's
manual option is labelled with the "default" tag when it matches, so the
operator can see that the row is inheriting rather than pinned.

### G2. Deleting the value deletes no branch, and collapses two predicates into one

After commit 1, `allows_auto_merge(s)` and `requires_low_risk(s)` are true under
exactly the same condition. `assess` asks both, in sequence
(`auto_merge.py:242` then `:522`).

**Recommendation.** Keep both. They ask different questions ("may the scope
authorise" and "does that authority demand the classifier") and the second will
diverge again the day a third value is added. But add
`test_the_two_predicates_agree_for_every_policy` iterating `POLICIES`, so if
someone later adds a value and updates only one of them, a test says so. Do not
collapse them into one function; that is the change that makes the next value
dangerous.

### G3. An auto-merged change leaves its ticket sitting in review, forever

**Verified.** `work_review.merge_change` (`:136-191`) merges the branch and
records the change row. It does not touch the ticket, and nothing else does
either: in `project_ralph.sh`, `publish_or_park` returns 0 and the CALLER then
performs the `in_review` transition, after `record_change_gates` has already
run and possibly merged. `work_review`'s own module docstring names this as a
known half finished piece ("the sweep still writes `in_review` itself ... doing
it properly means the lease refactor (B3)").

So under a scope policy: the code lands, and the ticket still queues for the
operator. The design's argument for the whole feature in section 6 is "a review
queue that grows faster than it drains turns the supervised ceiling from a
safety property into the bottleneck". **This feature as built does not drain
that queue.** It drains the merge backlog, which is a different backlog.

Accepting such a ticket afterwards is at least safe: `merge_for_task` calls
`change_for_task`, which skips changes in status `merged`, returns None, and the
acceptance proceeds as an ordinary status transition (`work_review.py:99-119`,
`routes/tasks_all.py:1193`). Nothing breaks. It is just manual.

**Recommendation.** Do not fix it in these five commits, it needs the lease
refactor. Do two cheaper things: (a) file a ticket "an auto-merged change should
complete its ticket" referencing B3, and (b) make the settings screen copy
honest, because the alternative is a control that overpromises. Add to the
second option's explanation: "The code lands by itself. The ticket still comes
to you to close."

### G4. Nothing checks the ticket's own status, so a change can merge under a worker

**Verified.** `MERGEABLE_STATUSES` (`auto_merge.py:96`) constrains the CHANGE's
status, not the ticket's. No condition in `assess` reads the ticket at all
except `_touches_clean`, which reads `touches` and never `status`.

The window: a change opened while a worker is still committing (`POST /changes`
is a public route, and `open_change` is idempotent for a branch), a reviewer run
recorded against it, gates green. `branch_ready`'s head sha check narrows this
considerably (the change must have been refreshed at the branch's current head),
but it does not close it, and the failure mode is a worker's half finished
branch landing on the trunk.

One mitigation arrives for free with commit 1: after the deletion, the only
scope authority is the one that runs `is_low_risk`, and `_touches_clean`
refuses a change carrying no ticket, a ticket that does not resolve, or a ticket
declaring no `touches` (`auto_merge.py:410-434`). So under a scope policy a
resolvable ticket is now always required. Only its STATUS is unchecked.

**Recommendation.** Add a seventh condition, `ticket_settled`, in commit 1
(it is small and it belongs with the deletion that makes it cheap): under
`scope_merge_policy` authority only, refuse when any carried ticket is in a
status that means work is still underway. Fail closed on a ticket that will not
resolve, as `_touches_clean` already does. Leave the per change path alone: a
person who armed a specific diff has decided about that diff.

### G5. `claim_check`'s verdict does not participate, and cannot

**Verified, in three independent ways, and all three are deliberate.**

1. `ADVISORY_GATES = (GATE_NAME,) = ("claim_to_diff",)`
   (`claim_check.py:88`). `_gates_green` drops advisory names from `considered`
   before counting (`auto_merge.py:329-330`), so a **failing** claim check
   cannot block a merge, and a **passing** one cannot be the "at least one
   passed" that qualifies it.
2. `claim_to_diff` is not in `GATE_NAMES` either (that tuple is `reviewer`,
   `no_new_stubs`, the eighteen `GATE_CHECKS` gates, `acceptance_criteria`), so
   the advisory filter inside `_gates_complete` is a no op today. It is there
   for the day an advisory gate does appear in the required set.
3. `record_claim_check` does **not** call `auto_merge.consider`
   (`claim_check.py:510-555`), and in `project_ralph.sh` it runs AFTER
   `record_change_gates` (`:484-485`). So at the moment the decision is made,
   the claim gate is usually not even on the row, and a failing claim check
   written a second later lands on an already merged change.

**Recommendation.** Change nothing in the mechanism. An advisory gate that could
block would be a judgement holding code hostage, which is what C6 explicitly
refused. But **say it on the settings screen**, in the classifier explanation:
"The claim to diff check is advisory and is not counted either way." An operator
who reads "every gate green" and then sees a red claim-to-diff row on a merged
change has been misled by the copy, not by the code.

### G6. "Every non-advisory gate green" is three separate mechanics, and the copy must not flatten them

For the record, so the screen's wording is accurate:

- **Complete**: every non advisory name in `GATE_NAMES` is PRESENT. Absent is
  not green. An empty required set is a refusal, not a vacuous pass.
- **Green**: among the present non advisory gates, no `fail`, no `running`, no
  status outside `GATE_STATUSES`, and at least one `pass`.
- **`skipped` neither blocks nor counts.** Most reviewer checks legitimately do
  not apply to a given diff, so blocking on skip would block everything; but a
  change whose every check skipped has zero passes and does not qualify.

**Recommendation.** The screen says "every reviewer check recorded and passing,
no check still running" rather than "all gates green". Three more words, and it
is the difference between a true statement and a slogan.

### G7. A conflict at merge time is handled correctly and is completely invisible

**Verified.** `work_review.merge_change` raises `MergeRefused` when
`worktrees.merge_branch` returns `ok: False`; `consider` catches it
(`auto_merge.py:576-582`) and returns `{"merged": False, "reason": "<git's
words>"}`. The branch is untouched, the change is untouched, nothing is retried
and nothing is forced. `test_a_conflict_parks_and_changes_nothing` pins it.

The gap is downstream. `_consider_auto_merge` **discards the returned dict**
(`change_gates.py:421-423`) and its caller is best effort by contract. So the
only trace of "this change tried to auto-merge and git refused" is one
`log.info` line in the backend log. It is not on the change, not in the inbox,
not on any screen. The operator's experience is that a change he armed simply
sits there.

**Recommendation.** Out of scope for these five commits, but file it, and note
that the cheapest honest fix is not a notification: it is to record the refusal
where the question gets asked. `merge_basis` cannot carry it
(`changes.update_change` refuses to write merge outcome fields, `changes.py:341-348`,
correctly). A change NOTE, or one `inbox.notify` item bounded by the existing
rule in `services/inbox.py`, are the two candidates. Decide when it is built,
not now.

### G8. `POST /agents/autonomy/merge-policy` does not validate the scope, despite saying it does

**Verified.** `_scope_root` (`routes/autonomy.py:406-408`) returns
`paths.workshop_root()` when `task_scope.root_for_context(scope)` answers None.
It never raises. Both the GET and the POST docstrings claim it "is what proves
the scope EXISTS (and passes the developer-mode gate)". It proves neither.

Consequence: `POST` with `scope: "project:typoo"` returns 200, writes the key,
and `configured_scopes()` lists it forever, while `auto_merge.scope_for` will
never produce that key so the policy governs nothing. This is precisely the
failure `merge_policy`'s docstring says the root-first key derivation exists to
prevent: "IT MUST PRODUCE THE KEY THE OPERATOR SET, or the control lies."

**Recommendation.** In commit 3, alongside the enumerating endpoint: make the
POST return 404 when `root_for_context` answers None, and correct both
docstrings. Watch the one case that looks like a regression and is not:
`development` resolves only with developer mode on, so refusing it with the flag
off is correct and the refusal should carry
`task_scope.dev_mode_refusal`'s wording rather than a bare "no such scope". The
enumerating endpoint's `orphans` field is what lets the operator see and clear
anything already written.

### G9. Nothing enumerates policies, so the screen the design asks for cannot be built

`GET /agents/autonomy/merge-policy` takes a single required `scope` query
parameter. `merge_policy.configured_scopes()` exists, is documented as "the
audit answer to what can land without me", and is called from nowhere. The
design's section 7 item 1 asks for a screen showing "every scope's policy, which
scopes differ from the default" and there is no endpoint that can answer it.
Covered by commit 3; named here because it is a design omission, not an
implementation detail.

### G10. After an auto-merge, the operator sees nothing that says it was one

**Verified.** `merged_by` and `merge_basis` are on the column
(`db/models.py:1310-1311`), on the service shape (`changes.py:138-139`) and on
the API response model (`routes/changes.py:107-108`). A grep across
`frontend/src` and `packages/ui/src` finds **zero** readers of either field.
`ChangeReview.tsx`'s merged card (`:380-391`) renders "Merged 2h ago as
abc1234. There is nothing left to decide." An auto-merge and an acceptance the
operator made himself are indistinguishable on screen.

The design's section 7 says notification is "not now" because "the merge record
already says who and why". The record does. The product does not.

**Recommendation.** Fold four lines into commit 4, since it is the UI commit
anyway: in `mergedCard`, when `change.merged_by` is `change_auto_merge` or
`scope_merge_policy`, name the actor in plain words ("Merged by itself, on your
standing policy for project:alpha" / "Merged by itself, because you pre-approved
this diff") and render `merge_basis` underneath as the reason. Add a
`Merged by` row to the rail `Props` beside `Merge commit`. That is what makes
"no notification needed" a defensible position rather than a gap.

---

## 5. Verification

Run from `/workspace`.

```bash
# 1. The deletion is complete in code, and only documents still name it.
grep -rn "auto_when_green\b" --include='*.py' --include='*.ts' --include='*.tsx' . \
  | grep -v node_modules | grep -v '_and_low_risk'
# expect: no hits outside docs/

# 2. Backend tests.
cd backend && .venv/bin/python -m pytest tests/test_merge_policy.py \
  tests/test_auto_merge.py tests/test_work_review.py tests/test_scope_execution.py -q

# 3. The day-one guarantee, by name.
cd backend && .venv/bin/python -m pytest \
  tests/test_merge_policy.py::test_the_shipped_tree_has_no_scope_configured \
  tests/test_auto_merge.py::test_nothing_in_this_tree_arms_a_scope -q

# 4. The stores really are absent on this instance.
ls -la .runtime/merge-policy.json .runtime/merge-defaults.json 2>&1
# expect: No such file or directory, both

# 5. The generated client matches the source tree. Absolute gate, not a ratchet.
npm run gen:api-types && python3 scripts/check_generated_client.py

# 6. Types, both the kit and the app. NEVER `npx tsc`.
npm run typecheck

# 7. Design system on the changed .tsx: inline hex, emoji, not-allowed, em-dash.
bash scripts/check_design.sh

# 8. Frontend logic tests.
cd frontend && npm test

# 9. No platform source file names a specific app; the counts may only go down.
python3 scripts/check_no_app_names.py

# 10. No new strongly-connected component. auto_merge takes the two gate lists
#     as arguments precisely to avoid one; a lazy import that reintroduces the
#     triangle fails here.
python3 scripts/check_module_layout.py

# 11. The completion gate. Nothing is marked [x] without it.
bash scripts/reviewer.sh

# 12. Deliver it. A UI change is not delivered until the origin serves it.
bash scripts/apply.sh
```

Re-measure the design doc's database claim before quoting it:

```bash
cd backend && .venv/bin/python -c "
from services import changes, ownership
ownership.pin_operator()
rows=[c for c in changes.list_changes() if c['status'] not in ('merged','closed')]
print(len(rows),'open changes;',sum(1 for c in rows if c.get('gates')),'carry gates')
"
```

Manual check after commit 4, on the built origin, in both themes:
the Merging tab appears in the Settings column; the deployment default reads "I
review everything"; every scope row reads the same and carries the "default"
tag; the two threshold inputs read 5 and 100; Save is disabled until something
is dirty; Cancel restores; and disconnecting the backend produces a
`StateBlock variant="error"` naming `/api/agents/autonomy/merge-policies` with a
Retry, not a blank panel.

---

## 6. Genuinely the operator's decision

Nothing below is settled by this plan. Each is a number or a choice with no
technically correct answer, and each is on the settings screen precisely so it
can be changed without a commit.

1. **`LOW_RISK_MAX_FILES`, currently 5.** Policy, not physics
   (`auto_merge.py:99-101`). The argument for 5 is that reviewer accuracy is
   measured to collapse as diff size grows, so the ceiling is the whole point of
   the classifier. The argument for raising it is that a genuine small change
   that touches a component, its test and its story is already at 3, and one
   refactor of an import path is at 12. **Recommendation: leave it at 5 until a
   scope has actually been armed for a week, then raise it against real refused
   changes rather than against a guess.**

2. **`LOW_RISK_MAX_CHANGED_LINES`, currently 100.** Same argument. 100 is "a
   person could read the whole thing in a minute if they chose to", which is the
   honest standard for a thing nobody is going to read. **Recommendation: leave
   it at 100.** Note that this is the only setting in the feature whose movement
   in one direction strictly increases risk, which is why it lives behind Save
   rather than committing on change.

3. **Whether to arm any scope at all, and which one first.** The design
   recommends arming none until the loop runs again, and that is right: with the
   loop off, a standing authority buys nothing and `manual` everywhere is
   correct. When the loop is back on, `development` is the obvious first
   candidate and also the worst one, because it is the platform repo and its
   scope only resolves in developer mode.

4. **Whether the deployment default should ever be anything but `manual`.** On a
   single operator instance the deployment default and the one scope he uses are
   nearly the same knob. The tier earns its place the day there is a second
   scope, and until then setting it is equivalent to setting the scope.
   **Recommendation: build the tier, ship it reading `manual`, and leave it
   there.**

5. **G3: whether an auto-merged change should complete its ticket.** Landing the
   code and still queueing the ticket is defensible ("the operator closes his
   own work") and is also the reason the queue does not drain. This one needs
   the lease refactor either way, so the decision is only about whether to
   file it now.

6. **G7: whether a failed auto-merge should reach the operator at all**, and if
   so as a change note or an inbox item. Doing nothing is a real option on a
   single operator instance where the change is on a screen he already visits.
