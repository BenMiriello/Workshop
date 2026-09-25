from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from deps_auth import require_loopback
from deps_user import (
    require_user,
    require_operator,
)
from middleware.compression import SSEAwareGZipMiddleware

# Platform-core routers only — the shell. App routers are NOT imported here; they mount from the installed-
# app catalog (services/app_registry) so adding/removing an app never edits this
# file. See docs/apps-are-pluggable.md.
from routes import agents, projects, base_apps, relay, services, events, jobs, push, autonomy, chat, worktrees, auth, voice, voice_stream, knowledge, grants, usage, tasks_all, notes_all, docs_index, inbox, trackers, refs as refs_routes, memory_policy as memory_policy_routes, memory as memory_routes, memory_compat
from routes import files as files_routes
from routes import mcp as mcp_routes
from routes import model_providers_routes
from routes import github_identity_routes
from routes import tools as tools_routes
from routes import stores as stores_routes
from routes import apps as apps_routes
from routes import git as git_routes
from routes import changes as changes_routes
from routes import branches as branches_routes
from routes import presence as presence_routes
from routes import control as control_routes
from routes import leases as leases_routes
from services import app_registry
from services import app_mount_record
from services import installation
# Imported HERE rather than inside `lifespan` like its neighbours: this module is
# a timer around two shell commands (stdlib plus `wsplatform.paths`), so there is
# no heavy import to defer and no cycle to break, and a function-local import
# would add one more to the 574 `scripts/check_module_layout.py` is counting down.
from services import backend_schedule as _backend_schedule
import asyncio
import datetime as _dt
import logging
import os

# MODULE SCOPE, not inside `_live_schema_revision`. `db.engine` builds its
# engine lazily (`_engine = None` until `engine()` is called), so importing it
# opens no connection and costs nothing at startup, and a function-local import
# here would be one more of the 639 this repo is already trying to get rid of
# (scripts/check_module_layout.py counts them and gates on the delta).
from sqlalchemy import text as _sa_text

from db.engine import engine as _db_engine

