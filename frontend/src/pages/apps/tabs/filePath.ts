// Pure rules behind the Files tab: which folders have to be open for a path to
// be visible in the tree, which icon a file gets, and what the viewer is
// allowed to do with one file's read response. Extracted so all of it survives
// without a DOM harness (frontend/CLAUDE.md: "the target of a test is always
// the pure rule behind a behaviour").
import type { FileTreeIconHint, FileTreeNode } from "@workshop/ui";

export type Crumb = { label: string; path: string };

/** Every directory on the way to a relative path ("" = the workspace root).
 *  Each crumb's `path` is also the tree node's id for that directory, since a
 *  filesystem adapter keys nodes by path. The root itself is not a crumb: its
 *  label is the workspace's own name, which the caller already has and this
 *  module does not. */
export function breadcrumbsFor(path: string): Crumb[] {
  const parts = path.split("/").filter(Boolean);
  const out: Crumb[] = [];
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    out.push({ label: part, path: acc });
  }
  return out;
}

/** The directory one level up from `path`. "" at the root, and "" stays "":
 *  there is nowhere above the workspace's own file root to go. */
export function parentPath(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

/** The file's own name: the last segment of a path, and the whole path when
 *  there is only one segment. The document header draws this in mono beside the
 *  directory that holds it (`parentPath`), which is the mockup's split of a
 *  path into a --faint prefix and a --fg name. */
export function fileNameOf(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

/** The branch ids the tree has to have open for `path` to be on screen,
 *  outermost first. This is what `FileTree`'s `defaultOpenIds` takes, so a
 *  link straight to `?path=src/pages/x.tsx` opens with its folders already
 *  expanded instead of at the bare root. One request per id, not a walk. */
export function ancestorIdsFor(path: string): string[] {
  return breadcrumbsFor(parentPath(path)).map((c) => c.path);
}

/** The same ancestors as `FileTreeNode`s, which is what the phone's drill takes
 *  as its trail: a file's folders, outermost first, so a link straight to
 *  `?path=src/pages/x.tsx` opens the level that file is IN rather than the
 *  workspace root. The root is not among them, since `drillTrail` supplies it
 *  and only the adapter knows its label. */
export function folderNodesFor(path: string): FileTreeNode[] {
  return breadcrumbsFor(parentPath(path)).map((c) => ({
    id: c.path,
    label: c.label,
    kind: "branch",
    icon: "folder",
  }));
}

/* Extensions whose icon says something the language does not. Everything else
 * is decided by the language table the editor already owns, so this is a short
 * list of exceptions rather than a second copy of that table. */
const IMAGE_EXTENSIONS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico", "svg", "heic", "tiff",
]);
const DATA_EXTENSIONS = new Set([
  "json", "jsonl", "ndjson", "yaml", "yml", "toml", "csv", "tsv", "sql", "db",
  "sqlite", "sqlite3", "parquet",
]);

function extensionOf(name: string): string {
  const base = name.split("/").filter(Boolean).pop() ?? "";
  const dot = base.lastIndexOf(".");
  // A dotfile's only dot is at index 0, and that is a name, not an extension.
  return dot <= 0 ? "" : base.slice(dot + 1).toLowerCase();
}

/** Which `FileTree` icon a file gets. `resolved` is what
 *  `languageForPath` (from `@workshop/ui/editor`) already answered for the same
 *  path, so a file is drawn as code exactly when the editor would open it as
 *  code, and adding a language stays one row in that one table. */
export function fileIconFor(
  path: string,
  resolved: { language: string | null; profile: "prose" | "code" },
): FileTreeIconHint {
  const ext = extensionOf(path);
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (DATA_EXTENSIONS.has(ext)) return "data";
  if (resolved.profile === "prose") return "doc";
  return resolved.language ? "code" : "file";
}

export type FileViewMode = "binary" | "truncated" | "readonly" | "editable";

/** What the viewer does with one file, from the four flags the file API
 *  carries. `editable` is about the BYTES (is this text, and small enough to
 *  put in an editor) and `writable` is about the LOCATION (does this workspace
 *  permit a write here at all); both are the backend's answer and neither is
 *  guessed here.
 *
 *  Order matters: a binary file that also reports `truncated` is reported as
 *  binary first, since that is the more fundamental reason nothing sensible
 *  can be shown. */
export function viewModeFor(f: {
  editable: boolean;
  truncated: boolean;
  encoding: string;
  writable: boolean;
}): FileViewMode {
  if (f.encoding === "binary") return "binary";
  if (f.truncated) return "truncated";
  if (!f.writable || !f.editable) return "readonly";
  return "editable";
}
