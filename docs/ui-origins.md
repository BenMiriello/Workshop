# Which URL serves the Workshop UI (and which build it is)

There are three UIs reachable on this box and they do NOT all serve the same
code. Resolve the origin before concluding that a frontend change did or did not
land — twice now that step was skipped and the wrong thing was "fixed".

| URL | Proxies to | Serves | Build step needed? |
|---|---|---|---|
| `https://debian.tail38e62e.ts.net:8443` | `127.0.0.1:5173` | Vite dev server, **from source** | **No** — edits are live on reload |
| `workshop.benmiriello.com` | cloudflared → `127.0.0.1:8002` | `frontend/dist`, the built bundle | **Yes** — `scripts/build_workshop_public.sh` |
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

## The rule that keeps them from diverging

**Every frontend change ends with `scripts/build_workshop_public.sh`.** The
tailnet origin needs nothing; the public one is stale until that runs. Skipping
it is the only way these two drift, and the drift is invisible from the origin
you happen to be looking at.

## Checking, when something looks stale

Do not inspect `frontend/dist` on disk — that says nothing about what a given
origin returned. Fetch from the origin and grep what it actually served:

```sh
curl -s http://127.0.0.1:8002/ | grep -oE '/assets/[^"]+\.css'   # the hash it references
curl -s http://127.0.0.1:8002/assets/<hash>.css | grep -c ws-truncate
curl -s http://127.0.0.1:5173/src/styles/components.css | grep -c ws-truncate
```

`tailscale serve status` lists ports **unsorted**. Read all of it; `head` will
cut off the row you need. That is how `:8443` got missed and a live security
finding was closed by mistake.