log = logging.getLogger("workshop.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Model credentials, before anything can spend one. Two steps, and they fail
    # differently on purpose.
    #
    # The move is HOUSEKEEPING: place any credential store still sitting in the
    # flat secrets directory into its owner's partition. Every read adopts a
    # legacy store anyway, so this pass is an optimisation, not a prerequisite,
    # and it must never be the reason a backend does not start. Hence the catch.
    #
    # The encryption check is a REFUSAL, and it is fatal: a pile of other
    # people's API keys unencrypted on a hosted box is the failure the
    # encryption layer was built for, and an opt-in protection is an off one. It
    # can only fire when somebody wrote `multi_user` in the deployment mode.
    # `require_encryption_in_multi_user` keys on the DECLARED value rather than
    # the fail-closed reading precisely so that a typo in one config line cannot
    # keep a single-operator installation from booting.
    from services import model_credentials as _model_credentials
    try:
        moved = _model_credentials.migrate_legacy_files()
    except Exception as e:  # noqa: BLE001 - housekeeping never blocks a boot
        log.warning("model credential migration skipped: %s", e)
    else:
        if moved:
            log.info("moved %d model credential store(s) into their owner "
                     "partition", moved)
    _model_credentials.require_encryption_in_multi_user()
    # Installed apps: run each installed app's startup hooks + background loops
    # (cache prewarms, periodic refreshes). A fresh instance with
    # no apps installed starts none of these. Adding an app never edits this file.
    app.state._app_bg = []
    for spec in app_registry.installed_specs():
        for init in spec.on_startup:
            try:
                await init(app)
            except Exception as e:
                log.warning("app %s startup hook failed: %s", spec.id, e)
        for loop in spec.background:
            app.state._app_bg.append(asyncio.create_task(loop()))
    # App platform: free the ports of processes that have died. Apps themselves are
    # kept alive by docker's restart policy, not by us.
    from services import port_registry
    app.state._bg_port_reconcile = asyncio.create_task(port_registry.reconcile_loop())
    # Job liveness: close Jobs left `running` by a process that died. Its only
    # shell caller never fires (the dispatcher exits early while autonomy is
    # engaged), so a crashed run used to read `running` forever. The backend
    # outlives every run, so it reaps.
    from services import jobs as jobs_svc
    app.state._bg_job_reconcile = asyncio.create_task(jobs_svc.reconcile_loop())
    # Background work loop: make the worker MECHANISM follow the execution
    # INTENT. Arming a scope (services/scope_execution) is the only control;
    # this ensures a resident queue runner exists for every armed scope and
    # brings one back after a reboot, an OOM kill or a stray kill. Nothing runs
    # for a scope left at its reactive default, and the global autonomy override
    # sits above it.
    from services import work_driver
    app.state._bg_work_driver = asyncio.create_task(work_driver.reconcile_loop())
    # THERE IS NO MAKER RECONCILER HERE, and its absence is deliberate. This
    # used to start `services.makers.reconcile_loop`, which every 60 seconds
    # wrote a maker definition file for every runnable scope. `maker` is not a
    # shell-dispatched role (`primitive-roles-2026-07-18.md` §3 omits it from
    # `ROLE_RUNNERS`) and a scope needs no definition file to be worked (§6:
    # "projects work without any definition file") -- the loop above IS the
    # maker. Auto-creating one per scope is the "machine for generating noise
    # and cost" the roles research rejects, and it is the same thing
    # `routes/projects.create_project` already refuses when it writes
    # `"agents": []`. A maker definition is now written only by the operator
    # action that asks for one (`POST /agents/build` -> `services.makers.ensure`).
    # Fire-and-continue: a finished background run leaves a continuation seed on
    # the originating thread's queue (services/run_report -> chat_drain), written
    # from the detached worker process that cannot reach this event loop. This is
    # the backend side that notices the seed and starts the thread's own next
    # turn once it is idle, so a chat acts on a run's result without the operator
    # having to prompt it. Only threads holding a seed are touched.
    from services import chat_drain as _chat_drain
    app.state._bg_chat_continuation = asyncio.create_task(_chat_drain.continuation_loop())
    # Dedup vectors: embed the open backlog so filing a ticket does not. `decide`
    # compares a new ticket against every open one, and the embedder is one HTTP
    # call per text, so a cold cache made the File ticket button wait 17.5s
    # (measured 2026-08-16). The cache is a file and survives a restart, so this
    # normally finds everything present and returns immediately.
    from services import task_dedup as _task_dedup
    app.state._bg_dedup_warm = asyncio.create_task(
        asyncio.to_thread(_task_dedup.warm_vectors))
    # Semantic index: keep it fresh from HERE, the long-lived process, and
    # declare this process the owner of that job. The rebuild used to start in
    # whichever process called search() first, which is nearly always a chat
    # turn's own MCP server child: an 8-minute multi-GB batch job inside a
    # turn's process group, charged to that turn. On 2026-08-22 a turn was
    # killed at the 6 GB ceiling for calling search on a cold index. A search
    # now degrades to what is on disk while this loop catches it up.
    from services import semantic_index as _semantic_index
    app.state._bg_index_maintenance = asyncio.create_task(
        _semantic_index.maintenance_loop())
    # Scheduled passes, from HERE, because there is no crontab on this host and
    # no systemd timer either. Two of them today. `dispatch_triggers.sh` is the
    # only thing that turns a declared `cron` or `loop` trigger into a running
    # sweep and its only caller was a crontab entry, so `trigger-state.json` sat
    # frozen from 2026-07-18 to 2026-09-04; `report_ratchet_drift.py` reads the
    # absolute ratchet counts, which nothing else does on any cadence, so three
    # upward drifts past a baseline that may only go down were found by audits
    # rather than by a gate. ONE loop with a table, not a loop per job. It only
    # ticks: each command still owns every decision it makes, including the
    # autonomy override that currently stops each trigger tick before it
    # dispatches.
    app.state._bg_schedule = asyncio.create_task(
        _backend_schedule.schedule_loop())
    # Crash/restart resilience: re-attach to any build turn that was still running
    # when the backend went down. Detached claude subprocesses keep working across
    # a restart; this tails their on-disk journals to completion (or finalizes a
    # dead one) so a chat is never left stuck mid-turn.
    #
    # The chat-log migration (one JSON document per thread -> an append-only
    # event log, docs/chat-rebuild-spec-2026-08-09.md) USED to run here on every
    # boot. It is finished: 58 threads migrated, zero legacy documents left on
    # any owner, measured 2026-08-17. The per-owner fold-in stays where it can
    # still matter (chat_store.list_threads / get_thread), so a straggler is
    # still absorbed on first touch without a startup pass over every owner.
    try:
        # Layer 0 filesystem restructure: move the four in-source state files
        # (grants/resource-grants/apps/store-policies.json) under the untracked
        # runtime root, once, before anything reads them. Readers already resolve
        # through wsplatform.paths with a fallback to the old location, so this is
        # the physical move that makes the fallback unnecessary. Idempotent (a
        # file already relocated is skipped) and logs a conservation line per
        # move. See services/state_file_relocate.
        from services import state_file_relocate
        relocated = state_file_relocate.run_once()
        if relocated:
            log.info("state files relocated: %s", "; ".join(relocated))
    except Exception as e:
        log.error("state file relocation failed at startup: %s", e)
    try:
        # The operator's knowledge notes and the workshop queue export moved
        # from the tracked `shared/` tree to the runtime root; copy an existing
        # install's files across once, logging what moved. See
        # services/workspace_relocate.
        from services import workspace_relocate
        workspace_relocate.run_once()
    except Exception as e:
        log.error("workspace relocation failed at startup: %s", e)
    try:
        # The chat store's on-disk `run` vocabulary becomes `turn`, once. This
        # runs BEFORE anything reads a thread, because the keys and the code
        # that fences on them have to change together: a turn in flight under
        # the previous build would stop recognising its own claim. Guarded by a
        # marker file, so later boots cost one stat. See services/chat_log_turn_rename.
        from services import chat_log_turn_rename
        renamed = chat_log_turn_rename.run_once()
        if renamed:
            log.info("chat turn rename: %s", renamed)
    except Exception as e:
        log.error("chat turn rename failed at startup: %s", e)
    # NO APP-REGISTRY RECONCILE HERE, deliberately. The `app` table is kept in
    # step with the two file registries by `services.apps.reconcile_from_registries`,
    # which needs a TENANT, and a tenant is resolved from the acting owner --
    # which startup does not have. It runs on the `/apps` read and after every
    # projects-registry write instead, both of which do. `owner_session` would
    # supply a tenant without one, and is exactly the trap `services/tenancy`
    # records falling into: the serving process does not hold that credential,
    # so it raises in production and passes under pytest.
    try:
        from services import build_streaming
        build_streaming.reattach_running_turns()
        # Second pass, after the live journals are re-registered: any thread
        # still pointing at a run nobody is driving is closed out as lost, so a
        # chat is never left waiting on something that will never answer.
        build_streaming.reconcile_threads()
    except Exception:
        pass
    try:
        # Cache each thread's resolved context cut. Threads cleared before the
        # cache existed carry a boundary and no count, and the sync endpoint
        # answers from meta alone — so without this their divider would vanish
        # while the prompt kept honouring the cut. One log read per thread, once.
        from services import chat_store as _cs
        n = _cs.backfill_cuts()
        if n:
            log.info("resolved the context cut for %d thread(s)", n)
    except Exception as e:
        log.error("context-cut backfill failed at startup: %s", e)
    try:
        # ...and then send anything that was waiting behind a turn this restart
        # killed. The queue is on the thread, so it survived; nothing else would
        # ever notice it, because the browser that queued those messages may
        # never open that chat again. Import here, and after the reconcile, so a
        # thread whose dead run has just been closed out reads as idle.
        # routes.chat registers the starter, so import it first.
        from routes import chat as _chat_routes  # noqa: F401
        from services import chat_drain
        app.state._bg_queue_drain = asyncio.create_task(chat_drain.drain_all())
    except Exception as e:
        log.error("queued chat messages could not be drained at startup: %s", e)
    yield
    # FIRST, before anything is cancelled: say that this is a restart, not an
    # abandonment. asyncio.run() cancels every surviving task on the way out,
    # including each turn's drive loop, and that cancellation used to be handled
    # by killing the turn's detached agent process and committing "this turn
    # ended early". A restart is meant to be a pause -- the child is detached and
    # the unit sets KillMode=process precisely so it survives -- and this flag is
    # what keeps that promise from being broken from inside.
    try:
        from services import build_streaming as _bs
        _bs.begin_shutdown()
    except Exception:
        pass
    for t in [*getattr(app.state, "_app_bg", []),
              getattr(app.state, "_bg_port_reconcile", None),
              getattr(app.state, "_bg_job_reconcile", None),
              getattr(app.state, "_bg_queue_drain", None),
              # Named explicitly rather than left to asyncio's exit-time sweep:
              # its trigger pass starts detached sweeps, and a tick that begins
              # while the process is tearing down would launch work nothing is
              # left to supervise. The next boot runs it again.
              getattr(app.state, "_bg_schedule", None)]:
        if t:
            t.cancel()


# Interactive API docs (/docs, /redoc, /openapi.json) disclose the full route map.
# Off by default; opt in with WORKSHOP_ENABLE_DOCS=1 for local dev.
# (security-audit-2026-07-18 L3)
_DOCS = os.environ.get("WORKSHOP_ENABLE_DOCS", "0").strip().lower() in {"1", "true", "yes"}

# The HTTP contract version, carried in the OpenAPI schema's `info.version`.
# Set because the schema is now a build artifact: `scripts/gen_openapi.py`
# emits it and `frontend/src/api/schema.gen.ts` is generated from it, and
# shipping FastAPI's placeholder "0.1.0" would have that artifact state a
# version this app never chose.
#
# Hand-bumped, deliberately, and deliberately NOT the backend source id.
# `_SOURCE_ID` below already answers "what code is answering" at /healthz and
# changes on every commit; putting that here would rewrite the emitted schema
# on every commit while saying nothing about whether the CONTRACT changed, and
# the whole point of the generated types being committed is that a diff in
# them means something. Bump the minor on an additive change, the major on a
# breaking one.
API_VERSION = "1.0.0"

app = FastAPI(
    title="Workshop",
    version=API_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


# ADR 0023 clause 3: one error model, from one place, covering all 348
# existing `raise HTTPException` sites with zero edits to any of them. The
# wire shape stays `{"detail": ...}` -- what FastAPI already produces -- and
# `detail` becomes `{"code", "message", "hint"}` here. These are the first
# exception handlers this app has ever registered.
#
# `starlette.exceptions.HTTPException`, not `fastapi.HTTPException`: every
# `fastapi.HTTPException` IS one (it's a subclass), and catching the
# Starlette base also catches the ones the framework raises itself (an
# unmatched route's 404, a wrong-method 405) rather than only the ones this
# codebase wrote by hand.
#
# This does not touch SSE or WebSocket framing. A `StreamingResponse` that
# has already started sending bytes cannot be intercepted by an exception
# handler at all (Starlette can no longer replace a response once
# `http.response.start` has been sent) — this only ever fires for an
# HTTPException raised before a streaming response begins, e.g. an auth
# check, which already produced a plain JSON error and continues to.
# WebSocket routes don't raise HTTPException in the first place (there's no
# HTTP response to carry one); a `WebSocketException` is a different type
# entirely and is untouched by this handler.
from wsplatform.http import error_body, validation_error_body  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": error_body(exc.status_code, exc.detail)},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        {"detail": validation_error_body(exc.errors())},
        status_code=422,
    )


# Response compression. SSE-safe by construction -- see middleware/compression.py
# for why the stock starlette GZipMiddleware is unsafe for streaming responses
# and must never be swapped in here. minimum_size=500B: don't bother on tiny
# bodies. compresslevel=5: level 9 measured 87ms on a ~4.9MB payload for
# negligible extra ratio; 5 is the right cost/benefit tradeoff.
#
# THE POSITION OF THIS LINE IS LOAD-BEARING. `add_middleware` inserts at the
# front of the stack, so the LAST one registered ends up OUTERMOST. This must
# be registered FIRST, which makes it INNERMOST: closest to the router, where a
# JSONResponse still arrives as one complete `http.response.body` message.
#
# `strip_api_prefix` below is a BaseHTTPMiddleware, and BaseHTTPMiddleware
# re-emits every response it handles as a chunked stream (`more_body=True`).
# This middleware deliberately refuses to compress a chunked body, because it
# has no per-chunk flush and stalling SSE is the worst failure available here.
# So with compression registered LAST it sat outside BaseHTTPMiddleware, saw
# only chunks, and correctly compressed nothing at all: measured 2026-08-14,
# a 4.83MB events response went out uncompressed with the middleware active
# and apparently wired. Registering it here fixes that. If you move it below
# any BaseHTTPMiddleware (including any `@app.middleware("http")` function),
# compression silently switches itself off again and nothing fails loudly.
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=500, compresslevel=5)


