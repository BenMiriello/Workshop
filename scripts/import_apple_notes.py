#!/usr/bin/env python3
"""Import an Apple Notes export into the Workshop Notes vault.

The vault is a plain folder of UTF-8 Markdown files with `[[wikilinks]]` and
arbitrary subfolders (see backend/services/notes_vault.py). This script turns an
export produced by scripts/export_apple_notes.applescript (a tree of `.html`
files, each with a `<!-- workshop-apple-note ... -->` metadata header) into clean
`.md` notes there.

It is:
  * one-off      - run it whenever; it needs nothing running.
  * idempotent   - re-running never duplicates. Unchanged notes are skipped; a
                   note whose Apple source changed is updated in place only with
                   --update, and a note you edited in the vault is never clobbered
                   (unless --force). Identity is the stable Apple note id from the
                   header, tracked in a manifest under the vault's `.import/` dir.
  * reusable     - stdlib only, no venv, no installs. Also accepts plain `.md` /
                   `.txt` exports (e.g. from the "Exporter" Mac app), so it works
                   even if you didn't use the bundled AppleScript.

Usage:
    python3 scripts/import_apple_notes.py ~/apple-notes-export
    python3 scripts/import_apple_notes.py ~/apple-notes-export --dry-run
    python3 scripts/import_apple_notes.py ~/apple-notes-export --update
    python3 scripts/import_apple_notes.py --self-test

Options:
    --vault PATH   Vault root. Default: $WORKSHOP_NOTES_VAULT or ~/notes.
    --into NAME    Subfolder inside the vault to import under. Default
                   "Apple Notes". Pass "" to import at the vault root.
    --update       Overwrite notes we previously imported whose Apple source has
                   since changed (still never touches vault-side edits).
    --force        Overwrite the target even if it was edited in the vault.
    --dry-run      Print the plan; write nothing.
    --self-test    Run the built-in converter tests and exit.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


# --------------------------------------------------------------------------- #
# HTML -> Markdown (the subset Apple Notes emits: headings, div/p lines, br,
# bold/italic/strike/underline, ul/ol lists, links, blockquote, pre/code).
# --------------------------------------------------------------------------- #
class _Html2Md(HTMLParser):
    _HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}
    _BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: str = ""
        self.prefix: str = ""           # block prefix for the current line (heading / bullet / quote)
        self.list_stack: list[list] = []  # each: ["ul"|"ol", counter]
        self._a_href: str | None = None
        self._a_start: int = 0
        self._pre = 0

    # -- line handling -----------------------------------------------------
    def _emit(self, allow_blank: bool) -> None:
        """Append the current buffer as one output line. `allow_blank` decides
        whether an empty buffer still produces a blank line (true for div/p/br so
        Apple's `<div><br></div>` paragraph breaks survive; false at a block start
        or for empty headings/bullets, which would otherwise inject stray blanks
        between adjacent content blocks)."""
        text = self.buf if self._pre else self.buf.strip()
        if text:
            self.lines.append(self.prefix + text)
        elif allow_blank:
            self.lines.append("")
        self.buf = ""

    def _start_block(self, prefix: str = "") -> None:
        self._emit(allow_blank=False)  # flush inline text outside a block, no blank
        self.prefix = prefix

    def _end_block(self, div_like: bool) -> None:
        self._emit(allow_blank=div_like)
        self.prefix = ""

    def _list_bullet(self) -> str:
        if not self.list_stack:
            return "- "
        kind = self.list_stack[-1][0]
        indent = "  " * (len(self.list_stack) - 1)
        if kind == "ol":
            n = self.list_stack[-1][1]
            self.list_stack[-1][1] += 1
            return f"{indent}{n}. "
        return f"{indent}- "

    # -- tags --------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self._emit(allow_blank=True)  # line break; keep the block prefix
            return
        if tag in self._HEADINGS:
            self._start_block(self._HEADINGS[tag]); return
        if tag in ("p", "div", "tr"):
            self._start_block(""); return
        if tag == "blockquote":
            self._start_block("> "); return
        if tag in ("ul", "ol"):
            self._emit(allow_blank=False)
            self.list_stack.append([tag, 1])
            return
        if tag == "li":
            self._start_block(self._list_bullet()); return
        if tag in ("b", "strong"):
            self.buf += "**"; return
        if tag in ("i", "em"):
            self.buf += "*"; return
        if tag in ("s", "strike", "del"):
            self.buf += "~~"; return
        if tag == "pre":
            self._emit(allow_blank=False); self.lines.append("```"); self._pre += 1; return
        if tag == "code":
            self._pre += 1; self.buf += "`"; return
        if tag == "a":
            for k, v in attrs:
                if k == "href":
                    self._a_href = v
            self._a_start = len(self.buf)
            return
        if tag == "hr":
            self._emit(allow_blank=False); self.lines.append("---"); return

    def handle_endtag(self, tag):
        if tag in self._HEADINGS or tag in ("blockquote", "li"):
            self._end_block(div_like=False); return
        if tag in ("p", "div", "tr"):
            self._end_block(div_like=True); return
        if tag in ("ul", "ol"):
            self._emit(allow_blank=False)
            if self.list_stack:
                self.list_stack.pop()
            return
        if tag in ("b", "strong"):
            self.buf += "**"; return
        if tag in ("i", "em"):
            self.buf += "*"; return
        if tag in ("s", "strike", "del"):
            self.buf += "~~"; return
        if tag == "pre":
            self._emit(allow_blank=False); self.lines.append("```"); self._pre = max(0, self._pre - 1); return
        if tag == "code":
            self.buf += "`"; self._pre = max(0, self._pre - 1); return
        if tag == "a":
            href = self._a_href
            self._a_href = None
            if href:
                text = self.buf[self._a_start:]
                # Bare-URL or empty-text links: just show the URL.
                if not text.strip() or text.strip() == href:
                    self.buf = self.buf[: self._a_start] + href
                else:
                    self.buf = self.buf[: self._a_start] + f"[{text}]({href})"
            return

    def handle_data(self, data):
        if self._pre:
            self.buf += data
        else:
            # collapse internal whitespace/newlines within a block to single spaces
            self.buf += re.sub(r"[ \t\r\n]+", " ", data)

    # -- result ------------------------------------------------------------
    def markdown(self) -> str:
        self._emit(allow_blank=False)
        out: list[str] = []
        blank = False
        for ln in self.lines:
            if ln.strip() == "":
                if not blank and out:
                    out.append("")
                blank = True
            else:
                out.append(ln.rstrip())
                blank = False
        return "\n".join(out).strip()


def html_to_markdown(html: str) -> str:
    p = _Html2Md()
    p.feed(html)
    p.close()
    return p.markdown()


# --------------------------------------------------------------------------- #
# Export parsing
# --------------------------------------------------------------------------- #
_META_BLOCK = re.compile(r"^\s*<!--\s*workshop-apple-note\s*(.*?)-->", re.DOTALL)


def parse_source(text: str, fallback_stem: str, suffix: str) -> dict:
    """Return {id, title, folder, body_markdown} from one export file's text."""
    meta: dict[str, str] = {}
    m = _META_BLOCK.search(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        text = text[m.end():]

    if suffix in (".html", ".htm"):
        body = html_to_markdown(text)
    elif suffix == ".txt":
        body = text.strip()
    else:  # .md / .markdown - already markdown
        body = text.strip()

    title = (meta.get("title") or "").strip()
    if not title:
        title = _title_from_markdown(body) or fallback_stem
    return {
        "id": (meta.get("id") or "").strip(),
        "title": title,
        "folder": (meta.get("folder") or "").strip(),
        "created": (meta.get("created") or "").strip(),
        "modified": (meta.get("modified") or "").strip(),
        "body": body,
    }


def parse_apple_dt(s: str) -> float | None:
    """Apple export timestamps are naive local time; return an epoch or None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def build_frontmatter(created: str, modified: str) -> str:
    """YAML frontmatter carrying the Apple note's real dates, or '' if none."""
    fields = []
    if created:
        fields.append(f"created: {created}")
    if modified:
        fields.append(f"modified: {modified}")
    if not fields:
        return ""
    return "---\n" + "\n".join(fields) + "\n---\n\n"


def _title_from_markdown(body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^#{1,6}\s+", "", s)          # a leading heading
        s = re.sub(r"^[*_~`]+|[*_~`]+$", "", s).strip()  # strip wrapping emphasis
        return s[:120]
    return ""


def compose_note(title: str, body: str) -> str:
    """Ensure the note opens with exactly one `# Title`, not a duplicate of the
    Apple first line (which is usually the title again)."""
    lines = body.splitlines()
    # drop a leading line that just repeats the title (with/without heading/emphasis)
    while lines and lines[0].strip() == "":
        lines.pop(0)
    if lines:
        first = re.sub(r"^#{1,6}\s+", "", lines[0].strip())
        first = re.sub(r"^[*_~`]+|[*_~`]+$", "", first).strip()
        if first.lower() == title.strip().lower():
            lines.pop(0)
            while lines and lines[0].strip() == "":
                lines.pop(0)
    remainder = "\n".join(lines).strip()
    return f"# {title}\n\n{remainder}\n" if remainder else f"# {title}\n"


# --------------------------------------------------------------------------- #
# Vault-safe filesystem helpers (mirror notes_vault's containment rules)
# --------------------------------------------------------------------------- #
def sanitize_component(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if not name:
        name = "Untitled"
    return name[:120]


def atomic_write(abs_path: Path, content: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=abs_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, abs_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def collect_sources(export_dir: Path) -> list[Path]:
    exts = {".html", ".htm", ".md", ".markdown", ".txt"}
    files = [p for p in export_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts
             and not any(part.startswith(".") for part in p.relative_to(export_dir).parts)]
    # deterministic order so collision-suffixing (" 2") is reproducible across runs
    files.sort(key=lambda p: p.relative_to(export_dir).as_posix().lower())
    return files


def run_import(export_dir: Path, vault: Path, into: str, *, update: bool, force: bool, dry_run: bool) -> dict:
    manifest_path = vault / ".import" / "apple-notes-manifest.json"
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            manifest = {}
    by_id: dict = manifest.setdefault("by_id", {})

    used_paths: set[str] = set()
    # paths we already own (from prior runs) count as used so we don't re-collide
    for rec in by_id.values():
        used_paths.add(rec.get("path", ""))

    stats = {"created": 0, "updated": 0, "unchanged": 0, "changed_skipped": 0,
             "edited_skipped": 0, "attachments": 0, "errors": 0, "total": 0}
    plan: list[str] = []

    for src in collect_sources(export_dir):
        stats["total"] += 1
        try:
            raw = src.read_text("utf-8", errors="replace")
            note = parse_source(raw, src.stem, src.suffix.lower())
        except OSError as e:
            stats["errors"] += 1
            plan.append(f"ERROR read {src}: {e}")
            continue

        title = note["title"]
        content = build_frontmatter(note["created"], note["modified"]) + compose_note(title, note["body"])
        content_sha = sha(content)
        note_mtime = parse_apple_dt(note["modified"]) or parse_apple_dt(note["created"])
        if re.search(r"!\[|<img", note["body"], re.I):
            stats["attachments"] += 1

        # ---- resolve the target vault-relative path (stable per identity) ----
        note_id = note["id"]
        rec = by_id.get(note_id) if note_id else None
        if rec and rec.get("path"):
            rel = rec["path"]
        else:
            folder = sanitize_component(note["folder"]) if note["folder"] else ""
            base_parts = [p for p in [into.strip("/"), folder, sanitize_component(title)] if p]
            rel = "/".join(base_parts) + ".md"
            # de-collide against paths already claimed this run or in prior runs
            n = 2
            stem = rel[:-3]
            while rel in used_paths or (vault / rel).exists() and not _we_own(vault, rel, by_id):
                rel = f"{stem} {n}.md"
                n += 1
        used_paths.add(rel)
        abs_path = (vault / rel).resolve()
        if not abs_path.is_relative_to(vault.resolve()):
            stats["errors"] += 1
            plan.append(f"ERROR unsafe path for {src}")
            continue

        # ---- decide action ----
        exists = abs_path.is_file()
        existing = abs_path.read_text("utf-8", errors="replace") if exists else None
        identity = note_id or rel
        prior = by_id.get(identity) or (rec if note_id else None)

        if not exists:
            action = "create"
        elif existing == content:
            action = "unchanged"
        else:
            we_wrote_it = prior and prior.get("content_sha") == sha(existing)
            if force:
                action = "update"
            elif we_wrote_it and update:
                action = "update"          # our note, Apple source changed
            elif we_wrote_it:
                action = "changed_skipped"  # changed but no --update
            else:
                action = "edited_skipped"   # user edited in the vault (or foreign file)

        # ---- apply ----
        if action in ("create", "update"):
            if not dry_run:
                atomic_write(abs_path, content)
                if note_mtime is not None:
                    os.utime(abs_path, (note_mtime, note_mtime))
                by_id[identity] = {"path": rel, "content_sha": content_sha,
                                   "title": title, "source": src.name}
            stats["created" if action == "create" else "updated"] += 1
            plan.append(f"{action.upper():9} {rel}")
        else:
            stats[action] += 1
            if action != "unchanged":
                plan.append(f"{action.upper():9} {rel}  ({src.name})")

    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))

    stats["_plan"] = plan
    return stats


def _we_own(vault: Path, rel: str, by_id: dict) -> bool:
    """True if an existing vault file at rel was written by a prior import run."""
    for rec in by_id.values():
        if rec.get("path") == rel:
            try:
                return rec.get("content_sha") == sha((vault / rel).read_text("utf-8"))
            except OSError:
                return False
    return False


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def self_test() -> int:
    cases = [
        ("<div><b>Hello</b> world</div>", "**Hello** world"),
        ("<h1>Title</h1><div>line one</div><div>line two</div>", "# Title\nline one\nline two"),
        ("<ul><li>a</li><li>b</li></ul>", "- a\n- b"),
        ("<ol><li>one</li><li>two</li></ol>", "1. one\n2. two"),
        ('go to <a href="https://x.com">X</a> now', "go to [X](https://x.com) now"),
        ("<div>a</div><div><br></div><div>b</div>", "a\n\nb"),
        ("<i>em</i> and <s>gone</s>", "*em* and ~~gone~~"),
    ]
    ok = True
    for html, want in cases:
        got = html_to_markdown(html)
        flag = "ok " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"[{flag}] {html!r}\n       got:  {got!r}\n       want: {want!r}")

    # compose_note dedupes a repeated title line and guarantees one heading
    comp = compose_note("My Note", "# My Note\n\nbody here")
    assert comp == "# My Note\n\nbody here\n", repr(comp)
    comp2 = compose_note("My Note", "My Note\nbody")
    assert comp2 == "# My Note\n\nbody\n", repr(comp2)
    print("[ok ] compose_note dedupe")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Import Apple Notes export into the Workshop vault.")
    ap.add_argument("export_dir", nargs="?", help="folder holding the Apple Notes export")
    ap.add_argument("--vault", default=os.environ.get("WORKSHOP_NOTES_VAULT") or str(Path.home() / "notes"))
    ap.add_argument("--into", default="Apple Notes", help='vault subfolder to import under ("" for root)')
    ap.add_argument("--update", action="store_true", help="update notes whose Apple source changed")
    ap.add_argument("--force", action="store_true", help="overwrite even vault-side edits")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.export_dir:
        ap.error("export_dir is required (or pass --self-test)")

    export_dir = Path(args.export_dir).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    if not export_dir.is_dir():
        print(f"export dir not found: {export_dir}", file=sys.stderr)
        return 2
    vault.mkdir(parents=True, exist_ok=True)

    s = run_import(export_dir, vault, args.into, update=args.update, force=args.force, dry_run=args.dry_run)
    print("\n".join(s.pop("_plan", []))[:20000])
    print("-" * 56)
    print(f"vault:        {vault}  (under '{args.into or '/'}')")
    print(f"source files: {s['total']}")
    print(f"created:      {s['created']}")
    print(f"updated:      {s['updated']}")
    print(f"unchanged:    {s['unchanged']}")
    if s["changed_skipped"]:
        print(f"changed:      {s['changed_skipped']}  (Apple source changed; re-run with --update to apply)")
    if s["edited_skipped"]:
        print(f"vault-edited: {s['edited_skipped']}  (edited in the vault; left untouched, --force to overwrite)")
    if s["attachments"]:
        print(f"attachments:  {s['attachments']} note(s) referenced images/attachments (text imported; media not migrated)")
    if s["errors"]:
        print(f"errors:       {s['errors']}")
    if args.dry_run:
        print("(dry run - nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
