"""Converting an uploaded file Claude cannot read into one it can.

Claude reads three things natively: images (png/jpeg/gif/webp), PDFs, and plain
text. A docx, an xlsx, a zip are binary: handed over as bytes they are noise. So
on upload we KEEP the original blob and, for a convertible type, DERIVE a
text/markdown rendering that is stored beside it (chat_attachments.save). The
model reads the derivation; the operator keeps the original.

Every converter is best-effort and NEVER raises: a corrupt docx or an encrypted
zip returns a `ConvertResult` whose `text` is None and whose `note` says why, so
the file still stores (original preserved) and the model is told plainly that it
could not be read rather than being handed a broken block. The `version` on each
result lets a future converter improvement re-derive on a cache miss.

ARCHIVES ARE THE ONLY REAL DANGER HERE and are handled defensively: never
`extractall`, never write to disk, hard caps on entry count and total
uncompressed bytes, per-entry compression-ratio guard against zip bombs, and
outright refusal of path-traversal names, symlinks and hardlinks. An archive is
presented to the model as a manifest of its paths plus the inlined text of its
text-like entries; binary entries are listed, not inlined. Nested archives are
listed, not recursed into.
"""

from __future__ import annotations

import gzip
import io
import logging
import tarfile
import zipfile
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# The derived rendering is stored in full up to this bound; the send path
# (chat_attachments.content_blocks) truncates again to its own MAX_TEXT_CHARS.
# Two limits because the viewer shows the stored text while the model gets the
# shorter send-time slice.
MAX_DERIVED_CHARS = 400_000

# Archive safety. Real zip bombs run millions:1; 42KB expands to 4.5GB. We never
# trust a header's declared size, we sum as we read and stop at the budget.
MAX_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # 200 MB across all entries
MAX_ENTRY_INLINE_BYTES = 512 * 1024         # inline at most 512 KB of one entry
BOMB_RATIO = 1000                           # skip inlining an entry over this ratio

# xlsx/pptx bounds so one giant sheet cannot blow the derivation.
MAX_SHEET_ROWS = 5000
MAX_SHEET_COLS = 100

# Extensions we will inline as text when found inside an archive. A deliberate
# allowlist: a binary entry is listed in the manifest, not decoded as garbage.
_ARCHIVE_TEXT_EXT = {
    "txt", "text", "md", "markdown", "mdx", "rst", "adoc", "org", "tex", "log",
    "csv", "tsv", "json", "jsonl", "ndjson", "yaml", "yml", "toml", "ini", "cfg",
    "conf", "env", "properties", "ts", "tsx", "js", "jsx", "mjs", "cjs", "py",
    "rb", "go", "rs", "java", "kt", "swift", "c", "h", "cc", "cpp", "hpp", "cs",
    "php", "lua", "pl", "r", "scala", "clj", "ex", "exs", "erl", "dart", "sh",
    "bash", "zsh", "sql", "graphql", "proto", "tf", "hcl", "nix", "html", "htm",
    "xml", "svg", "css", "scss", "less", "diff", "patch", "gitignore",
    "dockerfile", "makefile", "vue", "svelte", "astro",
}

# ext -> converter key. The key is what gets recorded on the attachment and what
# routes convert() below. Archives share the two archive converters.
_CONVERT_EXT = {
    "docx": "docx",
    "xlsx": "xlsx", "xlsm": "xlsx",
    "pptx": "pptx",
    "zip": "zip",
    "tar": "tar", "tgz": "tar", "gz": "tar",
}

# A few MIME types worth trusting when the name has no useful extension.
_CONVERT_MIME = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/zip": "zip",
    "application/x-tar": "tar",
    "application/gzip": "tar",
}

# A human phrase per source type, used in the block header the model sees and in
# the viewer so nothing claims a fidelity it does not have.
SOURCE_LABEL = {
    "docx": "Word document",
    "xlsx": "Excel spreadsheet",
    "pptx": "PowerPoint presentation",
    "zip": "zip archive",
    "tar": "tar archive",
}

# The original file's real media type, for downloading the preserved bytes.
SOURCE_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "tar": "application/x-tar",
}

# Bumped when a converter's output changes enough to want re-derivation.
CONVERTER_VERSION = {"docx": 1, "xlsx": 1, "pptx": 1, "zip": 1, "tar": 1}


@dataclass
class ConvertResult:
    converter: str
    version: int
    text: str | None = None
    lossy: bool = True
    note: str = ""
    warnings: list[str] = field(default_factory=list)


def _ext(name: str) -> str:
    base = (name or "").rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else base


