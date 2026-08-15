import { shortHttpError } from "./httpError";
// Client for the local app + kit registry (the marketplace).
// Backend: /api/base-apps/registry/* (docs/workshop-v1/03-ui-kit-and-marketplace.md).
// Local-now, remote-later: the same interface backs both, so this client does
// not need to change when the registry moves to a remote host.

export type RegistryKind = "app" | "kit";

export type RegistryItem = {
  id: string;
  kind: RegistryKind;
  title: string;
  description: string;
  version: string;
  source_slug: string;
  sensitivity: string;
  published_at: string;
  // The backend may carry more (manifest, history, bundle_path); the UI reads
  // only the fields above, so the rest is left open.
  [k: string]: unknown;
};

// Install forks the backing project into the workspace and returns the new
// project entry (at minimum a slug + title).
export type InstalledProject = {
  slug: string;
  title?: string;
  [k: string]: unknown;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

export const marketplaceApi = {
  listItems: () =>
    fetch("/api/base-apps/registry")
      .then(j<{ items: RegistryItem[] }>)
      .then((r) => r.items),

  getItem: (id: string) =>
    fetch(`/api/base-apps/registry/${encodeURIComponent(id)}`).then(j<RegistryItem>),

  // Publish a project as a new local release. The backend resolves title,
  // sensitivity, and the next version from the project + its manifest.
  publish: (body: { slug: string; version?: string; kind?: RegistryKind; description?: string }) =>
    fetch("/api/base-apps/registry/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<RegistryItem>),

  // Fork an item into the workspace; returns the new project entry.
  install: (id: string, body: { target_slug?: string } = {}) =>
    fetch(`/api/base-apps/registry/${encodeURIComponent(id)}/install`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(j<InstalledProject>),
};
