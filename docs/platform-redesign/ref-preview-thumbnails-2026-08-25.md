# Ref preview: thumbnails and excerpts (plan)

Status: PROPOSED, not built. Operator asked to see the plan before the work.
Supersedes nothing. Related: `managed-file-references-and-path-scoping-2026-08-18.md`
(Layer 3, which parked pdf/text rendering), `memory-and-documentation-model-2026-08-14.md` sec 10.

## What exists today

The ref preview card (`Ref.tsx`, `services/refs.py`) shows a kind icon, the
title (now the link), a meta line and copy rows. The `preview` descriptor
carries `mime`, `size_bytes`, `icon`, `thumb_url`. Only ONE case ever sets
`thumb_url`: a `file` ref whose kind is `image`, where the thumbnail is just the
full image bytes served at its own `/api/attachments/<id>` href. Everything else
(pdf, text, office, archive, and every ENTITY kind: note, doc, ticket, chat,
run, project, agent, app) has `thumb_url: null` and falls back to the icon.

So there is no thumbnail pipeline. This is the plan for one.

## The distinction that shapes it

A ref target is either a FILE (opaque bytes) or an ENTITY (a record with a
body). Their meaningful previews are different things:

- A file's preview is a raster THUMBNAIL: what does page one look like.
- An entity's preview is a text EXCERPT: the first lines of the note or doc, the
  ticket's objective, the chat's opening message. Rendering a markdown note to
  an image would be pointless. The words are the preview.

Both feed the same card, which already stacks (icon+title row, optional media,
meta, actions) after the 2026-08-25 redesign. We add two optional fields to the
descriptor: `excerpt` (short text) and a real `thumb_url` for files.

## Entity excerpts (cheap, no dependencies)

The resolver already opens each entity to confirm it still resolves, so the
excerpt is nearly free at that point.

- note / doc: first ~200 chars of the body, markdown lightly stripped.
- ticket: the Objective line.
- chat: the opening user message.
- run: the last status line.

Render as two or three muted, clamped lines under the title when there is no
`thumb_url`. This alone covers the refs the operator actually makes most (notes,
docs, tickets), and needs no new dependency.

## File thumbnails (raster)

Generate LAZILY on first request, never at upload (upload should not block on
work for a file nobody previews). Content-address the result and cache it beside
the blob: key `thumb/<sha>/<w>x<h>.<ext>` under the owner's files dir, generate
on a miss, serve on a hit. Thumbs are derived, disposable, rebuildable.

New endpoint: `GET /api/attachments/<id>/thumb?w=&h=`. The file resolver points
`thumb_url` at it instead of the full-bytes href, so a small box gets small
bytes.

By kind:

- image: Pillow downscale to fit the box (~320px longest edge), strip EXIF,
  emit WebP/PNG. Replaces "ship the full image into a small box". Pillow is
  already in the venv.
- pdf: `pdftoppm -png -f 1 -l 1 -scale-to <px>` (poppler, already on PATH) to
  render page one, then Pillow downscale. Zero new Python deps.
- office (docx/xlsx/pptx): these already store a derived TEXT rendering at
  ingest (`attachment_convert`). v1 shows that as an excerpt (the text path
  above), no new dep. Higher fidelity (LibreOffice `--headless` to pdf, then the
  pdf path) is a follow-up, gated on adding that binary.
- archive / unknown binary: no raster, keep the kind icon as today.

Safety and budget: skip the raster and fall back to the icon when the source is
over a size or page-count cap; bound render wall-clock; one thumbnail box to
start (the card's display size). A failed render falls back to the icon, never
an error.

## Frontend changes

Small, because the redesign already made room.

- `lib/refs.ts`: add `excerpt?: string | null` to `RefPreview`.
- `Ref.tsx`: render `excerpt` (muted, clamped ~3 lines) when there is no
  `thumb_url`; the thumb path already renders.
- `styles.css`: one `.ws-ref-card__excerpt` rule (muted, line-clamp).

## Where the work lands

- `backend/services/refs.py`: `excerpt` on the entity resolvers; file
  `thumb_url` -> the thumb endpoint.
- `backend/services/chat_attachments.py`: `thumbnail(att_id, box)` — lazy
  generate + cache; the Pillow / pdftoppm logic.
- `backend/routes/chat.py`: `GET /api/attachments/<id>/thumb`.
- frontend: the three small edits above.
- deps: NONE for excerpts, images, or pdfs. Office fidelity is an optional later
  step behind a LibreOffice binary.

## Suggested phasing (each shippable alone)

1. Entity excerpts. No deps, covers the common refs.
2. Image downscale thumbnails (Pillow).
3. PDF page-one thumbnails (pdftoppm).
4. Optional: office via LibreOffice -> pdf.

## Open question for the operator

Phase 1 (excerpts) and phases 2-3 (image + pdf thumbnails) are independent. Do
you want all three in the first pass, or excerpts first (they cover notes, docs
and tickets, which is most of what you link) and rasters as a follow-up?
