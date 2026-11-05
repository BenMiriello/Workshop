// Settings > Apps > Catalog: the apps this installation can install from its
// app catalog, each with an on/off switch. On installs the app if needed and
// starts it; off stops it, and it stays off. Nothing here names an app: the
// list is the catalog repository's data.

import { useCallback, useEffect, useState } from "react";
import {
  Button, Notice, SectionLabel, Skeleton, Stack, StateBlock, Table, TableScroll, Toggle,
  shortHttpError,
} from "@workshop/ui";
import type { components } from "../../api/schema.gen";
import { overlayApps } from "../../lib/overlayApps/store";

type CatalogList = components["schemas"]["CatalogList"];
type CatalogApp = components["schemas"]["CatalogApp"];

async function readError(r: Response): Promise<string> {
  const text = await r.text().catch(() => "");
  try {
    const detail = (JSON.parse(text) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch { /* not JSON */ }
  return shortHttpError(r.status, r.statusText, text);
}

export function AppCatalog() {
  const [list, setList] = useState<CatalogList | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ slug: string; message: string } | null>(null);

  const load = useCallback(async (refresh = false) => {
    setErr(null);
    try {
      const r = await fetch(`/api/apps/catalog${refresh ? "?refresh=true" : ""}`);
      if (!r.ok) throw new Error(await readError(r));
      setList(await r.json() as CatalogList);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const toggle = async (app: CatalogApp, on: boolean) => {
    setBusy(app.slug);
    setFailed(null);
    try {
      const r = await fetch(`/api/apps/catalog/${encodeURIComponent(app.slug)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on }),
      });
      if (!r.ok) throw new Error(await readError(r));
      const row = await r.json() as CatalogApp;
      setList((cur) => cur && { ...cur, apps: cur.apps.map((a) => (a.slug === row.slug ? row : a)) });
      void overlayApps.refresh();
    } catch (e) {
      setFailed({ slug: app.slug, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const header = <SectionLabel>Catalog</SectionLabel>;

  if (err && !list) {
    return (
      <Stack>
        {header}
        <StateBlock variant="error" title="Couldn't load the app catalog"
          hint={<code>GET /api/apps/catalog ({err})</code>}
          action={<Button size="sm" onClick={() => void load(true)}>Retry</Button>} />
      </Stack>
    );
  }
  if (!list) return <Stack>{header}<Skeleton /><Skeleton /></Stack>;
  if (!list.configured) {
    return (
      <Stack>
        {header}
        <StateBlock title="No app catalog set"
          hint={<>Set <code>WORKSHOP_APP_CATALOG_URL</code> in this installation's instance.env to a git repository holding <code>catalog.json</code>, then restart the backend.</>} />
      </Stack>
    );
  }
  if (list.apps.length === 0) {
    return (
      <Stack>
        {header}
        <StateBlock variant={list.error ? "error" : "empty"}
          title={list.error ? "Couldn't fetch the app catalog" : "The catalog lists no apps"}
          hint={list.error ? <code>{list.error}</code> : <>Add an entry to the catalog repository's <code>catalog.json</code>.</>}
          action={<Button size="sm" onClick={() => void load(true)}>Check again</Button>} />
      </Stack>
    );
  }

  return (
    <Stack>
      {header}
      {list.error && <Notice tone="warn" detail={list.error}>Showing the catalog as last fetched.</Notice>}
      {failed && (
        <Notice tone="error" detail={failed.message} where={`PUT /api/apps/catalog/${failed.slug}`}>
          Couldn't change that app.
        </Notice>
      )}
      <TableScroll>
        <Table>
          <thead>
            <tr><th>App</th><th>Status</th></tr>
          </thead>
          <tbody>
            {list.apps.map((a) => (
              <tr key={a.slug}>
                <td>
                  <div>{a.name}</div>
                  {a.description && <div className="ws-muted">{a.description}</div>}
                </td>
                <td>
                  <Toggle checked={a.on} disabled={busy !== null}
                    label={a.on ? "On" : "Off"}
                    onChange={(next) => void toggle(a, next)} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableScroll>
    </Stack>
  );
}
