import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Database, RefreshCw } from "lucide-react";
import { Button, Card, Row, Stack, PageHeader, StateBlock, StatusDot, Badge, Tag, Skeleton, Field, Input, Select, catFor, TableScroll, Table, shortHttpError } from "@workshop/ui";

import { anyPresent, tierKind } from "../lib/stores";
import type { components } from "../api/schema.gen";

// The row and the whole catalog, both named from the generated client. The
// hand-written copy had `path?` and `tier?` optional where the route declares
// both required, so this page was written to render a store with no tier that
// the server cannot send.
type Store = components["schemas"]["StoreRow"];
type StoreCatalog = components["schemas"]["StoreCatalog"];

// /stores — cross-app catalog. Every declared Store, filterable by type and
// tier. Each row links to the store's detail page.
export default function Stores() {
  const [stores, setStores] = useState<Store[] | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  const [tiers, setTiers] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [type, setType] = useState<string>("all");
  const [tier, setTier] = useState<string>("all");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch("/api/stores");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j: StoreCatalog = await r.json();
      setStores(j.stores ?? []);
      setTypes(j.types ?? []);
      setTiers(j.tiers ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    if (!stores) return [];
    return stores.filter((s) => {
      if (type !== "all" && s.type !== type) return false;
      if (tier !== "all" && s.tier !== tier) return false;
      if (q.trim()) {
        const needle = q.trim().toLowerCase();
        const hay = [s.uri, s.owner_app, s.name, s.host_project || "", s.type || ""].join(" ").toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [stores, type, tier, q]);

  // A column nothing fills is not a column. `host_project` is null for every
  // store on an installation with no project-hosted ones, and it drew 22 rows
  // of dashes. It returns by itself when a store has one.
  const showHost = anyPresent(filtered, (s) => s.host_project);

  return (
    <div>
      <PageHeader
        title={<><Database aria-hidden style={{ verticalAlign: "-3px", marginRight: 6 }} /> Stores</>}
        sub="The declared Store catalog across every app and project."
        actions={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button size="sm" variant="ghost" onClick={load}><RefreshCw aria-hidden /> Refresh</Button>
          </Row>
        }
      />

      <Row wrap between style={{ marginBottom: "var(--space-4)", gap: "var(--space-2)" }}>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          <Field label="Type">
            <Select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="all">All types</option>
              {types.map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          </Field>
          <Field label="Tier">
            <Select value={tier} onChange={(e) => setTier(e.target.value)}>
              <option value="all">All tiers</option>
              {tiers.map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          </Field>
        </Row>
        <Field>
          <Input placeholder="Filter by name / URI / project" value={q} onChange={(e) => setQ(e.target.value)} />
        </Field>
      </Row>

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="40%" /></Stack></Card>
      ) : err ? (
        <StateBlock variant="error" title="Couldn't load stores"
          hint={<code>GET /api/stores ({err})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : filtered.length === 0 ? (
        <StateBlock title="No stores match" hint="Clear filters, or install/register an app that declares a store." />
      ) : (
        <TableScroll>
          <Table>
            <thead>
              <tr>
                <th>Store</th>
                <th>Owner app</th>
                <th>Type</th>
                {showHost && <th>Host project</th>}
                <th>Tier</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.uri}>
                  <td>
                    {/* THE URI HAD ITS OWN COLUMN AND SAID NOTHING NEW: it is
                        `workshop://store/<owner app>/<name>`, which is the two
                        columns beside it spelled out. The store's own page
                        carries it in full, which is where an address to be
                        copied belongs, and the search box below still matches
                        on it. */}
                    <Link to={`/stores/${encodeURIComponent(s.owner_app)}/${encodeURIComponent(s.name)}`}
                          style={{ fontWeight: "var(--fw-semi)" }}>
                      {s.name}
                    </Link>
                  </td>
                  <td><Badge>{s.owner_app}</Badge></td>
                  <td>{s.type ? <Tag hue={catFor(s.type)}>{s.type}</Tag> : <span className="ws-muted">–</span>}</td>
                  {showHost && (
                    <td>
                      {s.host_project ? (
                        <Link to={`/projects/${encodeURIComponent(s.host_project)}`}>{s.host_project}</Link>
                      ) : <span className="ws-muted">–</span>}
                    </td>
                  )}
                  <td><StatusDot status={tierKind(s.tier)}>{s.tier}</StatusDot></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableScroll>
      )}
    </div>
  );
}
