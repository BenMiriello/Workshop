# Session follow-ups — deferred to 2026-07-12 ~05:56 PDT (operator "4-hour timer")

The operator paused mid-implementation and asked to resume in 4 hours, addressing
**all** of the following (not just the resilience feature). Do these in order,
verify each in the RUNNING app (not just typecheck), and commit as you go.

Deploy discipline (this repo): frontend changes → `bash scripts/build_workshop_public.sh`
(no edge restart). Backend changes → `bash scripts/restart_backend.sh --deferred`.
Gate before "done": `bash scripts/check_design.sh` + `cd frontend && npx tsc --noEmit`.

---

## A. Restart/crash resilience for chat turns  ← the main feature (planned, NOT built)

Goal: a build/voice turn must survive a backend restart — persist, re-attach, and
keep running; and the UI must never get stuck "reconnecting…" forever.

Root cause today: `claude` is spawned as a child of uvicorn with an in-memory
PIPE, and `Turn` state lives in `build_streaming.TURNS` (RAM). On restart the child
dies (reader gone) and the turn record vanishes; `get_turn()` → None → the SSE
endpoint 404s and the message stays `meta.streaming=true` forever.

Design (detached + file-backed journal + re-attach on boot):
1. **Detach the subprocess**: spawn with `start_new_session=True` (setsid) so
   `pkill -f uvicorn` on restart doesn't take it down.
