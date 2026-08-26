"""Files the operator attaches to a chat turn.

STORAGE IS CONTENT-ADDRESSED. The bytes live at
`.runtime/users/<owner>/files/blobs/<sha[:2]>/<sha>` and an append-only
`files/index.jsonl` maps an attachment id to that sha plus the name, kind, size,
scope and thread it arrived in. The same screenshot dropped into five chats is
stored once, and deleting one chat's history can never orphan a blob another
chat still points at. It also makes the second upload of a file free, which is
what actually happens when the operator re-drops the same screenshot.

The id is per-ATTACHMENT and the sha is per-CONTENT: two attachments of the same
bytes are two index rows and one blob, so a name given at upload stays attached
to that use of the file rather than being overwritten by the next one.

HOW THE MODEL SEES IT. `content_blocks` turns stored files into Anthropic
content blocks (`image` / `document` / `text`), which the CLI accepts on its
stream-json stdin. Verified against the CLI on 2026-08-11: a native image block
is replayed and read (a solid-orange PNG came back "Orange"), and a document
block's text is read without any tool call. That is the reason attachments are
NOT delivered as file paths for the agent to Read: an assistant chat's tool
surface is its declared MCP tools plus web plus the subagent tool (see
chat_context.effective_allowed_tools) and does not include Read, so a path would
work only in build chats. A content block works in every chat.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path

from . import attachment_convert, ownership

log = logging.getLogger(__name__)

# Matches the frontend's MAX_ATTACHMENT_BYTES. The client refuses first so the
# operator gets an instant answer; this is the one that actually holds.
MAX_BYTES = 20 * 1024 * 1024

# What a stored file is delivered as. Anything not in here was rejected at
# upload, so the store never holds bytes it cannot hand to a model.
KINDS = ("image", "pdf", "text")

# Media types we will emit in an image block. The CLI/API accepts these four;
# anything else that arrived as an image is delivered by its sniffed type below.
_IMAGE_MEDIA = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}

# How much of a text file to inline. A model reading a 4MB log in full is not
# reading it; it is filling its context with it. The block says it was cut.
MAX_TEXT_CHARS = 200_000


def _files_dir(owner_id: int | None = None) -> Path:
    d = ownership.user_runtime_dir(owner_id) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(owner_id: int | None = None) -> Path:
    return _files_dir(owner_id) / "index.jsonl"


def _blob_path(owner_id: int | None, sha: str) -> Path:
    return _files_dir(owner_id) / "blobs" / sha[:2] / sha


def safe_name(name: str) -> str:
    """A display name that cannot be a path. Attachments are addressed by sha on
    disk, so the name is only ever shown and sent to the model. Stripping the
    directory parts keeps a name like `../../etc/passwd` from being written
    anywhere or read back as one."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = base.replace("\x00", "")
    if not base or base in (".", ".."):
        base = "file"
    return base[:180]