def convertible(mime: str, name: str) -> str | None:
    """The converter key for this upload, or None if it is not a type we convert.

    Called only AFTER chat_attachments.kind_for has declined: a file that is
    already a native image/pdf/text never reaches here."""
    ext = _ext(name)
    if ext in _CONVERT_EXT:
        return _CONVERT_EXT[ext]
    m = (mime or "").lower().split(";")[0].strip()
    return _CONVERT_MIME.get(m)


def convert(key: str, data: bytes, name: str) -> ConvertResult:
    """Derive a text rendering for a convertible upload. Never raises."""
    version = CONVERTER_VERSION.get(key, 1)
    try:
        if key == "docx":
            return _convert_docx(data, version)
        if key == "xlsx":
            return _convert_xlsx(data, version)
        if key == "pptx":
            return _convert_pptx(data, version)
        if key == "zip":
            return _convert_zip(data, name, version)
        if key == "tar":
            return _convert_tar(data, name, version)
    except Exception as e:  # a converter must never take the upload down with it
        log.warning("attachment convert %s failed: %s", key, e)
        return ConvertResult(converter=key, version=version, text=None,
                             note=f"could not be read as {SOURCE_LABEL.get(key, key)}")
    return ConvertResult(converter=key, version=version, text=None,
                         note="unsupported")