2. **Journal to disk** (per-owner, mirrors chat_store's `user_runtime_dir()/"chat"`):
   `ownership.user_runtime_dir()/"turns"/<turn_id>/` with
   - `meta.json`: {turn_id, thread_id, agent_slug, cwd, model, session_id, pid,
     status: running|done, started, owner_id}
   - `stream.jsonl`: the claude stdout (redirect subprocess stdout to this fd, not PIPE)
   - `stream.err`: stderr
   The turn engine **tails** `stream.jsonl` (async line reader) instead of reading
   a pipe — identical code path whether we just spawned it or are re-attaching.
3. **Re-attach on boot** — add a boot-once hook in `backend/main.py` lifespan
   (before `yield`, ~line 152). Iterate `.runtime/users/*/turns/*/meta.json` with
   `status=running`:
   - pid alive (`os.kill(pid, 0)`) → rebuild the in-memory `Turn`, replay existing
     `stream.jsonl` lines so a reconnecting client (Last-Event-ID) catches up, and
     resume tailing. **Turn keeps going.**
   - pid dead → finalize from journal + `finalize_streaming_assistant(...)`.
4. **Startup safety net** — sweep `.runtime/users/*/chat/*.json`; any message with
   `meta.streaming=true` whose turn is not recoverable → finalize with a calm note:
   "Server restarted — the work up to here is saved. Send 'continue' to resume."
   (There's precedent: the per-thread self-heal at `routes/chat.py:172-187` clears
   orphaned streaming flags on GET; generalize that to a boot sweep across owners.
   Note `current_owner()` raises when unpinned — iterate `_RUNTIME/users/*` dirs
   directly, don't rely on a pinned owner at boot.)
5. **Voice**: `voice_worker` uses a resident stdin process (`WORKERS`). At minimum
   apply the startup safety net so voice threads don't hang; ideally give voice-build
   turns the same journal treatment.
6. **Restart script**: confirm `scripts/restart_backend.sh` (`pkill -f uvicorn`)
   leaves detached claude children alive (setsid removes them from uvicorn's group).
7. **Data turns** (blocking `chatApi.sendMessage`): quick; lower priority. Note the
   gap; a restart mid-data-turn just needs a resend. Don't over-invest.

Integration points (from a code map — verify before editing):
- `backend/main.py:126-157` — FastAPI lifespan; add boot hook before `yield`.
- `backend/services/build_streaming.py` — `start_turn`/`_run_turn` spawn (215-225
  is the `create_subprocess_exec`), `_read_stdout` pipe reader → switch to file tail;
  `TURNS`, `get_turn`, `iter_events`. Persist meta.json around spawn.
- `backend/routes/chat.py:769+` — `/turns/{turn_id}/stream` 404s on missing turn;
  after re-attach it finds the rebuilt turn.
- `backend/services/chat_store.py:161-232` — append/update/finalize streaming
  assistant; `meta.streaming` + `meta.turn_id` are the markers; `_find_by_turn`.
- `backend/services/ownership.py:68-73` — `user_runtime_dir()`; `_RUNTIME` at :29.
- Frontend already re-attaches on `meta.streaming` (`Chat.tsx` hydrate ~556-575,
  `handleDrop`/MAX_RECONNECTS=8 ~527-549). No change needed if server re-attach works;
  consider widening the retry window so the ~5s restart gap doesn't exhaust budget.

**VERIFY FOR REAL**: start a build turn (e.g. a blank builder: "create 5 files with
sleeps between"), `bash scripts/restart_backend.sh` mid-turn, confirm (a) the claude
proc stayed alive, (b) after boot the turn re-attached and finished, (c) files landed,
(d) the UI resolved (no infinite reconnect). A scratch integration test like
`backend/scratch/test_voice_build.py` is a good model.

## B. Blank-project builder chat — formatting bug (NEW, operator-reported)

Symptom: in the blank-project builder chat the content is **too wide and allows
horizontal scroll into empty blank space**; it "seems different in odd ways" and
feels like a different, worse chat with fewer features. It must be **fundamentally
the same chat** as any other builder chat.

Both blank and normal builders route `/chat/build:<slug>` → `FocusedChat` → `Chat`,
so investigate WHY it differs:
- A child forcing width (a `<pre>`/code block, a tool segment, a long unbroken
  token) without `min-width:0` / `overflow-x`/`word-break` on the chat log / message
  container → horizontal scroll into blank space. Grep `.chat-log`, message, and
  segment rules in `frontend/src/styles.css`; ensure `min-width:0` on flex children
  and `overflow-wrap/break-word` on content, `overflow-x` contained.
- "fewer features": check what renders differently for a **freestanding** agent
  (no `project_slug`) vs a project-linked one — toolbar, task panel, workbench button,
  model/provider controls. Make them identical.
- Empty-state (no messages yet) may render wide — check.
Fix so a freestanding blank builder chat is pixel-identical in layout to a linked
builder chat, with no horizontal overflow.

## C. Desktop embedded (workbench) chat — bounded height (verify/finish)

Earlier operator request: the workbench-embedded chat must be bounded on desktop too
("higher max height but still always bounded by the height of the page"). Mobile was
bounded to 70vh. Verify desktop is bounded; if not, bound it. Files: `AgentBuild.tsx`
embedded `<Chat>` + `.ws-agent-build__chat` in `frontend/src/styles/components.css`.

## D. Audit ALL prior requests from this session are actually live & correct

Walk the running app and confirm each is real (not just committed):
draft persistence/sync; buttons-below-textarea; working indicator (Working→dots→time);
SSE reconnect + edge SSE passthrough; no 502 HTML wall (shortHttpError); first-tap/Enter
send; all agents have web (WebSearch/WebFetch) + tools; blank-project builders (real,
incrementing, renamable, in Agents list); voice-driven building; audio buttons cleaned
(no dictation mic, voice only where it works). Fix anything that regressed.

## E. Voice+text SHARED session per builder  ← now WANTED (a test encodes it)

`backend/scratch/test_voice_build.py` was edited (by the operator / a parallel agent)
to assert `shared_session`: the voice worker's `session_id` MUST equal the build
agent's own `claude_session` (from `build_agents.ensure_session_id(slug)`), i.e. voice
and text share ONE continuous brain per builder — not the throwaway session v1 used.
The test currently FAILS this assertion. Implement it:
- In `voice_worker.ensure_worker`, for a build context adopt the agent's session:
  `sid, is_new = build_agents.ensure_session_id(slug)`; set `worker.session_id = sid`
  and spawn with `--resume` when not new.
- Concurrency safety: the resident voice process holds the session open, while the
  text build path spawns `claude -p --resume <same sid>` per turn. Two processes on
  one session file can corrupt it. Guard against simultaneous voice+text turns on the
  same agent (single-flight per agent session, or tear down the resident voice worker
  before a text turn resumes / vice-versa). Design this deliberately.
- Make `test_voice_build.py` PASS (it checks file created + narration + shared_session).

---

Context: this brief exists because the resume timer (CronCreate) is session-only. A
project memory (`pending-restart-resilience-and-followups`) also points here.
