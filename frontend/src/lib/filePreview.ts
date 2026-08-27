// Which files the browser can show us without any help, and where to point it.
//
// The rule this sits under is file-viewer-and-editor-2026-08-22.md section 4:
// "a file gets a rendered view when its rendered form differs from its source."
// A PNG's source is a wall of bytes nobody can read, so its rendered form is
// the only form there is, and until this existed the viewer said "this file is
// binary" and stopped. Markdown is the other case and already has its two forms.
//
// DELIBERATELY SHORT. Each entry is a type the browser renders natively from a
// plain `<img>` or `<iframe>`, so nothing here ships a decoder. A CSV table, a
// notebook, an office document and a media player are all real wants and none
// of them is this: they need a parser or a player, which is a different piece
// of work with a different cost.

export type FilePreviewKind = "image" | "pdf" | "none";

/** Raster and vector images every current browser draws in an `<img>`. Formats
 *  that need a decode nobody has agreed on (HEIC, TIFF) are not here on
 *  purpose: they would draw a broken-image icon and call it a preview. */
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);

function extensionOf(path: string): string {
  const base = path.split("/").filter(Boolean).pop() ?? "";
  const dot = base.lastIndexOf(".");
  // A dotfile's only dot is at index 0, and that is a name, not an extension.
  return dot <= 0 ? "" : base.slice(dot + 1).toLowerCase();
}

/** How this path should be shown, from its extension alone. `none` means there
 *  is nothing better than the text the file API already returned, which for a
 *  binary is the "can't show this" line the viewer has always drawn. */
export function previewKindFor(path: string): FilePreviewKind {
  const ext = extensionOf(path);
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  return "none";
}

/** The bytes of one workspace file, with its media type (GET /api/files/raw).
 *  `/api/files/content` is the JSON door and reports a binary file's content as
 *  absent; this is the same file through the door an `<img>` needs. */
export function rawFileUrl(workspace: string, path: string): string {
  return `/api/files/raw?workspace=${encodeURIComponent(workspace)}&path=${encodeURIComponent(path)}`;
}
