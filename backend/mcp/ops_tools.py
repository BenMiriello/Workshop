"""The operation verbs on the workshop tool server: status, logs, start, stop,
restart, for any app, from any build chat.

They run in the backend (`services/app_ops.py`), which is the process that owns
the running apps, so they are offered only over the HTTP transport; a stdio
child of a turn has no runner to operate. Which app a chat may operate comes
from its signed scope claim, not from the call.
"""
from __future__ import annotations

from mcp import turn_context

STATUS_NAME = "app_status"
LOGS_NAME = "app_logs"
CONTROL_NAME = "app_control"
BUILD_NAME = "app_build"
TEST_NAME = "app_test"
CHECK_NAME = "app_check"
APPLY_NAME = "app_apply"
ENV_NAME = "app_env"
PREVIEW_NAME = "app_preview"
AGENT_NAME = "app_agent"
RUN_CAPPED_NAME = "run_capped"
ALL_NAMES = (STATUS_NAME, LOGS_NAME, CONTROL_NAME, BUILD_NAME, TEST_NAME, CHECK_NAME,
             APPLY_NAME, ENV_NAME, PREVIEW_NAME, AGENT_NAME, RUN_CAPPED_NAME)

_APP = {"type": "string",
        "description": ("The app's slug, or `workshop` for the platform itself. Leave out in "
                        "an app's own build chat to mean that app.")}

TOOLS = [
    {
        "name": STATUS_NAME,
        "description": (
            "What an app is doing right now: each service's state, health, port and "
            "pid, its last error, and whether its repo has uncommitted changes. Use "
            "this instead of probing ports or reading process lists."
        ),
        "inputSchema": {"type": "object", "properties": {"app": _APP},
                        "additionalProperties": False},
    },
    {
        "name": LOGS_NAME,
        "description": "The end of one service's log (its build output and what it printed while running).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": _APP,
                "service": {"type": "string", "description": "The service, as app_status names it."},
                "max_chars": {"type": "integer", "description": "How much of the end to return (default 8000)."},
            },
            "required": ["service"],
            "additionalProperties": False,
        },
    },
    {
        "name": CONTROL_NAME,
        "description": (
            "Start, stop or restart an app, or restart one of its services. This is how "
            "a change goes live: never kill processes or start servers by hand. A "
            "restart runs the service's declared preflight check first, swaps in the "
            "new code (gracefully, when the app declares how), and waits until the "
            "service is healthy, then reports the old and new pid; if it does not come "
            "back it reports the error and the end of the log. A restart is refused "
            "while the app's repo has uncommitted changes, because it would load work "
            "nobody has committed; commit first, or pass include_uncommitted when the "
            "changes are your own and ready to run. Can take minutes for a service that "
            "hands over gracefully."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": _APP,
                "action": {"type": "string", "enum": ["start", "stop", "restart"]},
                "service": {"type": "string",
                            "description": "For restart: just this service. Default: every running service."},
                "include_uncommitted": {"type": "boolean",
                                        "description": "Restart even though the repo has uncommitted changes."},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },

    {
        "name": APPLY_NAME,
        "description": (
            "Make a running app match its source after a change: run its declared "
            "migration, build what it declares, and restart what is running (with the "
            "same checks and health wait as app_control). For the platform (`workshop`) "
            "this is the platform's whole apply step; its own backend restart is "
            "deferred until this turn's reply lands, so it never cuts this turn off. "
            "This is the one way to make a finished change live."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": _APP,
            "include_uncommitted": {"type": "boolean",
                                    "description": "Proceed although the repo has uncommitted changes that are yours."},
        }, "additionalProperties": False},
    },
    {
        "name": BUILD_NAME,
        "description": (
            "Run an app's declared build, for one service or all of them, without "
            "restarting anything. Pass setup to reinstall its dependencies first."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": _APP,
            "service": {"type": "string", "description": "Just this service."},
            "setup": {"type": "boolean", "description": "Run the declared dependency setup first."},
        }, "additionalProperties": False},
    },
    {
        "name": TEST_NAME,
        "description": (
            "Run an app's declared tests (for `workshop`, the platform's suites), always "
            "under a memory ceiling with no swap and at low CPU priority, so a runaway "
            "suite cannot take the machine down. Use this rather than running a whole "
            "suite by hand. args are passed to the declared command as separate "
            "arguments, e.g. one test file."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": _APP,
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "Arguments for the test command, such as a file or a filter."},
        }, "additionalProperties": False},
    },
    {
        "name": CHECK_NAME,
        "description": (
            "Run every check an app declares (for `workshop`: typecheck, design system, "
            "prompt layers and the platform gates) and report pass or fail for each, "
            "with the output of any that failed."
        ),
        "inputSchema": {"type": "object", "properties": {"app": _APP},
                        "additionalProperties": False},
    },
    {
        "name": ENV_NAME,
        "description": (
            "List the settings and secrets an app declares and whether each is set "
            "(never their values), or save values for declared keys. A running "
            "service sees new values after a restart."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": _APP,
            "set": {"type": "object", "additionalProperties": {"type": "string"},
                    "description": "Values to save, by declared key."},
        }, "additionalProperties": False},
    },
    {
        "name": PREVIEW_NAME,
        "description": (
            "Screenshot one page of an app as the operator's browser renders it, signed "
            "in the way the app declares, and report what the page complained about "
            "(console errors, failed requests). Returns the picture. Use this to check "
            "a visual change instead of writing a screenshot script."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": _APP,
            "path": {"type": "string", "description": "The page, e.g. / or /settings."},
            "width": {"type": "integer", "description": "Viewport width (default: the app's first declared viewport)."},
            "height": {"type": "integer", "description": "Viewport height."},
        }, "additionalProperties": False},
    },
    {
        "name": AGENT_NAME,
        "description": (
            "Hand work on an app to an agent that works in that app's own folder, "
            "under its own rules and repo, with its own app_* tools. It runs in the "
            "background and its report arrives in this chat as a later turn."
        ),
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string", "description": "The app's slug."},
            "task": {"type": "string",
                     "description": "What to do, with everything the agent needs: it does not see this chat."},
        }, "required": ["app", "task"], "additionalProperties": False},
    },
    {
        "name": RUN_CAPPED_NAME,
        "description": (
            "Run one heavy command (a build, a data job, anything that could use a lot "
            "of memory) inside this chat's own view of the files, with a memory "
            "ceiling, no swap and low CPU priority, so a runaway is stopped alone "
            "instead of stopping this turn or the machine. Returns its exit status and "
            "the end of its output. Declared tests and checks have app_test and "
            "app_check; this is for everything else."
        ),
        "inputSchema": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The shell command, run from this chat's working folder."},
            "memory": {"type": "string", "description": "Memory ceiling, e.g. 4G (default 8G)."},
            "timeout_s": {"type": "integer", "description": "Seconds before it is stopped (default 1800)."},
        }, "required": ["command"], "additionalProperties": False},
    },
]