def _sniff_image_media(data: bytes, name: str) -> str:
    """The media type to declare for an image block. Magic bytes first: a
    screenshot pasted from the clipboard has a real type and a useless name, and
    a name can lie about its contents."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _IMAGE_MEDIA.get(ext, "image/png")


# Mirrors TEXT_EXT in frontend/src/components/chat/attachments.ts. Both lists
# exist because both ends must refuse the same files: the client so the operator
# gets an instant answer, the server because the client is not the only caller.
_TEXT_EXT = {
    "txt", "text", "md", "markdown", "mdx", "rst", "adoc", "asciidoc", "org",
    "tex", "latex", "bib", "srt", "vtt", "log", "csv", "tsv",
    "json", "jsonl", "ndjson", "jsonc", "json5", "yaml", "yml", "toml",
    "ini", "cfg", "conf", "env", "properties",
    "ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts", "py", "rb", "go", "rs",
    "java", "kt", "kts", "swift", "vue", "svelte", "astro",
    "c", "h", "cc", "cpp", "hpp", "cs", "php", "lua", "pl", "r", "scala",
    "clj", "cljs", "cljc", "edn", "rkt", "scm", "lisp", "el",
    "ex", "exs", "erl", "dart", "zig", "nim", "hs", "elm", "jl", "groovy", "gradle",
    "fs", "fsx", "ml", "mli", "f90", "pas", "vb", "d", "sv", "vhd", "asm",
    "m", "mm", "prisma", "sol",
    "sh", "bash", "zsh", "fish", "ps1", "psm1", "psd1", "bat", "cmd", "tcl", "awk",
    "sql", "graphql", "gql", "proto",
    "tf", "tfvars", "hcl", "nix", "cmake",
    "html", "htm", "xhtml", "xml", "svg", "css", "scss", "sass", "less",
    "diff", "patch", "gitignore", "dockerfile", "makefile", "lock",
}
# Only the four formats the API accepts as a native image block. Not bmp/heic/
# tiff: those would need conversion (a new code path), so they are refused
# rather than delivered relabelled as png. Mirror of IMAGE_EXT on the client.
_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}


def kind_for(mime: str, name: str) -> str | None:
    """The kind we would store this as, or None if we would refuse it."""
    m = (mime or "").lower().split(";")[0].strip()
    if m.startswith("image/"):
        return "image"
    if m == "application/pdf":
        return "pdf"
    base = safe_name(name).lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else base
    if ext in _IMAGE_EXT:
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext in _TEXT_EXT:
        return "text"
    if m.startswith("text/") or m in ("application/json", "application/xml"):
        return "text"
    return None


def _write_blob(owner_id: int | None, data: bytes) -> str:
    """Content-address `data` and store it once. Returns the sha."""
    sha = hashlib.sha256(data).hexdigest()
    blob = _blob_path(owner_id, sha)
    if not blob.exists():
        blob.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never sees a half-written blob, and so a
        # crash mid-upload leaves a stray temp rather than a corrupt file that
        # hashes to a name claiming it is intact.
        tmp = blob.with_name(f".{sha}.{uuid.uuid4().hex}.part")
        tmp.write_bytes(data)
        os.replace(tmp, blob)
    return sha


def ingest(data: bytes, name: str, mime: str, *, scope: str = "",
           thread_id: str = "", owner_id: int | None = None) -> dict:
    """Store an uploaded file and return its index record.

    A native image/pdf/text stores as-is. A convertible binary (Office file,
    archive) is ALSO stored as-is, and a text rendering is derived and stored
    beside it so the model has something to read. Raises ValueError on a file we
    can neither read natively nor convert."""
    kind = kind_for(mime, name)
    if kind is not None:
        return save(data, name, kind, scope=scope, thread_id=thread_id,
                    owner_id=owner_id)
    key = attachment_convert.convertible(mime, name)
    if key is None:
        raise ValueError(f"{safe_name(name)} is not a type a chat can read")
    result = attachment_convert.convert(key, data, name)
    return save(data, name, "text", scope=scope, thread_id=thread_id,
                owner_id=owner_id, derived_text=result.text, converter=key,
                converter_version=result.version, note=result.note,
                media_type=attachment_convert.SOURCE_MEDIA.get(key))


def save(data: bytes, name: str, kind: str, *, scope: str = "",
         thread_id: str = "", owner_id: int | None = None,
         derived_text: str | None = None, converter: str | None = None,
         converter_version: int | None = None, note: str = "",
         media_type: str | None = None) -> dict:
    """Store `data` and return its index record. Raises ValueError on a file we
    would not be able to hand to a model.

    When `converter` is set the original bytes are a binary we cannot read, and
    `derived_text` (if any) is stored as a SECOND content-addressed blob whose
    sha is recorded as `derived_sha`. The model reads that derivation; the
    original blob is kept so the operator can still download the real file."""
    if kind not in KINDS:
        raise ValueError(f"unsupported attachment kind: {kind}")
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // (1024 * 1024)} MB")

    sha = _write_blob(owner_id, data)

    rec = {
        "id": uuid.uuid4().hex,
        "sha256": sha,
        "name": safe_name(name),
        "kind": kind,
        "size": len(data),
        "scope": scope or "",
        "thread_id": thread_id or "",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if kind == "image":
        rec["media_type"] = _sniff_image_media(data, rec["name"])
    elif kind == "pdf":
        rec["media_type"] = "application/pdf"
    if converter:
        rec["converter"] = converter
        if converter_version is not None:
            rec["converter_version"] = converter_version
        if media_type:
            rec["media_type"] = media_type
        if note:
            rec["note"] = note
        if derived_text:
            rec["derived_sha"] = _write_blob(owner_id, derived_text.encode("utf-8"))

    idx = _index_path(owner_id)
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def list_records(owner_id: int | None = None) -> list[dict]:
    """Every stored attachment, newest last. The index is append-only, so a
    later row for the same id (there is no writer that makes one today) would
    win; reading it as a dict keyed by id keeps that true if one ever appears."""
    idx = _index_path(owner_id)
    if not idx.exists():
        return []
    out: dict[str, dict] = {}
    for line in idx.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out[rec["id"]] = rec
    return list(out.values())


def get(att_id: str, owner_id: int | None = None) -> dict | None:
    for rec in reversed(list_records(owner_id)):
        if rec.get("id") == att_id:
            return rec
    return None


def delete(att_id: str, owner_id: int | None = None) -> bool:
    """Forget one attachment. False if it was not there.

    The BLOB only goes when no other row still points at that sha. Content
    addressing means one file's bytes can back several attachments (the same
    screenshot dropped into three chats), so deleting the bytes with the row
    would blank an image in a chat the operator never touched.

    The index is append-only in normal use, so removal rewrites it whole. That
    is fine at this size and it is atomic (write-then-rename), which matters
    more: a half-written index is every attachment lost, not one."""
    rows = list_records(owner_id)
    keep = [r for r in rows if r.get("id") != att_id]
    if len(keep) == len(rows):
        return False
    gone = next(r for r in rows if r.get("id") == att_id)

    idx = _index_path(owner_id)
    tmp = idx.with_name(f".index.{uuid.uuid4().hex}.part")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in keep), encoding="utf-8")
    os.replace(tmp, idx)

    # A converted file has two blobs (original bytes + derived text); either may
    # be shared with another row, so each is GC'd independently.
    referenced = {s for r in keep for s in (r.get("sha256"), r.get("derived_sha")) if s}
    for sha in {gone.get("sha256"), gone.get("derived_sha")}:
        if sha and sha not in referenced:
            try:
                _blob_path(owner_id, sha).unlink(missing_ok=True)
            except OSError as e:
                log.warning("attachment %s: blob %s not removed: %s", att_id, sha, e)
    return True


def blob_for(rec: dict, owner_id: int | None = None) -> Path | None:
    p = _blob_path(owner_id, rec.get("sha256", ""))
    return p if p.exists() else None


def derived_path(rec: dict, owner_id: int | None = None) -> Path | None:
    """The stored text rendering of a converted file, if there is one."""
    sha = (rec or {}).get("derived_sha")
    if not sha:
        return None
    p = _blob_path(owner_id, sha)
    return p if p.exists() else None


# --- Thumbnails -------------------------------------------------------------
#
# A raster preview of a file, generated LAZILY on first request and cached
# content-addressed beside the blob (thumb/<sha>/<w>x<h>.webp). A thumb is a
# pure function of (source bytes, box), so the key needs nothing else and a
# regenerate is free. Only `image` and `pdf` produce one; a text/converted file
# has no meaningful raster, so the ref card shows an excerpt instead. Any
# failure returns None and the caller falls back to the kind icon: a thumbnail
# is a nicety, never load-bearing.

# Below this the box is pointless and above it we are rendering a poster, not a
# thumbnail. Clamped, not trusted from the query string.
_THUMB_MIN = 48
_THUMB_MAX = 512
# A pdf page one render past this many seconds is not worth the wait for a
# preview; fall back to the icon.
_PDF_RENDER_TIMEOUT = 8


def _thumb_path(owner_id: int | None, sha: str, w: int, h: int) -> Path:
    return _files_dir(owner_id) / "thumb" / sha[:2] / sha / f"{w}x{h}.webp"


def _clamp_box(w: int, h: int) -> tuple[int, int]:
    def c(v: int) -> int:
        return max(_THUMB_MIN, min(_THUMB_MAX, int(v)))
    return c(w), c(h)


def _render_pdf_page1(src: Path, box_px: int) -> "object | None":
    """Page one of a pdf as a PIL image, via poppler's pdftoppm (already on the
    box, no Python dep). None if poppler is absent or the render fails."""
    import shutil
    import subprocess
    import tempfile
    from PIL import Image

    exe = shutil.which("pdftoppm")
    if exe is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "page"
        try:
            subprocess.run(
                [exe, "-png", "-f", "1", "-l", "1", "-scale-to", str(box_px),
                 str(src), str(out)],
                check=True, timeout=_PDF_RENDER_TIMEOUT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        pngs = sorted(Path(td).glob("page*.png"))
        if not pngs:
            return None
        img = Image.open(pngs[0])
        img.load()
        return img


def thumbnail(att_id: str, w: int = 320, h: int = 320,
              owner_id: int | None = None) -> Path | None:
    """Path to a cached thumbnail of the attachment fitted inside (w, h), or None
    when the file has no raster preview (text/converted) or rendering failed.
    Generates on a cache miss, serves on a hit."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    rec = get(att_id, owner_id)
    if rec is None:
        return None
    kind = rec.get("kind") or ""
    if kind not in ("image", "pdf"):
        return None
    sha = rec.get("sha256") or ""
    src = _blob_path(owner_id, sha)
    if not sha or not src.exists():
        return None

    w, h = _clamp_box(w, h)
    dest = _thumb_path(owner_id, sha, w, h)
    if dest.exists():
        return dest

    try:
        if kind == "image":
            img = Image.open(src)
            img.load()
        else:  # pdf
            img = _render_pdf_page1(src, max(w, h) * 2)
            if img is None:
                return None
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.thumbnail((w, h))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{uuid.uuid4().hex}.part")
        img.save(tmp, "WEBP", quality=82, method=4)
        os.replace(tmp, dest)
        return dest
    except (UnidentifiedImageError, OSError, ValueError) as e:
        log.debug("thumbnail failed for %s: %s", att_id, e)
        return None


