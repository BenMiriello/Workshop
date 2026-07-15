// Wikilink handling for the preview: the Markdown primitive renders standard
// markdown but not Obsidian `[[wikilinks]]`, so we pre-transform them into
// markdown links with a sentinel href that NoteView intercepts on click.
//
//   [[target]]            -> [target](#wl:<enc resolved-or-target>)
//   [[target|alias]]      -> [alias](#wl:<enc ...>)
//   [[target#heading]]    -> [target#heading](#wl:<enc ...>)   (heading kept in text)
//   unresolved target     -> href uses #wl-missing: so the click opens "create note"
//
// A resolver maps a raw target to an existing note path (or null). It mirrors the
// backend's resolution: exact path match (with/without .md), else unique basename.

import type { NoteMeta } from "./api";

// Full form with heading/alias captured — mirrors vault._WIKILINK_FULL.
const WIKILINK = /\[\[([^\]|#\n]+)(#[^\]|\n]*)?(\|[^\]\n]*)?\]\]/g;

export type Resolver = (rawTarget: string) => string | null;

export function makeResolver(notes: NoteMeta[]): Resolver {
  const byPath = new Set<string>();
  const byStem = new Map<string, string[]>();
  for (const n of notes) {
    byPath.add(n.path);
    const stem = (n.path.split("/").pop() || n.path).replace(/\.md$/i, "").toLowerCase();
    const arr = byStem.get(stem) || [];
    arr.push(n.path);
    byStem.set(stem, arr);
  }
  return (rawTarget: string): string | null => {
    const t = rawTarget.trim();
    if (!t) return null;
    for (const cand of [t, `${t}.md`]) {
      if (byPath.has(cand)) return cand;
    }
    const stem = (t.split("/").pop() || t).replace(/\.md$/i, "").toLowerCase();
    const hits = byStem.get(stem);
    return hits && hits.length === 1 ? hits[0] : null;
  };
}

const escapeMd = (s: string) => s.replace(/([\\`*_[\]()])/g, "\\$1");

// Transform wikilinks in `content` into markdown links for the preview.
export function transformForPreview(content: string, resolve: Resolver): string {
  return content.replace(WIKILINK, (_m, target: string, heading?: string, alias?: string) => {
    const rawTarget = target.trim();
    const resolved = resolve(rawTarget);
    const shown = alias ? alias.slice(1) : `${rawTarget}${heading || ""}`;
    const scheme = resolved ? "#wl:" : "#wl-missing:";
    const payload = resolved || rawTarget;
    return `[${escapeMd(shown)}](${scheme}${encodeURIComponent(payload)})`;
  });
}

// A leading YAML frontmatter block (`--- … ---`) — metadata (dates, etc.), never
// shown in the preview and not part of the note's name.
const FRONTMATTER = /^﻿?---\n[\s\S]*?\n---\n/;

export function stripFrontmatter(content: string): string {
  return content.replace(FRONTMATTER, "");
}

const stripTitleMarks = (s: string) =>
  s
    .trim()
    .replace(/^#{1,6}\s+/, "")
    .replace(/^[*_~`]+|[*_~`]+$/g, "")
    .trim();

// The body shown in the preview: frontmatter removed, and the leading title line
// dropped — the name is rendered once as the note header, not twice.
export function previewBody(content: string, title: string): string {
  const lines = stripFrontmatter(content).split("\n");
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i++;
  if (i < lines.length && stripTitleMarks(lines[i]).toLowerCase() === title.trim().toLowerCase()) {
    lines.splice(0, i + 1);
  }
  return lines.join("\n").trim();
}

// Rename by rewriting the first line — the single source of the note's name.
// Frontmatter is preserved; the filename follows on save (backend auto-syncs it).
export function setNoteTitle(content: string, title: string): string {
  const fm = content.match(FRONTMATTER)?.[0] ?? "";
  const lines = content.slice(fm.length).split("\n");
  let i = 0;
  while (i < lines.length && lines[i].trim() === "") i++;
  const heading = `# ${title.trim()}`;
  if (i < lines.length) lines[i] = heading;
  else lines.push(heading);
  return fm + lines.join("\n");
}

// Parse a sentinel href produced above. Returns the kind + decoded path/target.
export function parseWikiHref(href: string): { kind: "open" | "create"; value: string } | null {
  if (href.startsWith("#wl:")) return { kind: "open", value: decodeURIComponent(href.slice(4)) };
  if (href.startsWith("#wl-missing:")) return { kind: "create", value: decodeURIComponent(href.slice(12)) };
  return null;
}
