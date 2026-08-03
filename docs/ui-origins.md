# Which URL serves the Workshop UI (and which build it is)

There are three UIs reachable on this box and they do NOT all serve the same
code. Resolve the origin before concluding that a frontend change did or did not
land — twice now that step was skipped and the wrong thing was "fixed".

| URL | Proxies to | Serves | Build step needed? |
|---|---|---|---|
| `https://debian.tail38e62e.ts.net:8443` | `127.0.0.1:5173` | Vite dev server, **from source** | **No** — edits are live on reload |
| `workshop.benmiriello.com` | cloudflared → `127.0.0.1:8002` | `frontend/dist`, the built bundle | **Yes**, but automatic — rebuilt on every commit that touches `frontend/` (below); by hand: `scripts/build_workshop_public.sh` |
| `https://debian.tail38e62e.ts.net:8447` | `127.0.0.1:8012` | **a different user's tree** (`/home/simonsays/…`) | not ours |

**`:8443` is the operator's usual UI.** `:8000/` (tailnet `:8446`) is the
backend and redirects there, so it is not a third UI, just a bounce.

## Why there are two of ours, on purpose

They are different surfaces, not accidental duplicates:

- The **tailnet** origin is the operator's admin UI. It runs from source so a
  change is visible immediately, which is what makes iterating on the UI in
  conversation possible at all.
- The **public** origin is the personal surface, behind the Cloudflare tunnel.
  `workshop_public_app.py` serves the built bundle and reverse-proxies only a
  safe API subset, blocking the loopback-trusted ingest routes so a public
  visitor cannot inherit the proxy's loopback trust.

A dev server has no place on the public origin, and the public edge's proxy
allowlist has no place in front of the admin UI. So both stay.

## What keeps them from diverging

It used to be a rule ("remember to run the build"), and a rule is exactly the
wrong mechanism: it depends on the person who already believes the work is
finished. It is now three pieces of machinery.

**1. A build id, defined once.** `scripts/ui_source_id.sh` hashes the inputs that
can change the bundle (`frontend/src`, `index.html`, the vite/ts configs,
`package.json`, `package-lock.json`) and prints a 12-char id. Everything that
compares a build to its source calls this one script. It hashes CONTENT, so
`git checkout` and `touch` do not move it, and it covers untracked files,
because an uncommitted new component still changes the bundle.

**2. Every build stamps itself.** `scripts/stamp_build.sh` writes
`frontend/dist/build-info.json` — source id, commit, branch, dirty flag, build
time. It is wired into npm's `prebuild`/`postbuild`, so `npm run build` stamps
just as `build_workshop_public.sh` does; there is no build path that skips it.
The id is captured BEFORE compiling, so a source edit during the build cannot
label a bundle with source it does not contain.

**3. Committing UI work rebuilds the bundle.** `.githooks/post-commit` (enable
once with `scripts/install_hooks.sh`) rebuilds in the background when a commit
touches `frontend/`. It refuses to run outside the canonical worktree or off the
default branch — a build from a linked worktree under `branches/` would publish
unmerged code to the public origin. Edge-code changes are NOT auto-applied: a
restart drops in-flight requests and chat SSE streams, so the hook prints the
command and leaves it to you.

## Checking, when something looks stale

```sh
bash scripts/ui_sync.sh          # source vs dist vs what :8002 actually serves
```

It asks each origin over HTTP for `/build-info.json`. Do not conclude anything
from `frontend/dist` on disk — that says nothing about what an origin returned.

Two traps this hit while being built, both worth knowing:

- The edge answers **200 with the SPA shell** for any path it has no file for,
  so `curl -sf .../build-info.json` "succeeds" against an origin with no stamp
  at all. Reachability and stamped-ness are separate questions; `ui_sync.sh`
  only trusts a body that actually carries a `source_id`.
- `check_design.sh` (run before every UI change) calls `ui_sync.sh --warn`, and
  warns if `core.hooksPath` is unset — a tree where the automation is off says
  so instead of quietly drifting. `--strict` exits 1 for a gate.

To see what a bundle contains rather than what it claims:

```sh
curl -s http://127.0.0.1:8002/ | grep -oE '/assets/[^"]+\.css'   # the hash it references
curl -s http://127.0.0.1:8002/assets/<hash>.css | grep -c ws-truncate
curl -s http://127.0.0.1:5173/src/styles/components.css | grep -c ws-truncate
```

`tailscale serve status` lists ports **unsorted**. Read all of it; `head` will
cut off the row you need. That is how `:8443` got missed and a live security
finding was closed by mistake.