# A reverse proxy can mount the backend directly at :443/, so paths from an
# external ingest client arrive unprefixed — but the vite dev proxies use
# /api/... and strip that prefix. Accept both so callers don't have to care
# which entry point they used.
@app.middleware("http")
async def strip_api_prefix(request, call_next):
    path = request.scope.get("path", "")
    if path == "/api" or path.startswith("/api/"):
        new_path = path[4:] or "/"
        request.scope["path"] = new_path
        request.scope["raw_path"] = new_path.encode("ascii", errors="ignore")
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(installation.dev_origins()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gating model (docs/auth-redesign/): identity, never network position.
#   PLATFORM control      -> require_operator (the admin role)
#   per-user personal data -> require_user (cookie / device token), owner-scoped
#   single-dataset domains -> require_domain_owner(<domain>) (owned data; the
#                             domain comes from the app's declaration, never from here)
#   low-sensitivity + ingest -> require_user_or_service(scope)
#   internal machine infra  -> require_loopback (no personal data; not public)
#   ingest webhooks         -> require_service(scope) / Basic bound to an owner
_OPERATOR = [Depends(require_operator)]
_USER = [Depends(require_user)]
_LOOPBACK = [Depends(require_loopback)]

# Platform / infra control surface — operator (admin) only.
app.include_router(jobs.router, dependencies=_OPERATOR)  # top-level Job feed (/jobs)
app.include_router(tasks_all.router, dependencies=_OPERATOR)  # cross-scope task feed (/tasks)
app.include_router(trackers.router, dependencies=_OPERATOR)  # external tracker connection + triage (/trackers)
app.include_router(notes_all.router, dependencies=_OPERATOR)  # cross-scope notes feed (/memory/notes)
app.include_router(apps_routes.router, dependencies=_OPERATOR)  # the app entity + its collections (/apps)
app.include_router(inbox.router, dependencies=_OPERATOR)      # agent inbox (/inbox)
app.include_router(docs_index.router, dependencies=_OPERATOR)  # docs index, read + write (/docs)
app.include_router(files_routes.router, dependencies=_OPERATOR)  # one file API over a resolved workspace root (/files)
app.include_router(refs_routes.router, dependencies=_OPERATOR)  # ref resolve + backlinks (/refs)
app.include_router(memory_policy_routes.router, dependencies=_OPERATOR)  # what other chats see (/memory-policy)
app.include_router(memory_routes.router, dependencies=_OPERATOR)         # semantic search + index status (/memory)
app.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_OPERATOR)
app.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=_OPERATOR)
# ONE PREFIX FOR ONE CONCEPT (MEM-15). This was `/knowledge`, alongside
# `/notes` and `/memory` for the same store, while the page is called Memory and
# the thing is called a note. Everything scope-addressed sits under
# `/memory/scopes/` so the wildcard cannot swallow `/memory/notes`; see the
# comment in routes/knowledge.py. The old paths are served by
# `routes/memory_compat.py` as redirects.
app.include_router(knowledge.router, prefix="/memory/scopes", tags=["memory"], dependencies=_OPERATOR)
app.include_router(memory_compat.router, dependencies=_OPERATOR)  # the retired prefixes, as redirects
app.include_router(worktrees.router, prefix="/worktrees", tags=["worktrees"], dependencies=_OPERATOR)
# Git itself, against an explicit repository (workspace-ownership-and-leases
# 2026-08-22 sec 13.1). Below /worktrees in the layering: /worktrees resolves a
# project slug and calls the same service, this one takes the repo path.
app.include_router(git_routes.router, dependencies=_OPERATOR)  # /git
# The reviewable artifact a branch produces (changes-and-review-2026-08-22 C1).
# ABOVE /worktrees in the layering: it calls that service's published surface
# for the branch state and the diff, and nothing below it knows what a change is.
app.include_router(changes_routes.router, dependencies=_OPERATOR)  # /changes
# The worktree RECORD and its timeline (workspace-ownership-and-leases
# 2026-08-22 sec 13.2, step B2). BESIDE /worktrees, not above it: that
# router is addressed by (project_slug, branch_slug) and operates on the
# disk; this one is addressed by record id and answers who allocated a
# tree, when, for which ticket, and what became of it. It is also where
# the lease hangs off in B3.
app.include_router(branches_routes.router, dependencies=_OPERATOR)  # /branches
# Who is working here (workspace-ownership-and-leases-2026-08-22 sec 6, step
# B4). A READER of a shared channel, not its owner: the sweeps write presence
# by shelling scripts/presence.py, so this router holds no state and a restart
# loses nothing. /presence/directory is the read-time join.
app.include_router(presence_routes.router, dependencies=_OPERATOR)  # /presence
# The three verbs of the control channel (workspace-ownership-and-leases
# 2026-08-22 sec 8 and 9, steps B6 and B7): queue, steer, preempt. ABOVE
# /presence and /branches in the layering -- it reads the directory to know who
# is working and writes the worktree timeline to record what it did, and neither
# of those knows this router exists. It holds NO queue of its own: a chat's
# requests are chat_queue entries and a sweep's are the control file
# scripts/project_ralph.sh reads at the top of each iteration.
app.include_router(control_routes.router, dependencies=_OPERATOR)  # /control
# What is claimed and whether anyone is still there. Starts from the LEASE
# TABLE, which is why it is not a query parameter on /presence/directory: the
# directory joins outward from live presence, so the two rows that matter most
# to a stuck-work view -- an expired lease, and a held one whose holder stopped
# writing presence -- have no session to join from and render as silence.
app.include_router(leases_routes.router, dependencies=_OPERATOR)  # /leases
# `/drift` WAS HERE AND IS DELETED (2026-08-28). It served one always-mounted
# banner saying "you may be looking at old code"; the operator deleted that
# banner permanently, and an endpoint whose only reader is gone is dead weight
# that the next agent would wire back into some new chrome. The same question is
# answered by `scripts/apply.sh --check` from a terminal, which is where it was
# always answered. Do not reintroduce a global drift warning in any form.
app.include_router(base_apps.router, prefix="/base-apps", tags=["base-apps"], dependencies=_OPERATOR)
# Grants + Stores (Phase 4). /base-apps/grants stays as a thin alias in base_apps
# until Phase 5; this is the real grants router (/grants, /grants/mint,
# /grants/revoke, /stores).
app.include_router(grants.router, dependencies=_OPERATOR)
# The Store catalog + the one door onto a Store's contents (/stores). The operator
# is served ungated here; the gate is on a caller that says it is an agent.
app.include_router(stores_routes.router, dependencies=_OPERATOR)
# THE DEPLOY ROUTER WAS HERE AND IS DELETED (2026-09-04, ticket `726d75a9`).
# It served `/deploys`, `/deploys/providers`, `/deploys/autonomy/override` and
# the per-project `/projects/{slug}/deploys/...` family, over roughly 1,900
# lines of target bindings, two provider drivers, a run pipeline and a polling
# watcher. None of it ever ran: zero job records carried `kind=deploy` and the
# record store was empty. Do not reintroduce a deployment surface here; the
# model of record for running an app is `hosting-apps-design-2026-08-24.md`,
# and it starts from an empty seat on purpose.
app.include_router(autonomy.router, dependencies=_OPERATOR)
app.include_router(usage.router, dependencies=_OPERATOR)  # rolling-window token usage (/usage)
# Settings -> MCP: which MCP servers exist, whether they connect, what they
# cost. Machine-wide facts, so it hangs off settings rather than a chat.
app.include_router(mcp_routes.router, dependencies=_OPERATOR)
# Settings -> Tools: every capability this instance can name and who owns each
# (docs/platform-redesign/tools-are-declared-2026-08-23.md). Beside /mcp for the
# same reason: machine-wide facts, not a chat's. The router lists all three
# provenances and writes only the user's own -- LISTING IS NOT GRANTING, and
# services/llm_router.tool_flags stays the only thing that puts a tool on an
# agent's argv.
app.include_router(tools_routes.router, dependencies=_OPERATOR)
# Settings -> Models: the provider catalog and the operator's own API key store.
# Stage 1: list, add, remove. The key is stored encrypted and never returned
# (docs/platform-redesign/providers-credentials-and-limits-2026-08-23.md §3b).
app.include_router(model_providers_routes.router, dependencies=_OPERATOR)
# Settings -> Apps: the GitHub account this installation pushes and opens pull
# requests as. Beside the model keys because it is the same kind of thing, an
# outbound credential the operator registers once, stored in their own runtime
# partition and never returned
# (backend/services/github_identity.py says why it exists).
app.include_router(github_identity_routes.router, dependencies=_OPERATOR)
# The two addresses the forge redirects the browser back to, mounted WITHOUT the
# operator gate because they cannot pass it: the session cookie is
# SameSite=Strict and a redirect arriving from another site does not carry it,
# so a gate here would 401 every real completion. They authenticate on the state
# nonce instead, which is minted under an authenticated request, bound to one
# owner and one leg of the flow, accepted once, and expired in fifteen minutes
# (routes/github_identity_routes.py says this at more length).
app.include_router(github_identity_routes.callback_router)

