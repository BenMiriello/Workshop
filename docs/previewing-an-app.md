# Previewing an app: one verb, configured per app

**Status:** current. Written 2026-08-23, implementing section 3 of
[`platform-redesign/tools-are-declared-2026-08-23.md`](platform-redesign/tools-are-declared-2026-08-23.md).

Preview is the generic capability "load this page in a real browser, look at it,
and tell me what it complained about". It is one implementation for every app,
because the platform provides the verb and the app declares its particulars. That
is the same rule worktree provisioning already follows: the platform clones the
heavy inputs an app declares rather than naming `node_modules` in platform code.

    bash scripts/shoot.sh /memory                     # first declared viewport
    bash scripts/shoot.sh /memory 1440 900            # an explicit one
    bash scripts/shoot.sh /apps/birds/files 390 800 scratch/files.png
    bash scripts/shoot.sh --app projects/notes-app /  # a different app tree
    bash scripts/shoot.sh --config                    # what it resolved, and from where
    bash scripts/shoot.sh --setup                     # bootstrap only; normally implicit

First use installs the browser driver by itself, under the gitignored `scratch/`.
There is no step to remember.

**Read the PNG.** A screenshot nobody looked at is not evidence, it is a
confident report about a screen nobody saw. For UI work this is a gate, not
advice: see the "Look at it. In a browser." section of `frontend/CLAUDE.md`.

## The three pieces, and what each is allowed to know

| file | what it is | what it may name |
| --- | --- | --- |
| `scripts/shoot.mjs` | the verb | nothing app-specific. It loads a URL at a viewport, applies whatever auth it was handed, screenshots, and reports console errors, page errors and failed requests. Every value it uses arrives as JSON on stdin. |
| `scripts/preview_config.py` | the configuration | how to READ a declaration and what a stack implies. It executes nothing and reaches the network never. |
| `scripts/shoot.sh` | the caller and the driver bootstrap | how to install a named driver, and the Node traps below. It does not know which app it is shooting. |

If you find yourself adding a port, a cookie name, a user id or a browser version
to `shoot.mjs`, that value belongs in a declaration instead.

## Where an app declares its preview, in resolution order

1. **A `preview` block in `workshop.app.json`** (or `.jsonc`). This is where an
   app that the platform runs should declare it. It is read through
   `app_runner._read_manifest_file`, the one reader of that file, which already
   owns the filename set and the jsonc tolerance.
2. **`workshop.preview.json` beside it.** For a tree with no service manifest.
   The platform's own source tree is the case in point: systemd runs its
   services, not `app_runner`, so it has no `workshop.app.json`, and giving it
   one purely to hold a preview block would change how `resolve_manifest` treats
   the self project.
3. **The stack defaults below.** An app that declares nothing still gets a
   working preview if its stack is recognizable.

Every string may read the environment as `${VAR}` or `${VAR:-fallback}`, so a
machine's own origin stays in `~/.config/workshop/instance.env` and out of a
tracked file.

### The block

    "preview": {
      "origin": "http://127.0.0.1:5173",      // where a built instance answers
      "auth": { "mode": "none" },             // none | cookie | header | basic
      "viewports": [[390, 800], [1440, 900]],
      "touch_below": 900,                     // emulate touch under this width
      "device_scale_factor": 2,
      "settle_ms": 1200,
      "timeout_ms": 45000,
      "driver": { "name": "playwright-core", "browser": "chromium", "version": "" }
    }

Only `origin` is ever required, and only when the stack defaults cannot supply
one.

### Auth modes

| mode | fields | what happens |
| --- | --- | --- |
| `none` | | nothing is sent. A refused client lands on a login page, and a login page is a legible screenshot. |
| `cookie` | `name`, and `value` or `token_command` | the cookie is set for the origin. `token_command` is a shell command run by `shoot.sh` with the app dir as cwd; its output is cached under `scratch/preview/` at mode 600. |
| `header` | `name`, and `value` or `value_command` | sent as an extra HTTP header on every request. |
| `basic` | `username`, `password` | sent as HTTP basic credentials. |

