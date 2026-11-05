// Browser storage an app keeps on this device for itself. An app names it
// `ws-app.<slug>.<name>` (`appStorageName`), and the shell deletes whatever
// belongs to an app that is no longer in the session's app list (removed,
// archived, made private, locked) and everything on sign-out. The app does not
// have to know about any of that.
//
// The list it is checked against is `/api/apps`: every app the session may
// see, whether or not it contributes UI to the shell, and with the apps a
// locked session may not see already left out. An answer that is not a list
// purges nothing.

const PREFIX = "ws-app.";

export const appStorageName = (slug: string, name: string) => `${PREFIX}${slug}.${name}`;

/** The names whose app is not in `keep`. */
export function staleNames(names: readonly string[], keep: ReadonlySet<string>): string[] {
  return names.filter((n) => {
    if (!n.startsWith(PREFIX)) return false;
    const slug = n.slice(PREFIX.length).split(".")[0];
    return !keep.has(slug);
  });
}

/** Delete the storage of every app not in `keep`. Never throws: storage that
 *  cannot be reached (private mode, an old browser) has nothing to purge. */
export async function purgeApps(keep: ReadonlySet<string>): Promise<void> {
  try {
    const known = (await indexedDB.databases?.()) ?? [];
    const names = known.flatMap((d) => (d.name ? [d.name] : []));
    for (const name of staleNames(names, keep)) indexedDB.deleteDatabase(name);
  } catch { /* nothing reachable to purge */ }
  try {
    const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i) ?? "");
    for (const key of staleNames(keys, keep)) localStorage.removeItem(key);
  } catch { /* nothing reachable to purge */ }
}

/** Purge the storage of every app the session can no longer see. */
export async function purgeAbsentApps(): Promise<void> {
  try {
    const r = await fetch("/api/apps");
    if (!r.ok) return;
    const body = (await r.json()) as { apps?: { slug?: string; archived_at?: string | null }[] };
    if (!Array.isArray(body.apps)) return;
    const seen = body.apps.filter((a) => a.slug && !a.archived_at).map((a) => a.slug as string);
    await purgeApps(new Set(seen));
  } catch { /* unreachable: nothing is known, so nothing is purged */ }
}
