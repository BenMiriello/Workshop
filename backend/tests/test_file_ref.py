"""Layer 2 of managed-file-references-and-path-scoping-2026-08-18.md: the `file`
ref kind. A file is addressed by a managed ref (workshop:file/<attachment-id>)
resolving to an in-app href + preview descriptor, never a raw path.

Attachment storage is redirected to tmp_path so this never writes into a real
owner's runtime dir (the tests-must-not-share-owner-storage rule)."""
from __future__ import annotations

import pytest

from services import refs, chat_attachments


def test_file_ref_roundtrip():
    s = refs.file_ref("abc123def456")
    assert s == "workshop:file/abc123def456"
    r = refs.parse_ref(s)
    assert r.kind == "file" and r.key == "abc123def456"
    assert refs.serialize_ref(r) == s


def test_file_ref_escaping_key_is_refused():
    # A key with a separator is a path trying to escape the store.
    with pytest.raises(ValueError):
        refs.parse_ref("workshop:file/a/b")
    assert refs.resolve_ref("workshop:file/../etc/passwd") is None


def test_resolve_unknown_file_is_none():
    assert refs.resolve_ref("workshop:file/deadbeefdeadbeefdeadbeefdeadbeef") is None


def test_resolve_live_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments.ownership, "user_runtime_dir",
                        lambda owner_id=None: tmp_path)
    rec = chat_attachments.save(b"hello world", "notes.txt", "text")
    att_id = rec["id"]

    out = refs.resolve_ref(refs.file_ref(att_id))
    assert out is not None
    assert out["kind"] == "file"
    assert out["title"] == "notes.txt"
    assert out["href"] == f"/api/attachments/{att_id}"          # in-app, not a path
    assert out["status"] == "current"
    assert out["preview"]["icon"] == "text"
    assert out["preview"]["size_bytes"] == len(b"hello world")
    assert out["preview"]["thumb_url"] is None                  # text has no thumbnail

    # A deleted attachment stops resolving, rather than rendering a dead chip.
    chat_attachments.delete(att_id)
    assert refs.resolve_ref(refs.file_ref(att_id)) is None


def _real_png(w: int, h: int) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 120, 90)).save(buf, "PNG")
    return buf.getvalue()


def test_thumbnail_generates_and_caches_for_image(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments.ownership, "user_runtime_dir",
                        lambda owner_id=None: tmp_path)
    rec = chat_attachments.save(_real_png(900, 600), "wide.png", "image")
    p = chat_attachments.thumbnail(rec["id"], 320, 320)
    assert p is not None and p.exists()
    from PIL import Image
    tw, th = Image.open(p).size
    assert tw <= 320 and th <= 320 and (tw == 320 or th == 320)  # fit the box
    # A second call is a cache HIT: same path, no regeneration.
    assert chat_attachments.thumbnail(rec["id"], 320, 320) == p


def test_thumbnail_is_none_for_text(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments.ownership, "user_runtime_dir",
                        lambda owner_id=None: tmp_path)
    rec = chat_attachments.save(b"plain text file", "notes.txt", "text")
    assert chat_attachments.thumbnail(rec["id"]) is None


def test_thumbnail_box_is_clamped(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments.ownership, "user_runtime_dir",
                        lambda owner_id=None: tmp_path)
    rec = chat_attachments.save(_real_png(400, 400), "sq.png", "image")
    # An absurd box is clamped to the max, not honoured.
    p = chat_attachments.thumbnail(rec["id"], 9000, 9000)
    from PIL import Image
    assert max(Image.open(p).size) <= 512


def test_resolve_image_attachment_points_at_thumb_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_attachments.ownership, "user_runtime_dir",
                        lambda owner_id=None: tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    rec = chat_attachments.save(png, "shot.png", "image")
    out = refs.resolve_ref(refs.file_ref(rec["id"]))
    assert out is not None
    assert out["preview"]["icon"] == "image"
    # An image now gets a lazily-rendered, cached thumbnail from /thumb, not the
    # full-bytes href, so a small preview box does not download the whole image.
    assert out["preview"]["thumb_url"] == f"/api/attachments/{rec['id']}/thumb"
    assert out["preview"]["mime"] == "image/png"