def offered() -> bool:
    return turn_context.over_http()


def _target(args: dict) -> str:
    scope = turn_context.get("WORKSHOP_QUERY_SCOPE")
    slug = str(args.get("app") or "").strip()
    if not slug:
        kind, _, own = scope.partition(":")
        if kind != "project" or not own:
            raise ValueError("name the app to operate (app).")
        slug = own
    from services import app_ops
    app_ops.authority(scope, slug)
    return slug


def _with_picture(text: str, shot) -> list[dict]:
    """The report as content blocks, the picture inline when it is small enough
    to send, and where the chat can open it again."""
    blocks: list[dict] = []
    if shot is not None:
        from services import scoped_exec
        root = turn_context.get("WORKSHOP_QUERY_ROOT")
        text += f"\nSaved as {scoped_exec.sandbox_path(str(shot), root) if root else shot}"
        raw = shot.read_bytes()
        if len(raw) <= 4_000_000:
            import base64
            blocks.append({"type": "image", "mimeType": "image/png",
                           "data": base64.b64encode(raw).decode()})
    return [{"type": "text", "text": text}, *blocks]


def _hand_off(args: dict) -> str:
    scope = turn_context.get("WORKSHOP_QUERY_SCOPE")
    slug = str(args.get("app") or "").strip()
    task = str(args.get("task") or "").strip()
    if scope != "development":
        raise ValueError("handing work to an app's agent is for the development chat; "
                         "an app's own chat does the work itself.")
    thread = turn_context.get("WORKSHOP_QUERY_THREAD").strip()
    owner = turn_context.get("WORKSHOP_QUERY_OWNER").strip()
    if not thread or not owner.isdigit():
        raise ValueError("this call is not bound to a chat, so there is nowhere to report back.")
    if not slug or not task:
        raise ValueError("name the app and describe the task.")
    from services import app_runner, background_runs
    if not (app_runner.project_dir(slug) / "workshop.app.json").exists():
        raise ValueError(f"{slug} is not an app with its own folder and manifest.")
    prompt = (f"You are working on the app {slug}, in its own folder, at the request of "
              f"another chat, which cannot see what you do except through your final "
              f"report. The request:\n\n{task}\n\nDo the work in this app's own code and "
              f"repo, following its agent rules. Commit what you change in this app's "
              f"repo, make it live with app_apply, check it, and end with a short report: "
              f"what changed, how you checked it, and anything left undone.")
    job = background_runs.launch(thread_id=thread, owner_id=int(owner), scope=f"project:{slug}",
                                 prompt=prompt, label=f"{slug}: {task.splitlines()[0][:50]}")
    return (f"Handed to {slug}'s own agent (job {job}). It works in {slug}'s folder and its "
            "report will arrive in this chat as a later turn. Tell the operator in one line "
            "and carry on.")


