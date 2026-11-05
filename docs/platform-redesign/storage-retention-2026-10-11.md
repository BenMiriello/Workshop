# Storage retention and limits

- Status: **stages 1-2 built (measure, retention pass), and the part of stage 3 that answers a short disk (section 6); the rest of 3 and stages 4-7 not built (7 = backups, section 8)**

Workshop writes far more than the user authors: dependency trees, build output,
model downloads, agents' throwaway probes, database snapshots, extra working
copies of branches. Nothing removed any of it. The scheduled reaper
(`services/gc.py`) only ran when called by hand and could not see a working copy
an agent created inside its sandbox, so an installation filled its disk with
copies of its own work. This is the system that keeps that bounded, for a local
install (the limit is the disk) and a hosted one (the limit is the plan).

## 1. What comparable systems do

| system | mechanism worth taking |
|---|---|
| Kubernetes image GC, BuildKit, Nix, ccache | evict caches between two watermarks (start at 85% of the budget, stop at 80%), least recently used first |
| journald | two knobs only: a max size and a keep-free floor (10-15% of the disk, capped) |
| Browser storage (StorageManager) | one quota for everything; each site's data is best-effort and evicted least-recently-used site first unless marked persistent |
| Codespaces, Gitpod | an idle workspace is deleted after a retention period (user setting, 30 days default); its work survives in git |
| Drive, Dropbox | deletion is soft for a recovery window, then final |

## 2. Four kinds of bytes

Every byte Workshop manages is exactly one of these, and the kind alone decides
what may happen to it.

| kind | examples | automatic action |
|---|---|---|
| `work` | committed files and history, app data (databases, outputs, uploads) | never deleted; a warning when it alone passes the limit |
| `rebuildable` | dependency dirs, build output, download and model caches, caches an app declares | evicted under pressure, least recently used first; the app rebuilds it |
| `throwaway` | `tmp/`, `scratch/`, logs, snapshots an app declares | expires after the retention period |
| `checkout` | any working copy of an app's repo other than the main one | removed when merged into the app's trunk and clean; parked when idle past the retention period |

Classification is declared, with safe defaults. An app's `workshop.app.json`
may carry a `storage` block naming its own paths:

```json
"storage": {"rebuildable": ["data/thumbs"], "throwaway": ["out/previews"]}
```

Without one, `tmp/` and `scratch/` at any depth are throwaway (the repo
convention already), well-known dependency and build directories are
rebuildable, and everything else is work. An unknown file is work, so the
default never deletes something the user wanted.

A name listed in a throwaway directory's `.keep` file (one per line) never
expires.

Host-wide tool caches (package managers, model hubs) belong to no app. They are
reported as one shared row and pruned with each tool's own command.

## 3. Sharing the limit between apps

No fixed per-app split: one app may grow while there is room. Under pressure,
space is taken back from the app furthest over its fair share first. Fair share
is the limit divided between apps by weight, and an app's weight decays with the
time since it was last used, so an app untouched for a month gives back before
the one in use today. Within an app, its least recently used rebuildable item
goes first. A protected app is never evicted from, and a per-app cap, when set,
is enforced even while the total is under the limit.

## 4. Settings

Four installation settings, plus two per app.

| setting | local default | hosted |
|---|---|---|
| Storage limit | the disk, less the keep-free floor | the plan's quota; may be set lower, never higher |
| Keep free on disk | 10% of the disk, at least 20 GB and at most 50 GB | not shown |
| Remove throwaway files and idle checkouts after | 14 days | same |
| Keep deleted items recoverable for | 30 days | same |
| per app: protect, cap | off, none | same |

A protected app is skipped by every automatic removal, not only eviction.

The plan quota is an installation fact, `WORKSHOP_STORAGE_QUOTA_GB`. Unset means
a local install bounded by its disk.

## 5. Deletion

Nothing is unlinked directly. A removal is a rename into a Workshop trash on the
same filesystem, recorded with its origin, its size and the reason; restore is
the reverse rename. The trash is emptied of entries older than the recovery
window by the same scheduled pass.

## 6. The retention pass