The resolver never executes a command. Anything that runs, runs in the caller.

## The defaults table

One row per stack, first match wins. Adding a stack is a row in
`_STACK_DEFAULTS` in `scripts/preview_config.py`, not a code change. Print it
with `python3 scripts/preview_config.py --explain`.

| stack | detected by | default origin | why that |
| --- | --- | --- | --- |
| `vite` | `vite` in package.json deps | `http://127.0.0.1:5173` | Vite's own default dev port. An app the platform serves through its edge instead should declare the edge origin. |
| `next` | `next` in package.json deps | `http://127.0.0.1:3000` | Next's default dev port. |
| `node` | a package.json at all | `http://127.0.0.1:3000` | Node web servers overwhelmingly default to 3000, so it is the least surprising guess for a package.json with no framework we recognize. |
| `python-asgi` | `uvicorn`/`fastapi`/`starlette` in requirements.txt or pyproject.toml | `http://127.0.0.1:8000` | uvicorn's default port, which is what these apps are started on when nobody passes one. |
| `static` | an index.html and nothing else | `http://127.0.0.1:8080` | a plain directory of files has no server of its own, so this is the port of whatever static server is pointed at it. |
| none matched | | (refuses) | Nothing about the tree said how it is served, so it asks for a declaration by name rather than screenshotting a guess. |

And, for every stack, because these are properties of REVIEWING a page rather
than of the stack serving it:

| default | value | why |
| --- | --- | --- |
| `auth` | `none` | most apps serve a local dev instance unauthenticated, and guessing at a scheme is worse than a shot of a login page. |
| `viewports` | `[[390, 800], [1440, 900]]` | phone first: the operator reviews on a phone and that is where layout breaks first. The second is the widest thing worth checking. |
| `touch_below` | `900` | the breakpoint most responsive layouts in this tree switch at. Keep it in step with the app's own or the shot lies about which layout the operator sees. |
| `device_scale_factor` | `2` | so text is legible when a person zooms into the PNG. |
| `settle_ms` | `1200` | grace after networkidle for animations and late paints. |
| `timeout_ms` | `45000` | a cold dev server can take a while to compile its first route. |
| `driver` | `playwright-core`, chromium, version by Node major | see the traps below. |

## This repo's own entry

`workshop.preview.json` at the repo root. It declares the public edge as the
origin (the BUILT bundle, which is what the operator looks at), a session cookie
minted for the operator's user, the two viewports above, and 900 as the frontend's
own WIDE breakpoint. Nothing in that list appears in `shoot.mjs` or
`preview_config.py`: this repo is the first consumer of a general mechanism
rather than the only thing it works for.

## Two Node traps, pinned on purpose

Both live in the driver-bootstrap section of `scripts/shoot.sh`, with their
comments, because both cost an afternoon to find:

1. **`playwright-core` above 1.49 hard-refuses to start on Node 18**, with
   "requires Node.js 20 or higher". 1.49.1 is the last line that runs there, so
   on Node < 20 the version is pinned rather than floated. An app may still
   override `driver.version` in its declaration.
2. **A bare `import "playwright-core"` resolves the ROOT workspace's newer
   copy**, not the pinned one, because Node walks up from the importing file. So
   the pinned entry point is resolved to an absolute path in the bootstrap and
   handed to the runner. That is how a correctly pinned 1.49 install still died
   with the Node 20 message.

## What is deliberately not here

Preview is not yet registered as a `command` tool. That is stage 5 of
`tools-are-declared-2026-08-23.md`, on top of `services/tool_registry.py`, and it
is ticketed separately. The shape here is what that stage will register: a
generic verb plus a per-app declaration, so registering it adds a record rather
than a second implementation.