def _run_capped(args: dict) -> tuple[str, bool]:
    import os
    from pathlib import Path
    from services import app_commands, command_tools, scoped_exec
    from services.app_runner import AppCommand
    root = turn_context.get("WORKSHOP_QUERY_ROOT")
    command = str(args.get("command") or "").strip()
    if not root or not command:
        raise ValueError("a command is needed, and this chat needs a working folder to run it in.")
    pattern = command_tools.forbidden_pattern(["bash", "-c", command])
    if pattern is not None:
        raise ValueError(f"that command matches the irreversible-action deny list ({pattern}).")
    timeout = int(args.get("timeout_s") or 1800)
    cmd = AppCommand(command="exec \"$@\"", name="run", timeout_s=max(10, min(timeout, 7200)),
                     memory=str(args.get("memory") or "8G"))
    inner = scoped_exec.spawn_argv(["bash", "-c", command], root)
    ok, out, secs = app_commands.run_capped(
        "run", cmd, cwd=Path(root), env=scoped_exec.turn_env(dict(os.environ), root), args=inner)
    return f"{'exit 0' if ok else 'FAILED'} after {secs:.0f} s\n{out}", not ok


def run(name: str, args: dict) -> tuple[str | list[dict], bool]:
    """(text, failed). A refusal or a failed operation is a failed tool call
    whose text says what happened and what was left untouched."""
    from services import app_ops
    try:
        if name == AGENT_NAME:
            return _hand_off(args), False
        if name == RUN_CAPPED_NAME:
            return _run_capped(args)
        slug = _target(args)
        if name == STATUS_NAME:
            return app_ops.status(slug), False
        if name == LOGS_NAME:
            return app_ops.logs(slug, str(args.get("service") or ""),
                                int(args.get("max_chars") or 8000)), False
        if name in (BUILD_NAME, TEST_NAME, CHECK_NAME, APPLY_NAME, ENV_NAME):
            from services import app_commands
            if name == BUILD_NAME:
                return app_commands.build(slug, (args.get("service") or None),
                                          setup=bool(args.get("setup")))
            if name == TEST_NAME:
                return app_commands.test(slug, [str(a) for a in (args.get("args") or [])])
            if name == CHECK_NAME:
                return app_commands.check(slug)
            if name == APPLY_NAME:
                return app_commands.apply(
                    slug, include_uncommitted=bool(args.get("include_uncommitted")))
            return app_commands.env(slug, args.get("set") or None), False
        if name == PREVIEW_NAME:
            from services import app_commands
            text, shot, failed = app_commands.preview(
                slug, str(args.get("path") or "/"), width=args.get("width"),
                height=args.get("height"),
                out_root=turn_context.get("WORKSHOP_QUERY_ROOT") or None)
            return _with_picture(text, shot), failed
        action = args.get("action")
        if action == "start":
            return app_ops.start(slug), False
        if action == "stop":
            return app_ops.stop(slug), False
        if action == "restart":
            return app_ops.restart(slug, (args.get("service") or None),
                                   include_uncommitted=bool(args.get("include_uncommitted"))), False
        return f"unknown action {action!r}: use start, stop or restart.", True
    except (app_ops.Refused, ValueError) as e:
        return f"Refused: {e}", True
    except Exception as e:  # noqa: BLE001 - an operation that failed says so
        return f"Failed: {e}", True