def _text_block(rec: dict, data: bytes) -> dict:
    """A text file, inlined and labelled. Fenced with its name so the model can
    tell one attached file from the next and from the operator's own message."""
    text = data.decode("utf-8", errors="replace")
    truncated = ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = f"\n... (truncated at {MAX_TEXT_CHARS} characters)"
    return {
        "type": "text",
        "text": f"Attached file: {rec['name']}\n```\n{text}{truncated}\n```",
    }


def _converted_block(rec: dict, owner_id: int | None = None) -> dict:
    """The text block for a converted binary. Reads the derived rendering, or, if
    conversion produced none (corrupt/encrypted file), says so plainly so the
    model does not answer about content it never received."""
    label = attachment_convert.SOURCE_LABEL.get(rec.get("converter", ""), "file")
    dpath = derived_path(rec, owner_id)
    if dpath is None:
        note = rec.get("note") or f"could not be read as {label}"
        return {"type": "text",
                "text": f"Attached {label} {rec['name']}: {note}. The original is stored."}
    text = dpath.read_text(encoding="utf-8", errors="replace")
    truncated = ""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = f"\n... (truncated at {MAX_TEXT_CHARS} characters)"
    return {
        "type": "text",
        "text": (f"Attached {label} {rec['name']}, converted to text "
                 f"(formatting may be lost):\n```\n{text}{truncated}\n```"),
    }


