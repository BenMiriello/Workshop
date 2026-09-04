# Handoff, 2026-09-04: the apps left the repo, and what is actually left

- Written at the operator's request as a compaction checkpoint. Read this
  instead of the transcript.
- **Everything labelled measured was run on 2026-09-04 and the command is
  given.** Nothing here is recalled. If you are about to repeat a blocker you
  read somewhere, re-run its command first: three separate blockers quoted in
  these docs were falsified today, and one of them had been false for a week.

---

## 1. What landed

**Stage 7 is finished.** All eight apps left the platform repository.

```
python3 scripts/check_no_app_names.py     ->  content 0   imports 0   literals 275
bash scripts/check_fresh_clone.sh         ->  PASS no_app_source
                                              PASS no_personal_data
                                              PASS build_with_zero_apps      (0 files removed)
                                              PASS shell_boots_with_empty_catalog
                                              FAIL no_hardcoded_identity     (see 3.1)
```

`build_with_zero_apps` reporting **0 files removed** is the sentence to read
twice. That assertion works by deleting app source from a fresh clone and
building what is left. It now deletes nothing, because the clone has none.

Platform commits: `b4e7a63c` `ad317429` `34d257a4` `37410236` `a16fcfe4`
`4c37dfbd` `65482dc4` `d9d36677` `b8aad2a9` `9985002a` `e052b078` `01ae7a2c`
`056057b7` `e8a80029` `25ec3f61`.
Apps repo (`/workspace/apps`, its own git repo): `161f8cd` `a4aafd9` `9a56f6c`
`4ed1e76` `5c70f6b` `17be714` `ab7cce3` `c79ad26`.

### 1.1 The enabling mechanism, which is the part that had been missing

The moves were never the hard part. `frontend/src/apps/registry.tsx` discovered
apps with `import.meta.glob` scanning **inside the platform package**, so an
app's pages could not leave and still render. Everyone was waiting on the
stage-5 runtime import-map loader, which is a large build, and that wait is why
seven extraction tickets sat still for two weeks.

The **build-time half** was built instead. The registry scans
`../../../apps/*/ui/module.tsx` as a second pattern, and an extracted app
SHADOWS an in-tree one of the same id — the rule `app_registry._build_catalog`
already applied on the backend, for the same reason: extraction is a sequence,
not a flag day.

This gets an app's SOURCE out, which is the requirement. It does not yet let an
app install without rebuilding the shell, which is stage 5
(`86564637`, still open). Stage 5 is unchanged by this and lands on the same
files: an app already lives at `apps/<slug>/ui/` and already exports
`appModule`.

Supporting seams, both deliberately named rather than convenient:

- **`@shell/...`** — vite alias + tsconfig path, how an app reaches a
  shell-owned module the kit does not export yet (`appModule`, `lazyPage`,
  `schema.gen`). Every specifier through it is a candidate for kit promotion
  (stage 4b); the alias is meant to shrink to nothing.
- **`testpaths = tests ../apps/*/tests`** in `backend/pytest.ini`, and
  `test.include` with `../apps/*/ui/**` in `frontend/vite.config.ts`. An app's
  tests leave with its source and must still run. The Python half was done
  first; the TypeScript half was missed for hours and a vitest run that finds
  fewer files still reports success.

### 1.2 Two silent failure modes. Carry these forward.

1. **`routes/X.py` and `services/X.py` share a stem**, so a mechanical rename
   maps both onto one module. One app's engine import became a self-import;
   another's router resolved to its store.
2. **A module that lived in `services/` and wrote `from . import ownership`**
   meant `services.ownership`. After the move `.` is the app package.

**Neither failed at import.** Both failed at catalog build, where
`_build_catalog` logs and SKIPS a declaration that raises — so the app was
simply absent, with one INFO line. An app's own tests cannot catch this: they
import the app directly and never ask the platform whether it was discovered.

> **After extracting an app, assert the catalog contains it.**
> `backend/tests/test_app_registry_tolerant.py` is that guard and it now reads
> BOTH declaration sources and fails loudly when neither yields anything.

### 1.3 What a fresh-context review caught, and it was right

`work-reviewer` rejected the extraction for five reasons, all consequences of
it, all since fixed (`9985002a`, `e8a80029`):

- the catalog guard above had become `assert []`;
- `reviewer.sh` emitted a `retired` status `change_gates` did not know;
- `apps/` becoming a nested repo dropped it out of the auto-commit guard (here
  the CODE was right and the TEST had memorised a directory list);
- the generated OpenAPI client went stale, because FastAPI derives component
  names from module paths;
- five live platform surfaces read as dead because their importers had moved
  out of the scanned corpus. Fixed in the SCANNER, not by pinning them.

A second re-review then caught one more, caused by a legitimate downward re-pin:
`test_ratchets_not_loosened` encoded a fact about today's baseline, so it failed
when the repo got BETTER. Its own docstring records the same defect being fixed
once before.

