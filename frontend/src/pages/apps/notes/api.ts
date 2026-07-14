// Typed client for the Notes app backend (routes/notes.py, mounted at /notes-app).
// The global fetch is auth-patched (lib/auth.installAuthFetch adds the session
// cookie + CSRF header to every /api call), so no auth code lives here. Every
// call throws on non-2xx with the failing endpoint in the message so the UI can
// surface it in an error StateBlock.

const API = "/api/notes-app";

export interface NoteMeta {
  path: string;
  title: string;
  size: number;
  modified: number;
}

export interface NoteLink {
  raw: string;
  target: string;
  resolved: string | null;
}

export interface Note {
  path: string;
  title: string;
  content: string;
  modified: number;
  links: NoteLink[];
}

export interface SearchHit {
  path: string;
  line: number;
  snippet: string;
}

export interface Backlink {
  path: string;
  line: number;
  context: string;
}

export interface WriteResult {
  path: string;
  title: string;
  modified: number;
}

export interface RenameResult extends WriteResult {
  links_updated: number;
  notes_rewritten: number;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = j?.detail ? `: ${j.detail}` : "";
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${method} ${path} → ${res.status}${detail}`);
  }
  return res.json() as Promise<T>;
}

const enc = (p: string) => p.split("/").map(encodeURIComponent).join("/");

export const notesApi = {
  list: () => req<{ notes: NoteMeta[]; count: number }>("GET", "/notes"),
  read: (path: string) => req<Note>("GET", `/notes/${enc(path)}`),
  search: (q: string) =>
    req<{ query: string; results: SearchHit[]; count: number }>("GET", `/search?q=${encodeURIComponent(q)}`),
  backlinks: (path: string) =>
    req<{ path: string; backlinks: Backlink[]; count: number }>("GET", `/backlinks/${enc(path)}`),
  create: (path: string, content: string) => req<WriteResult>("POST", "/notes", { path, content }),
  update: (path: string, content: string) => req<WriteResult>("PUT", `/notes/${enc(path)}`, { content }),
  rename: (path: string, to: string) => req<RenameResult>("POST", `/notes/${enc(path)}/rename`, { to }),
  remove: (path: string) => req<{ path: string; deleted: boolean }>("DELETE", `/notes/${enc(path)}`),
};