# Platform per-user primitives — owner-scoped to the authenticated user. These
# are the shell (chat, voice, push), NOT apps: apps build on them, so they mount
# unconditionally.
app.include_router(chat.router, prefix="/chat", tags=["chat"], dependencies=_USER)
# Voice mode — local STT/TTS for the chat surface, per-owner prefs.
app.include_router(voice.router, tags=["voice"], dependencies=_USER)
# The voice socket authenticates inside the handler: _USER is typed for an
# HTTP Request and a WebSocket is not one. Same auth_store, same fail-closed
# rule, plus an Origin check (a WebSocket is not covered by the same-origin
# policy but does carry cookies).
app.include_router(voice_stream.router, tags=["voice"])
app.include_router(push.router, dependencies=_USER)

# Installed apps mount from the built-in catalog (services/app_registry), gated
# by this instance's installed-app registry. A fresh clone installs nothing and
# mounts none of these; each app carries its own auth (declared in its AppSpec).
# Adding/removing an app is an installed-apps edit, never a change to this file.
# See docs/apps-are-pluggable.md.
#
# THE RESULT IS RECORDED, and `/healthz` serves the record. Mounting zero apps
# is a legitimate fresh-clone state and mounting zero apps off a roster that
# names eight is a total outage, and until 2026-09-05 this loop expressed both
# as the same silence. `record_mounted` is what tells them apart, from inside
# the process, which is the only place the resolved roster path is knowable.
app_registry.ensure_identity_manifests()


