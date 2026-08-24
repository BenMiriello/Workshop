# App separation: why it keeps coming back, and the mechanism that ends it

**Status: current. 2026-08-24.** Supersedes nothing. It sits under
`app-separation-and-github-2026-08-20.md` (the plan) and
`HANDOFF-app-platform-and-github-2026-08-20.md` (the stage state) and answers a
question neither of them asks: not *what is left*, but *why does every new agent
rediscover this, and why did earlier ones report it finished*.

## 0. The operator's question, 2026-08-24

> "Why do we still need to refactor it out? Because why would I trust you to do
> it if I have entrusted other Opus V agents to do it many times before, and
> they always say they did it, and then some future agent like you just did
> always rediscovers that the work was not actually done ... So what the fuck is
> going on here?"

It is a fair question, the record supports the complaint, and it has a specific
answer with four parts. None of them is "the agent lied".

## 1. First, the fact that matters most: nothing of his is committed

Measured on clean HEAD, 2026-08-24:

    git ls-files apps/ | wc -l          ->  0
    du -sh apps/                        ->  2.2G

**Zero files under `apps/` are tracked.** `/apps/` has been gitignored since
2026-08-20 (`.gitignore:160`), along with `/agents/apps/`, `/backend/mcp/plaid/`
and `/commute/`. The 2.2GB and ~30,000 files are untracked working-tree data on
this one machine. A clone by anyone else gets none of it.

The earlier session's phrasing ("30,000 files and 2.2GB living inside the
platform tree") was true of the working directory and read as if it meant the
repository. It did not. That distinction is the difference between an
embarrassment and a catastrophe, and it should have been stated first.

## 2. What IS still in the repo, measured

`scripts/check_no_app_names.py`, on clean HEAD:

    content   102  tracked files that ARE app source
                   birds 48, commute 21, finance 9, notes-app 9,
                   budget 7, location 5, health 3
    imports    20  platform files importing an app module
    literals  305  platform lines naming an app

So the complaint is substantially right: **102 tracked files of one operator's
apps are in the product**, mostly React pages under `frontend/src/pages/apps/`
and service modules under `backend/services/`. The giant data directories left
on 2026-08-20; the source did not.

Of the 305 literals, about 88 are the two enumerating registries
(`frontend/src/apps/registry.tsx` 54, `backend/services/app_registry.py` 34),
about 110 are test fixtures naming real apps, and 11 are the checker's own list
of the apps it watches.

**And the gate is FAILING on clean HEAD right now: 302 -> 305.** It gates
`reviewer.sh`, so while it fails it rejects unrelated work, which is the third
time this year a ratchet has quietly become a backlog stop.

## 3. Why it recurs. Four mechanisms, not one

### M1. A gate that measured the wrong thing, and said "clean"

`scripts/check_pluggable_apps.sh` printed `pluggable-apps: clean` and was wired
into `reviewer.sh`. It inspected **five files**. It did not look at
`frontend/src/pages/apps/`, at `apps/`, at `backend/mcp/plaid/`, or at the
app-named modules in `backend/services/`.

A gate that cannot see the violation is worse than no gate, because it converts
an unsolved problem into a solved-looking one. Fixed 2026-08-20 by
`check_no_app_names.py`, which measures three real counts over the whole tree.

### M2. A status line that overstated, with the exception one paragraph away

`docs/handoff-pluggable-apps-migration.md:3`, verbatim:

> **Status: DONE on `main` (2026-07-12).**

Twenty-one lines later, in one sentence: *"Remaining (intentionally deferred):
integrated in-shell frontend route injection."* That deferred clause is the
entire frontend. Anyone reading the top line concludes separation shipped. Every
later agent read the top line.

### M3. The actual blocker was named everywhere and specified nowhere

Dynamic frontend bundle injection was cited as the blocker in eight places
across docs and code and **designed in zero of them**. So each agent arrived,
hit the same undesigned wall, did the part it could reach, and wrote a new
document. That is the loop the operator has been watching.

