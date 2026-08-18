import { shortHttpError } from "./httpError";
// The one ref format, resolved in-app (memory-and-documentation-model-
// 2026-08-14.md sec 10). A ref names another entity as `workshop:<kind>/...`
// -- never a file path, never a copyable URL -- and this is the ONLY place the
// frontend turns a batch of them into `{kind, title, href, status}` via one
// call to POST /api/refs/resolve, never N. `<Ref>` (components/Ref.tsx) is
// the one chip that renders a resolved entry.

export type RefKind = "note" | "doc" | "ticket" | "chat" | "run" | "project" | "agent" | "app" | "file";

// A file ref's preview descriptor (Layer 2/3 of managed-file-references-and-
// path-scoping-2026-08-18). Only a `file` ref carries one.
export type RefPreview = {
  mime?: string | null;
  size_bytes?: number | null;
  icon?: string | null;
  thumb_url?: string | null;
};

export type ResolvedRef = {
  kind: RefKind;
  title: string;
  href: string;
  status: string;
  preview?: RefPreview | null;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

// `null` for a ref that could not be positively confirmed to still resolve
// (a deleted note, an unknown ticket, a bad shape) -- the backend REFUSES
// rather than returning a chip that would navigate nowhere.
export type ResolveMap = Record<string, ResolvedRef | null>;

export async function resolveRefs(refs: string[]): Promise<ResolveMap> {
  const unique = Array.from(new Set(refs));
  if (!unique.length) return {};
  const { resolved } = await fetch("/api/refs/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refs: unique }),
  }).then(j<{ resolved: ResolveMap }>);
  return resolved;
}

// Every ref whose current body names `ref` -- the "referenced by" read on an
// entity page (design doc sec 10: "backlinks fall out"). An index lookup on
// the backend, not a corpus scan.
export async function backlinksFor(ref: string): Promise<string[]> {
  const { refs } = await fetch(`/api/refs/backlinks?ref=${encodeURIComponent(ref)}`)
    .then(j<{ ref: string; refs: string[] }>);
  return refs;
}