def _cap(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_DERIVED_CHARS:
        return text, False
    return text[:MAX_DERIVED_CHARS], True


# ----------------------------------------------------------------- office files

def _convert_docx(data: bytes, version: int) -> ConvertResult:
    import mammoth
    res = mammoth.convert_to_markdown(io.BytesIO(data))
    text = (res.value or "").strip()
    if not text:
        return ConvertResult(converter="docx", version=version, text=None,
                             note="the document had no readable text")
    text, cut = _cap(text)
    if cut:
        text += "\n\n... (truncated)"
    return ConvertResult(converter="docx", version=version, text=text, lossy=True)


def _convert_xlsx(data: bytes, version: int) -> ConvertResult:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    warnings: list[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"## Sheet: {ws.title}")
            rows_out: list[str] = []
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= MAX_SHEET_ROWS:
                    warnings.append(f"sheet {ws.title!r} truncated at {MAX_SHEET_ROWS} rows")
                    break
                cells = row[:MAX_SHEET_COLS]
                rows_out.append(",".join(_csv_cell(c) for c in cells))
            parts.append("\n".join(rows_out) if rows_out else "(empty)")
            parts.append("")
    finally:
        wb.close()
    text = "\n".join(parts).strip()
    if not text:
        return ConvertResult(converter="xlsx", version=version, text=None,
                             note="the spreadsheet was empty")
    text, cut = _cap(text)
    if cut:
        text += "\n\n... (truncated)"
    return ConvertResult(converter="xlsx", version=version, text=text, lossy=True,
                         warnings=warnings)


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    s = str(value)
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _convert_pptx(data: bytes, version: int) -> ConvertResult:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        parts.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = "\n".join(p.text for p in shape.text_frame.paragraphs).strip()
                if txt:
                    parts.append(txt)
            if shape.has_table:
                for row in shape.table.rows:
                    parts.append(" | ".join(cell.text for cell in row.cells))
        parts.append("")
    text = "\n".join(parts).strip()
    if not text:
        return ConvertResult(converter="pptx", version=version, text=None,
                             note="the presentation had no readable text")
    text, cut = _cap(text)
    if cut:
        text += "\n\n... (truncated)"
    return ConvertResult(converter="pptx", version=version, text=text, lossy=True)


# --------------------------------------------------------------------- archives

def _human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n // 1024} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _unsafe_path(name: str) -> bool:
    """A member path that would escape the archive root if extracted. We never
    extract to disk, but a traversal name is a red flag and its bytes are not
    worth reading."""
    n = name.replace("\\", "/")
    if n.startswith("/"):
        return True
    return any(part == ".." for part in n.split("/"))


def _archive_body(manifest: list[str], inlined: list[str], warnings: list[str],
                  header: str) -> str:
    parts = [header, "", "Manifest:"]
    parts.extend(manifest or ["(no files)"])
    if inlined:
        parts.append("")
        parts.extend(inlined)
    if warnings:
        parts.append("")
        parts.append("Notes: " + "; ".join(warnings))
    return "\n".join(parts)


def _convert_zip(data: bytes, name: str, version: int) -> ConvertResult:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ConvertResult(converter="zip", version=version, text=None,
                             note="not a valid zip, stored as-is")
    manifest: list[str] = []
    inlined: list[str] = []
    warnings: list[str] = []
    total = 0
    count = 0
    text_budget = MAX_DERIVED_CHARS
    try:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if any(getattr(i, "flag_bits", 0) & 0x1 for i in infos):
            warnings.append("some entries are encrypted and were not read")
        for info in infos:
            if count >= MAX_ENTRIES:
                warnings.append(f"more than {MAX_ENTRIES} entries, list truncated")
                break
            count += 1
            iname = info.filename
            if _unsafe_path(iname):
                warnings.append(f"skipped unsafe path {iname!r}")
                continue
            manifest.append(f"- {iname} ({_human(info.file_size)})")
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                warnings.append("total size exceeded the limit, remaining entries not inlined")
                break
            if info.flag_bits & 0x1:
                continue
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > BOMB_RATIO:
                warnings.append(f"skipped {iname!r} (compression ratio {int(ratio)}:1)")
                continue
            ext = _ext(iname)
            if ext not in _ARCHIVE_TEXT_EXT or text_budget <= 0:
                continue
            take = min(MAX_ENTRY_INLINE_BYTES, text_budget)
            try:
                with zf.open(info) as fh:
                    raw = fh.read(take + 1)
            except Exception:
                warnings.append(f"could not read {iname!r}")
                continue
            chunk = raw[:take].decode("utf-8", errors="replace")
            if len(raw) > take:
                chunk += "\n... (truncated)"
            text_budget -= len(chunk)
            inlined.append(f"--- {iname} ---\n{chunk}")
    finally:
        zf.close()
    header = (f"Archive: {name} ({count} file{'s' if count != 1 else ''}, "
              f"{_human(total)} uncompressed)")
    text = _archive_body(manifest, inlined, warnings, header)
    return ConvertResult(converter="zip", version=version, text=text, lossy=True,
                         warnings=warnings)


def _convert_tar(data: bytes, name: str, version: int) -> ConvertResult:
    # tarfile.open sniffs gzip/bzip2/xz, so .tar, .tar.gz and .tgz all land here.
    # A bare (non-tar) .gz is a single compressed file and is handled below.
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.ReadError:
        if _ext(name) == "gz":
            return _convert_gz(data, name, version)
        return ConvertResult(converter="tar", version=version, text=None,
                             note="not a valid tar, stored as-is")
    manifest: list[str] = []
    inlined: list[str] = []
    warnings: list[str] = []
    total = 0
    count = 0
    text_budget = MAX_DERIVED_CHARS
    try:
        for member in tf:
            if member.issym() or member.islnk():
                warnings.append(f"skipped link {member.name!r}")
                continue
            if not member.isfile():
                continue
            if count >= MAX_ENTRIES:
                warnings.append(f"more than {MAX_ENTRIES} entries, list truncated")
                break
            count += 1
            if _unsafe_path(member.name):
                warnings.append(f"skipped unsafe path {member.name!r}")
                continue
            manifest.append(f"- {member.name} ({_human(member.size)})")
            total += member.size
            if total > MAX_TOTAL_UNCOMPRESSED:
                warnings.append("total size exceeded the limit, remaining entries not inlined")
                break
            ext = _ext(member.name)
            if ext not in _ARCHIVE_TEXT_EXT or text_budget <= 0:
                continue
            take = min(MAX_ENTRY_INLINE_BYTES, text_budget)
            try:
                fh = tf.extractfile(member)
                raw = fh.read(take + 1) if fh else b""
            except Exception:
                warnings.append(f"could not read {member.name!r}")
                continue
            chunk = raw[:take].decode("utf-8", errors="replace")
            if len(raw) > take:
                chunk += "\n... (truncated)"
            text_budget -= len(chunk)
            inlined.append(f"--- {member.name} ---\n{chunk}")
    finally:
        tf.close()
    header = (f"Archive: {name} ({count} file{'s' if count != 1 else ''}, "
              f"{_human(total)} uncompressed)")
    text = _archive_body(manifest, inlined, warnings, header)
    return ConvertResult(converter="tar", version=version, text=text, lossy=True,
                         warnings=warnings)


def _convert_gz(data: bytes, name: str, version: int) -> ConvertResult:
    """A single gzip-compressed file (not a tarball). Decompress with a bounded
    read and inline it if it decodes as text."""
    inner = name[:-3] if name.lower().endswith(".gz") else name
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            raw = gz.read(MAX_ENTRY_INLINE_BYTES + 1)
    except Exception:
        return ConvertResult(converter="tar", version=version, text=None,
                             note="not a valid gzip, stored as-is")
    chunk = raw[:MAX_ENTRY_INLINE_BYTES].decode("utf-8", errors="replace")
    if len(raw) > MAX_ENTRY_INLINE_BYTES:
        chunk += "\n... (truncated)"
    text = f"Gzip-compressed file: {inner}\n\n--- {inner} ---\n{chunk}"
    return ConvertResult(converter="tar", version=version, text=text, lossy=True)
