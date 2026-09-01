// Settings > Apps: every installed app, and what each one actually has.
//
// The page used to be `appSettingsPanels()` and nothing else, so it rendered
// the panels the frontend registry declares and stopped. Exactly one app
// declares one, so a page titled Apps was one app's config panel and said
// nothing about the other nine. Worse, the one app it showed is the one with
// no screen of its own, so the only app with a settings panel was also the
// only app that appears nowhere else in the UI.
//
// The index below is the fix: the platform's own installed-app list, with the
// two facts a reader of this page wants, stated per app. Where it opens comes
// from the launcher, which is what already resolves an app's home, so this page
// and the launcher grid cannot disagree. Whether it has settings comes from the
// registry, the one frontend file allowed to know an app by id.
//
// NOTHING HERE NAMES AN APP. The list, the names, the icons and the routes are
// all data; this file reads it.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Skeleton, Stack, StateBlock, Table, TableScroll, shortHttpError,
} from "@workshop/ui";

import type { components } from "../../api/schema.gen";
import { AppGlyph } from "../../lib/appIcon";
import { AppSuspense, appSettingsPanel } from "../../apps/registry";

type AppRecord = components["schemas"]["App"];
type LauncherCard = components["schemas"]["LauncherCard"];

/** Where an app opens, as the launcher resolved it, or null when it has no
 *  screen at all. An external app is a link out of this shell, so it keeps its
 *  href and is navigated as one. */
function opensAt(card: LauncherCard | undefined): { to: string; external: boolean } | null {
  if (!card) return null;
  if (card.route) return { to: card.route, external: false };
  if (card.href) return { to: card.href, external: true };
  return null;
}

export default function InstalledApps() {
  const [apps, setApps] = useState<AppRecord[] | null>(null);
  const [cards, setCards] = useState<Record<string, LauncherCard>>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const read = async (url: string) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json();
    };
    Promise.all([
      read("/api/apps") as Promise<components["schemas"]["AppCollection"]>,
      // A failed launcher read costs the "opens on" column and nothing else:
      // the list of installed apps is the other call, and it is the page.
      (read("/api/base-apps/launcher") as Promise<components["schemas"]["LauncherCards"]>)
        .catch(() => ({ apps: [] as LauncherCard[] })),
    ])
      .then(([list, launcher]) => {
        if (!live) return;
        setApps(list.apps ?? []);
        const byId: Record<string, LauncherCard> = {};
        for (const c of launcher.apps ?? []) byId[c.id] = c;
        setCards(byId);
      })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);

  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't load installed apps"
        hint={<code>GET /api/apps ({err})</code>}
      />
    );
  }
  if (!apps) return <Stack><Skeleton /><Skeleton /><Skeleton /></Stack>;
  if (apps.length === 0) {
    return (
      <StateBlock
        title="No apps installed"
        hint="Install an app and it appears here, with whatever settings it declares."
      />
    );
  }

  const withPanels = apps.filter((a) => appSettingsPanel(a.slug));

  return (
    <Stack>
      <TableScroll>
        <Table>
          <thead>
            <tr>
              <th>App</th>
              <th>Opens on</th>
              <th>Settings</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => {
              const where = opensAt(cards[a.slug]);
              const hasPanel = Boolean(appSettingsPanel(a.slug));
              return (
                <tr key={a.slug}>
                  <td>
                    <span className="ws-installed-app">
                      {/* The row's own declaration first, then the launcher
                          card's, which is where an app with no row icon
                          declares one. AppGlyph falls back to a monogram of the
                          name, so a glyph is never missing. */}
                      <AppGlyph icon={a.icon ?? cards[a.slug]?.icon} name={a.name} size={16} />
                      {a.name}
                    </span>
                  </td>
                  <td>
                    {!where ? (
                      /* A real answer, not a gap. An app whose whole frontend
                         is a settings panel has nowhere to land, and saying so
                         is what stops it reading as a missing row. */
                      <span className="ws-muted">no screen of its own</span>
                    ) : where.external ? (
                      <a className="ws-mono" href={where.to}>{where.to}</a>
                    ) : (
                      <Link className="ws-mono" to={where.to}>{where.to}</Link>
                    )}
                  </td>
                  <td>
                    {hasPanel
                      ? <span>below</span>
                      : <span className="ws-muted">no settings</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </TableScroll>

      {withPanels.length > 0 && (
        <AppSuspense>
          {withPanels.map((a) => {
            const Panel = appSettingsPanel(a.slug)!;
            return <Panel key={a.slug} />;
          })}
        </AppSuspense>
      )}
    </Stack>
  );
}