def _mount_installed_apps(target: FastAPI) -> dict:
    """Mount every installed app's routers onto `target`, and return the record
    of what that actually did.

    A FUNCTION, not a bare module-level loop, so the ids it accumulates are a
    local and this file gains no process-local mutable state
    (`scripts/check_no_process_globals.py`, target-architecture §2.6).
    """
    ids: list[str] = []
    before = len(target.routes)
    for spec in app_registry.installed_specs():
        for m in spec.mounts:
            target.include_router(
                m.router,
                prefix=m.prefix,
                tags=list(m.tags) or None,
                dependencies=list(m.deps),
            )
        ids.append(spec.id)
    return app_mount_record.build(ids, len(target.routes) - before)


# HELD ON THE APP, not in a module global: the record describes THIS app
# object's mounting pass, and a per-process global would let one uvicorn worker
# serve another's answer.
app.state.apps = _mount_installed_apps(app)

# Internal machine-to-machine infra — loopback only, no personal data, not public.
app.include_router(relay.router, dependencies=_LOOPBACK)
# OPERATOR-AUTHED, not loopback-gated (changed 2026-08-20). This router is a
# read-only health prober: it has one GET, no ingest, and no unauthenticated
# surface. Gating it on network position was the anti-pattern the public edge's
# own comment says was removed for every other platform router, and it had a
# measured cost: /services is on the edge blocklist, so the endpoint 404'd on
# the origin the operator actually uses, which silently broke the Apps
# launcher's per-app status AND made one app's settings say its API key was
# "missing" whether or not it was set. Identity gates it on either origin now.
app.include_router(services.router, dependencies=_OPERATOR)
app.include_router(events.router, dependencies=_LOOPBACK)
# App ingest routers run off-loopback (Docker bridge); internal-only, no
# personal data, never proxied to the public origin.

