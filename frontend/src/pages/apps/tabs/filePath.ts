// Pure rules behind the Files tab: turning a relative directory path into a
// breadcrumb trail, and turning one file's read response into what the
// viewer is allowed to do with it. Extracted so both survive without a DOM
// harness (frontend/CLAUDE.md: "the target of a test is always the pure rule
// behind a behaviour").

export type Crumb = { label: string; path: string };

/** Breadcrumbs for a relative directory path ("" = the app's file root).
 *  Each crumb's `path` is what GET /api/apps/{id}/files?path= takes to land
 *  there; the root itself is not a crumb here because its label is the app's
 *  own root name, which the caller already has and this module does not. */
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
 *  there is nowhere above the app's own file root to go. */
export function parentPath(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

export type FileViewMode = "binary" | "truncated" | "readonly" | "editable";

/** What the simplest-honest viewer does with one file, from the three flags
 *  AppFileContent actually carries. Order matters: a binary file that also
 *  happens to report `truncated` is still reported as binary first, since
 *  that is the more fundamental reason nothing sensible can be edited. */
export function viewModeFor(f: { editable: boolean; truncated: boolean; encoding: string }): FileViewMode {
  if (f.encoding === "binary") return "binary";
  if (f.truncated) return "truncated";
  if (!f.editable) return "readonly";
  return "editable";
}