def content_blocks(refs: list[dict], owner_id: int | None = None) -> list[dict]:
    """Anthropic content blocks for the attachments a turn carries.

    A ref that no longer resolves is skipped rather than raising: a turn whose
    attachment went missing should still be sent, minus the file. It comes back
    as a text block naming what was lost, so the model is not answering about an
    image it was silently never given.
    """
    blocks: list[dict] = []
    for ref in refs or []:
        att_id = str((ref or {}).get("id") or "")
        if not att_id:
            continue
        rec = get(att_id, owner_id)
        path = blob_for(rec, owner_id) if rec else None
        if not rec or not path:
            name = (ref or {}).get("name") or att_id
            blocks.append({"type": "text",
                           "text": f"(Attachment {name} is no longer available.)"})
            continue

        # A converted file: the model reads the derived text, never the original
        # binary bytes. The block names the source so the model knows it is
        # reading a rendering of a Word/Excel/archive, not a plain text file.
        if rec.get("converter"):
            blocks.append(_converted_block(rec, owner_id))
            continue

        try:
            data = path.read_bytes()
        except OSError as e:
            log.warning("attachment %s unreadable: %s", att_id, e)
            blocks.append({"type": "text",
                           "text": f"(Attachment {rec['name']} could not be read.)"})
            continue

        kind = rec.get("kind")
        if kind == "text":
            blocks.append(_text_block(rec, data))
        elif kind in ("image", "pdf"):
            blocks.append({
                "type": "image" if kind == "image" else "document",
                "source": {
                    "type": "base64",
                    "media_type": rec.get("media_type")
                    or ("image/png" if kind == "image" else "application/pdf"),
                    "data": base64.b64encode(data).decode("ascii"),
                },
            })
    return blocks


def describe(refs: list[dict]) -> str:
    """One line naming the attached files, appended to the operator's text.

    The blocks alone carry the content, but an image block has no filename, so
    without this the model cannot say WHICH file the operator means when there
    are three. Empty when there are none, so an ordinary turn is untouched."""
    names = [str((r or {}).get("name") or "").strip() for r in refs or []]
    names = [n for n in names if n]
    if not names:
        return ""
    return "Attached: " + ", ".join(names)