**Both reviews were worth more than the work they reviewed.** The
worker-does-not-judge-its-own-completion rule is load-bearing, not ceremony. Two
findings were "you verified against your working tree, and the file is
uncommitted" — a class of error self-review structurally cannot catch.

---

## 2. Where things now live

| thing | where | contract |
|---|---|---|
| app source | `apps/<slug>/` — its own git repo, `/apps/` in `.gitignore` | — |
| backend | `apps/<slug>/<pkg>/` | one `[project.entry-points."workshop.apps"]` line; `pip install -e` is the install step |
| frontend | `apps/<slug>/ui/module.tsx` exporting `appModule` | found by the shell's glob |
| tests | `apps/<slug>/tests/`, `apps/<slug>/ui/*.test.ts` | collected by pytest testpaths and vitest include |
| **config** | `apps/<slug>/app.env`, loaded at discovery | already-set always wins |
| legacy routes | `AppModule.redirects` | shell renders them, naming none |
| design exemptions | `workshop.app.json` → `ui.design_exempt` | `check_design.sh` reads it |
| app data | `.runtime/...`, env-overridable | unchanged, per `apps-are-pluggable.md` |

`scope_root("app:<slug>")` (`backend/wsplatform/paths.py:125`) is the single
mapping. The overlay has **no git remote** — that is decision D1 in
`stage-7-extraction-plan-2026-08-24.md` §8 and it is the operator's.

**`app.env` is NOT a ratified design.** The design set covers app DATA and says
nothing about where the env pointers themselves live. This is the smallest thing
that works and it should be ratified or replaced, not inherited as settled.

---

## 3. What is actually left, measured

### 3.1 The last fresh-clone assertion — ticket `d7a308f7`

`no_hardcoded_identity` fails on 216 sites. **Every one is in a `.md` file. Zero
are in source.**

```
bash scripts/check_no_hardcoding.sh --all --tree 2>&1 \
  | grep -E "^[[:space:]]+[^:]+:[0-9]+" | grep -v "\.md:" | wc -l    # -> 0
```

The message says "in tracked source", which is what `--all` means, and reading
it literally makes this look like a code cleanup. It is ~60 documents: audit
transcripts quoting real `ps aux` output, handoffs quoting real paths. It needs
a judgement (scrub / relocate / redact — the ticket recommends redact), not a
sweep. This is the ONLY thing between here and
`check_fresh_clone.sh` exiting 0, which per CLAUDE.md is the only permitted
claim that app separation is done. **Do not write that sentence anywhere; the
command says it or nobody does.**

### 3.2 `literal_sites: 275`, and the count is misleading

Do not read 275 as 275 app names in code. Measured breakdown:

| where | count | what it is |
|---|---|---|
| `docs/*` | 155 | design docs about these apps. Same judgement as 3.1. |
| tests | 84 | platform tests using real app ids as fixtures |
| ratchet baselines (`*.json`) | 7 | the gates' own memory |
| `shared/knowledge` | 3 | instance notes |
| **platform source** | **~11** | and most are FALSE POSITIVES |

Of the ~11 in source: `triggers.py` (5), `jobs.py`, `usage.py`,
`agents_registry.py` all match on the loop/token **budget** field, and
`app_runner.py` (6) on the manifest **`health:`** probe path field. Renaming a
platform field to satisfy a string matcher is the tail wagging the dog.

**The one real remainder is `backend/scripts/migrate_to_owned.py`**, a one-off
already-run migration carrying `_DOMAINS = [four app names]` plus per-app file
names and a per-app token. It should be RETIRED, not generalised.

### 3.3 Seven lines I could not remove

`~/.config/workshop/instance.env` holds 7 variables belonging to 3 apps
(`BIRDNET_*` ×5, `HEALTH_AUTH_ENV`, `WORKSHOP_COMMUTE_DISCORD_CHANNEL`). The
per-app `app.env` files exist and the loader is built, but **already-set wins**,
so the app files are shadowed until those lines go. That file is on a read-only
mount from an agent sandbox — this is reachability, not permission. One command
from a shell that can write it:

```
sed -i -E '/^(BIRDNET_|HEALTH_AUTH_ENV=|WORKSHOP_COMMUTE_DISCORD_CHANNEL=)/d' \
  ~/.config/workshop/instance.env
```

### 3.4 Worktrees: built, correct, and switched off

`WORKSHOP_TREE_LEASE_MOUNT` is unset. The whole lease/branch-on-write mechanism
is built and tested.