`services/storage/expire.py`, run daily from the backend schedule
(`scripts/storage_pass.py --apply`) and on demand (`POST /storage/cleanup`;
`GET` returns the same plan without acting). In order:

1. Throwaway: each entry of a throwaway directory whose newest file is older
   than the retention period goes to the trash.
2. Working copies the branch service owns (`wt/<app>/...`) are left to its own
   merged-branch reaper (`services/gc.py`), which this pass now also runs, so
   it no longer depends on someone calling it.
3. Every other working copy: kept when locked (`git worktree lock` is the way
   to keep one), in use, or recently active; removed when merged and clean;
   otherwise parked. Removing and parking both first save uncommitted work and
   unmerged commits to `refs/wip/storage/<name>-<date>`, the ref namespace the
   branch service already uses for work a machine took. The copy then goes to
   the trash and its entry under `.git/worktrees` is deleted. Never `git
   worktree prune`: a copy an agent made in its sandbox is recorded under the
   sandbox's path, so prune sees it as missing and deletes a live one's record.
4. The trash is emptied of entries past the recovery window.

When a disk holding an app, the workshop or a trash has less free than the
keep-free floor, the pass runs at once rather than waiting out its day (the
backend asks every 15 minutes), throwaway entries go after one day instead of
the retention period, and the trash deletes its oldest entries, recovery window
or not, until the floor is met. Moving something into a trash on the same disk
gives nothing back, so without this a full disk stayed full for the whole
window (measured 2026-11-03: 16 GB in the trash, nothing ever emptied, the disk
at 42 GB free).

"In use" is any process's working directory or open file under the path, read
from `/proc`, with sandbox paths mapped to the host.

## 7. Stages

1. Classify and measure: the storage service, the settings document, read and
   write routes. No deletion. Built.
2. Trash and the retention pass (section 6). Built.
3. The limit: watermarks, fair-share eviction of rebuildable data, shared cache
   pruning, the over-limit warning.
4. The Storage tab in Settings: usage by app and kind, the settings, clean up
   now.
5. Apps declare their `storage` blocks.
6. Snapshots: the helper, their retention, stray copies flagged (section 8).
7. Backups to a second disk (section 8).

## 8. Backups and snapshots

Measured on one installation in October 2026: the only scheduled backup was a nightly
mirror script that had never once completed (its target disk was full, then not mounted, and
the failures went unseen), and the other things that looked like backups were copies agents
made of an app's database before a risky change (11 GB each, several per week), left in the app's data folder, in `tmp/` and `scratch/`, and in
scratch checkouts on another disk, with no expiry. A copy on the same disk does not
protect against the disk failing; a copy nobody prunes fills it. The usual practice keeps
two separate things.

**Backups** protect against losing a disk or a mistake noticed late.

- Nightly, to a different disk or a remote store, never the disk being protected.
- Versioned, deduplicated and encrypted by the tool (restic or borg), so thirty nights of a
  large database cost little more than the changes between them.
- Databases captured consistently first: SQLite through its own backup API or
  `VACUUM INTO`, Postgres through `pg_dump`. Then the tool backs up the captures, the
  runtime directory and the user's own files.
- Pruned by the tool on a fixed policy (7 daily, 4 weekly, 6 monthly by default), as an
  installation setting.
- A restore is tested when the backup is first set up, and the last good backup time is
  shown on the Storage page.

**Snapshots** are a quick undo before one risky change, such as a migration.

- One helper takes them: `snapshot <app> <database> --reason <text>`, writing to
  `.runtime/storage/snapshots/<app>/`, never inside the app's tree.
- A new kind, `snapshot`, with count-and-age retention: the newest 2 per app, none older
  than 14 days, enforced by the retention pass. An app may declare a different count in
  its `storage` block.
- Once nightly backups exist, most snapshots are unnecessary: last night's backup is the
  undo.
- The retention pass reports database files outside the managed place that look like
  copies (same name or header as a database the app uses) on the Storage page. Copies
  under `tmp/` or `scratch/` already expire as throwaway.
- Agents are told to use the helper instead of copying a database by hand.
