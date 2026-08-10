# Importing Apple Notes into the Workshop Notes app

Status: built and tested (2026-07-14). Two scripts, both committed in the repo
under `scripts/`. The importer is stdlib-only and idempotent, so this is a
repeatable pipeline, not a one-shot.

## The goal

Get every Apple Note into the Workshop Notes vault as clean Markdown, without
lock-in, and be able to re-run it later to pull in new/changed notes without
duplicating anything.

## How the vault works (what we're importing into)

The Notes app is backed by `backend/services/notes_vault.py`. The vault is just a
folder of UTF-8 Markdown files:

- Location: `$WORKSHOP_NOTES_VAULT`, default `~/notes` (i.e.
  `$HOME/notes`). Plain `.md` files, arbitrary subfolders.
- `[[wikilinks]]` between notes (Obsidian / SilverBullet semantics).
- A note's title is its first `# ` heading, else the filename.
- No database. The Notes UI reads these files directly, so anything the importer
  writes shows up in the app immediately.

## The two scripts

| File | Runs on | What it does |
|---|---|---|
| `scripts/export_apple_notes.applescript` | your **Mac** | Dumps Apple Notes to `~/AppleNotesExport/<Folder>/<Title>.html`, one file per note, each with a metadata header the importer reads. |
| `scripts/import_apple_notes.py` | the **Workshop box** (as `the service user`) | Converts that export into vault `.md` notes. Stdlib only, idempotent. |

Absolute paths on the box:
- `$WORKSHOP/scripts/export_apple_notes.applescript`
- `$WORKSHOP/scripts/import_apple_notes.py`

## Why an AppleScript (not a third-party app)

Apple Notes has no clean bulk-Markdown export. The AppleScript gives us an export
in exactly the shape the importer wants, with a stable per-note id we use for
idempotency, and needs no third-party app. (The importer also accepts plain
`.md`/`.txt` exports, e.g. from the "Exporter" Mac app, if you ever prefer that.)

## Procedure

### 1. Export (on your Mac)

Get the script onto the Mac and run it:

```bash
scp the service user@<host>.<tailnet>:$WORKSHOP/scripts/export_apple_notes.applescript ~/
osascript ~/export_apple_notes.applescript
```

- First run pops a one-time "wants to control Notes" permission prompt: allow it.
- Near the top of the script, `set targetFolder to "Notes"` scopes the export to a
  single Apple Notes folder. Set it to `""` to export every folder.
- Output lands in `~/AppleNotesExport/`. It ends with an "Exported N notes" dialog.
- `osascript` and Apple Notes exist only on macOS. Run this on the Mac, not the
  Linux box (if your shell's home is `/home/the operator`, that's the box).

### 2. Transfer the export to the Workshop box

Any method works. Over the tailnet from the Mac:

```bash
rsync -av ~/AppleNotesExport/ the service user@<host>.<tailnet>:~/apple-notes-export/
```

### 3. Import (on the box)

```bash
# preview - writes nothing:
python3 $WORKSHOP/scripts/import_apple_notes.py ~/apple-notes-export --dry-run

# do it:
python3 $WORKSHOP/scripts/import_apple_notes.py ~/apple-notes-export
```

Notes land under `~/notes/Apple Notes/<Folder>/<Title>.md`. Use `--into ""` to
import at the vault root instead of under an "Apple Notes" folder.

## Idempotency and safety (why re-running is safe)

Identity is the stable Apple note id from the export header, tracked in a
manifest at `~/notes/.import/apple-notes-manifest.json` (a dot-dir, so the Notes
UI ignores it). On every run:

- **Unchanged note** -> skipped.
- **Apple source changed** -> skipped by default; pass `--update` to apply it.
- **You edited the note in the vault** -> never overwritten, even with `--update`.
  `--force` overrides that.
- **Title collisions** (same title, different folders) are de-collided
  deterministically, so re-runs are stable.

So the ongoing workflow is: re-export on the Mac, re-transfer, re-run the import
(add `--update` if you want Apple-side edits to flow in). New notes get created,
nothing duplicates.

## What converts

The importer has a built-in HTML->Markdown converter for the subset Apple Notes
emits: headings, paragraph/line breaks, **bold** / *italic* / ~~strike~~,
bulleted and numbered lists, links, blockquotes, and code. Verified with
`python3 scripts/import_apple_notes.py --self-test` plus an end-to-end run.

## Dates

Each note's real `created` / `modified` timestamps ride in the export header and
are preserved two ways: as YAML frontmatter at the top of the `.md`, and by
setting the file's mtime to the modified date (so the Notes UI shows the true
"edited" date, not the import date). Apple keeps no per-note edit *history* -
only those two endpoints exist, so intermediate revisions can't be recovered.

## Known limitations

- **Attachments/images are not migrated** (text and structure only). The
  AppleScript `body` strips media out entirely - it leaves no marker at all, so
  the text export carries no signal about which notes had images. Pulling them is
  a separate job that must read the source on the Mac:
  `~/Library/Group Containers/group.com.apple.notes/` - `NoteStore.sqlite` holds
  the note<->attachment mapping, `Accounts/<uuid>/Media/` holds the files. Plan:
  copy that container to the box, run a stdlib-`sqlite3` extractor that joins each
  image to its parent note's Apple id (the same id we import by) and drops it into
  the matching vault note. Not built yet.
- **Nested Apple folders flatten** to their leaf folder name.

## Flags reference

```
python3 scripts/import_apple_notes.py <export_dir> [options]
  --vault PATH   vault root (default $WORKSHOP_NOTES_VAULT or ~/notes)
  --into NAME    vault subfolder to import under (default "Apple Notes"; "" = root)
  --update       apply Apple-side changes to notes we previously imported
  --force        overwrite even notes you edited in the vault
  --dry-run      print the plan, write nothing
  --self-test    run the converter tests and exit
```

## Commits

- `13d406a` - importer + exporter added.
- `6ef37fa` - fix AppleScript `trim` crash on single-character titles.