- The blocker quoted for a week (`EXECUTION-PLAN-2026-08-27.md` C-3: "cannot be
  turned on, `session_defaults.resolve`/`.choose` have zero non-test callers")
  is **STALE**. Both have real callers (`chat_worktree.py:274, 302, 529`) and
  `chat_worktree.enter` is called from `routes/chat.py:3765`. Corrected in
  `b8aad2a9`; the original text is kept verbatim underneath.
- What genuinely blocked it is fixed (`d9d36677`): the write-blocked path
  refused to auto-create a worktree for any chat whose `context_key` did not
  start with `project:` — which is the operator's own chat, since
  `DevelopmentContextProvider` keys itself `development` by design (ADR 0014).
  Arming would have dropped the chat that cuts the most branches into the
  manual picker the design exists to delete.
- Worktree naming landed (`a3b6df40`); `branches/development/` shows readable
  names.
- `reader_staleness.py` still has zero callers anywhere. That is B8, separate.

**Arming it is one line in `instance.env` and it is the next real step.** It is
a global behaviour change whose documented failure mode is every turn losing
write access at once, so it wants its own turn with a restart someone watches.

---

## 4. The rest of the planned work

**210 open tickets.** This handoff does not summarise them; `project_tasks.py
list-json .` does. The five at priority 100:

| id | title |
|---|---|
| `ae9a6c08` | Messages sent during a running turn render out of order in the transcript |
| `c8c49cca` | Publish compaction state on the thread so every client can see it |
| `65cb8c6f` | The chats page is a list of chats again; a worktree is a label, not a heading |
| `70860d58` | Pause a run at the budget cap and resume it, without losing the work |
| `5c893b55` | [SEC-C3] Verify inbound IPv6 firewall policy |

Named clusters at priority 80, each with real design behind it:

- **App platform**: `86564637` stage-5 loader part one · `403b3b88` four blind
  spots in the app-name gate · `1596d691` the gate cannot see a hardcoded home
  path · `fb93c4d2` / `9ed7a34b` / `80709722` / `0cae7ab2` the artifact,
  deploy-block, release-record and disposable-worktree build chain.
- **Threads and autonomy**: `2f1b6a66` armed-on-ready-ticket · `5d004289` armed
  schedule · `e35a808a` a thread's role narrows its turn.
- **Surface deletion**: `5b41eb9d` retarget 17 links then delete `/runs`,
  `/changes`, `/deploys`.
- **Doc hygiene**: `4868a051` verify every stated blocker and correct the stale
  ones. **This ticket is now urgent and its premise is confirmed** — three
  stale blockers were falsified today by re-running one command each.
- **Open questions**: `2850a442` design the operator-intent concept ·
  `c9bcfc9b` the self app's surface is instance data.

Also unstarted from `one-thread-many-senders-2026-08-27.md` §11: the three
chats/agents-merge taste calls. And `main` is 1130 commits behind `origin/main`
with this branch unmerged.

---

## 5. Standing corrections, so they are not re-learned

1. **A stated blocker is a claim with a date.** Re-run its command before
   quoting it. Three were false today; one had been quoted for a week; one
   ("no in-tree app declarations") was created BY the extraction it guarded.
2. **A ratchet that improves must be re-pinned**, and the reason recorded.
   Improvements were being given back silently.
3. **A gate whose corpus shrinks reports absence as a defect.** Every
   false-positive class today had this shape. Fix the SCAN, never buy an
   exemption — an exemption hides the surface the next time it really dies.
4. **`git ls-files` is the platform's view only.** `apps/` is a separate repo
   and is invisible to it. Anything asking "does anything reach this?" must
   include nested-repo files.
5. **Verify against a COMMIT, not the working tree.** Two review findings were
   uncommitted files that made a gate pass only for the author.
6. **Do not treat another agent's work as a reason to stop.** The operator
   objected to this specifically on 2026-09-04: several agents each named the
   backend as the reason theirs could not finish. A restart is a pause.
7. **Parallelise earlier.** This session ran almost everything in one thread and
   the operator called it out. The extraction chain was genuinely sequential
   (each pair depends on the previous), but the doc-redaction work (3.1), the
   test-fixture cleanup (3.2) and the surface deletions (`5b41eb9d`) are
   independent and should have been dispatched at the start.

---

## 6. Immediate next actions, in order

1. Remove the seven lines from `instance.env` (3.3). One command, needs a shell
   that can write it.
2. Arm `WORKSHOP_TREE_LEASE_MOUNT` in its own turn, with a watched restart
   (3.4).
3. Take ticket `d7a308f7` — decide scrub/relocate/redact and close the last
   fresh-clone assertion (3.1).
4. Retire `backend/scripts/migrate_to_owned.py` (3.2).
5. Re-pin or fix the ~84 test fixtures naming app ids (3.2), which is the
   largest single remaining block of `literal_sites`.

**Verify before believing any of the above.** The three commands that decide
everything in this document:

```
python3 scripts/check_no_app_names.py
bash scripts/check_fresh_clone.sh
bash scripts/done.sh
```