`app-separation-and-github-2026-08-20.md` §2 closed it on paper (import maps +
dynamic `import()` of a built ESM bundle). It is still not built. The frontend
registry today still enumerates apps with literal module specifiers; `lazyPage`
defers *loading*, not *building*, so the code remains in the repo and in the
build graph.

### M4. Progress and completion diverge, and the number is not readable as status

This one is new and is mine. During a staged extraction the counts legitimately
go **up**: stage 4 raised `content_files` 95 -> 102 on purpose, because moving an
app's business logic out of a platform module and into app-owned modules
*increases* the count of app-source files while *decreasing* coupling
(`imports` 24 -> 20 in the same change). The baseline records that raise with a
reason, which is good discipline.

But it means the headline number cannot be read as "how close are we". A reader
sees 102 and cannot tell progress from regression without reading a paragraph.
And there has never been an **executable definition of done** at all: no
command anyone can run that answers "is a clone of this repo free of one
operator's apps, and does it work". Without that, "done" is always a sentence
someone wrote, and a sentence is exactly what failed in M2.

## 4. The mechanism that ends it: a fresh-clone assertion

The durable fix is not another doc, another status line or another count. It is
**one command that a person or a gate can run, whose result cannot be produced
by writing prose**:

    scripts/check_fresh_clone.sh

It clones the repo at HEAD into a temporary directory, installs, builds, boots
the backend, and asserts what a stranger's install must be true of:

1. **No app source.** Zero files matching the app-source classifier the existing
   checker already implements. Today: 102, so it starts pinned and ratchets down.
2. **The build succeeds with zero apps installed.** This is the one that
   catches M3, because a registry with literal module specifiers for apps that
   are not present fails here and passes every other gate in the repo.
3. **The shell boots and serves.** Nav, chat and settings render with an empty
   app catalog.
4. **No operator identity.** No `benmiriello`, no tailnet host, no personal
   paths in tracked files.

It is the right instrument for three reasons. It cannot be satisfied by a
document. It cannot be gamed by re-pinning a baseline, which is the failure this
repo already caught once. And it measures the thing the requirement is actually
about: what a stranger receives.

**Until it exists, "app separation is done" is not a claim anyone should make or
accept, including me.** After it exists, the claim is a command with an exit
code.

## 5. Two smaller corrections found while measuring

**The gate counts prose that describes the violation.** The current failure,
302 -> 305, is three lines in the module docstring of
`backend/tests/test_app_workspace_declaration.py`. All three are prose recording
a measurement ("birds apps/ 30150 files projects/ 41 (a scaffold)"). An agent
documented the problem carefully and thereby blocked every reviewer run.

A docstring cannot couple the platform to an app. The three counts measure
coupling. So `literal_sites` should count code and split prose into its own
pinned `prose_sites` count: same visibility, no ability to stop the queue, and
no incentive to leave the measurement undocumented. This is a correction, not a
weakening, and it re-pins DOWN.

**The checker counts itself.** `check_no_app_names.py` contains 11 literal
sites, being the list of apps it watches. It is the one file that must name them.

## 6. What is actually left, honestly

Per the handoff's own staging, 0-4 landed and these did not:

- **4b. The frontend seams.** Promote the app pages to app-owned modules the way
  stage 4 did for the backend.
- **5. The loader.** Import maps + dynamic `import()`. This is M3 and it is the
  one genuine blocker: nothing after it can happen and nothing before it removes
  the frontend.
- **7. Extraction.** The apps leave for their own repo and all three counts
  ratchet to zero.

That is a real sequence with a real dependency, not a stall. What was missing
was never the plan. It was an executable finish line and an honest reading of
the number in between.

## 7. The convention this establishes

1. **The counts are the status.** No document may carry a "Status: DONE" line
   for app separation. `check_no_app_names.py` and `check_fresh_clone.sh` say
   where it stands, and they are the only things that may.
2. **A raise must carry its reason**, which the baseline already enforces.
3. **A gate that fails on clean HEAD is a stop-the-line event**, because it
   rejects unrelated work. Three ratchets have now done this.
4. **No new tracked file may name a real app**, including in a docstring or a
   test fixture, while the counts are above zero. Fictional fixture names, or
   ask the registry.
