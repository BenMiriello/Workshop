<!-- author: self; chat: 95f7fd7fe8f6413dbcea1b7e80d47979 -->
`cp -al` is the documented fast way to give a worktree its node_modules (0.11s for 317MB vs a 305MB reinstall, per branches-worktrees-and-environments-2026-08-20 §13.1). It is safe for READING only.

A hardlink is one inode with two names. Anything a build tool WRITES inside the worktree's node_modules writes through to `/workspace/node_modules` — the live tree the operator is using. Concretely dangerous:

- `npm install` / `npm ci` rewrites `node_modules/.package-lock.json` and can prune or move existing packages
- vite writes `node_modules/.vite`
- `tsc -b` writes `*.tsbuildinfo` (found one with 13 links)

Protocol that worked (2026-08-26, client-state-v1):
1. `cp -al` the three node_modules (root, frontend, packages/ui)
2. immediately `rm -rf` the shared caches: `node_modules/.vite`, `.cache`, `.tmp`, and un-hardlink any `*.tsbuildinfo` with a link count > 1
3. if the branch ADDS a dependency, do NOT install over the hardlink farm. Convert to real copies first (`cp -a --no-preserve=links`, ~1GB, ~30s), verify with `stat -c %h` that a sample file shows 1 link, then install.
4. checksum a canonical file (`md5sum /workspace/node_modules/.package-lock.json`) before and after every install and assert it did not move.

Also: a new dependency committed on a branch is NOT in the canonical node_modules after merging. Run `npm install` in /workspace BEFORE `apply.sh`, or the bundle build fails.

And `scripts/reviewer.sh` cannot be used inside a worktree at all: `check_no_hardcoding.sh` tests `[[ -d $dir/.git ]]`, which is false for a linked worktree's `.git` FILE, so it falls through to scanning node_modules and dist. Filed as 62e181fe.