# Public auth entry (register/login/logout/me) — no gate; it establishes identity.
app.include_router(auth.router)


# Browser-facing origin for the Workshop SPA (e.g. a Tailscale Serve HTTPS URL).
# Per-deployment; empty by default so a fresh clone never bounces callers to
# another instance's hostname. An install sets WORKSHOP_CANONICAL_ORIGIN on its
# service to preserve the existing tailnet redirect.
CANONICAL_ORIGIN = os.environ.get("WORKSHOP_CANONICAL_ORIGIN", "").rstrip("/")


@app.get("/")
async def root_redirect():
    """The backend can also be reached at a site root (e.g. mounted at :443/ by
    Tailscale Serve so /api/* works for external callers). Bounce the bare root
    to the browser-facing Workshop SPA origin when one is configured
    (WORKSHOP_CANONICAL_ORIGIN); with none set there is nothing to redirect to,
    so just report liveness."""
    if CANONICAL_ORIGIN:
        return RedirectResponse(url=CANONICAL_ORIGIN + "/", status_code=302)
    return {"ok": True, "service": "workshop-backend"}


def _backend_source_id() -> str:
    """The id of the source THIS process is running, computed once at import.

    scripts/apply.sh compares it against a fresh `scripts/backend_source_id.sh`
    to decide whether a restart is owed. Shelling out to that script rather than
    reimplementing the hash here is deliberate: two definitions of "what counts
    as backend source" would drift the moment one of them learned about a new
    input, and drift between the checker and the thing it checks is worse than
    no checker at all (the argument is spelled out in ui_source_id.sh).

    Measured at 34ms over 354 files, paid once at startup. Any failure yields
    "unknown", which apply.sh reports as undecidable rather than treating as a
    match -- never silently as "in sync".
    """
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "backend_source_id.sh"
    try:
        out = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=30
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception as exc:  # pragma: no cover - diagnostic path
        log.warning("backend source id unavailable: %s", exc)
        return "unknown"


_SOURCE_ID = _backend_source_id()
_STARTED_AT = _dt.datetime.now(_dt.timezone.utc).isoformat()


def _live_schema_revision() -> str | None:
    """The alembic revision of the database THIS PROCESS is connected to.

    The backend is the only thing that can answer this truthfully. Everything
    else asks the DSN in the config and connects for itself, which is the same
    question only when the asker shares this process's network namespace -- and
    an agent working in a sandbox does not. Measured 2026-09-01: a sandboxed
    session read `WORKSHOP_DATABASE_URL`, reached a Postgres on the same
    host:port that was an EMPTY scratch copy, and `scripts/migration_state.py`
    reported its revision as "the live database" with total confidence while the
    real one served 129 chat threads a socket away.

    So the value is served from inside the process that holds the connection,
    and `migration_state.py` prefers it over connecting itself.
    """
    try:
        with _db_engine().connect() as conn:
            return conn.execute(
                _sa_text("select version_num from alembic_version")).scalar()
    except Exception as exc:  # pragma: no cover - diagnostic path
        log.debug("live schema revision unavailable: %s", exc)
        return None


@app.get("/healthz")
async def healthz():
    """Liveness, plus what code is answering and what schema it is answering on.

    `ok` is unchanged for existing callers. `source_id` is what lets a restart be
    VERIFIED instead of assumed: before this, nothing the backend served said
    which version it was, so "did my change land" could only be guessed at from
    uptime -- and guessing wrong is how backend work has been reported shipped
    while the old code kept serving.

    `schema` is the same idea for the database, and for the same reason: it is
    the one number that says which schema the running code is actually sitting
    on, reported by the process that is sitting on it rather than inferred by
    whoever is asking. Null when the query fails, which is honest; a caller must
    not read null as agreement.

    `apps` is the third of the same kind, and it is the one that cost two days.
    Every app 404'd while this endpoint said `ok`, because nothing served said
    which installed-apps roster this process read or what it managed to mount
    off it -- and the roster path is anchored to the module's own location, so
    two checkouts of the same source on one host resolve two different files. An
    agent in a sandbox cannot read `/proc/<pid>/cwd` to settle it. The process
    can, so the process answers: the resolved path, the roster it read, and the
    app ids whose routers are actually mounted, recorded at mount time rather
    than re-read now.

    UNAUTHENTICATED, so it carries app ids and one filesystem path and nothing
    else -- no route table, no configuration, no data. That is the same
    disclosure the launcher already makes to any caller.
    """
    return {
        "ok": True,
        "source_id": _SOURCE_ID,
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
        "schema": _live_schema_revision(),
        "apps": getattr(app.state, "apps", None) or app_mount_record.unmounted(),
    }
