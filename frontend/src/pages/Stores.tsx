import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Database, RefreshCw } from "lucide-react";
import {
  Button, Card, Row, Stack, PageHeader, StateBlock, StatusDot, Badge, Tag,
  Skeleton, Field, Input, Select, catFor,
} from "@workshop/ui";
import { TableScroll, Table } from "@workshop/ui";
import { shortHttpError } from "../lib/httpError";


type Store = {
  uri: string;
  owner_app: string;
  name: string;
  type?: string | null;
  path?: string;
  host_project?: string | null;
  tier?: string;
};


// /stores — cross-app catalog. Every declared Store, filterable by type and
// tier. Each row links to the store's detail page.
export default function Stores() {
  const [stores, setStores] = useState<Store[] | null>(null);
  const [types, setTypes] = useState<string[]>([]);
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
      const j = await r.json();
      setStores(j.stores || []);
      setTypes(j.types || []);
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

  const tierKind = (t?: string) => {
    if (t === "open") return "ok" as const;
    if (t === "mediated") return "warn" as const;
    if (t === "closed") return "danger" as const;
    return "stopped" as const;
  };

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
              <option value="open">open</option>
              <option value="mediated">mediated</option>
              <option value="closed">closed</option>
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
                <th>Host project</th>
                <th>Tier</th>
                <th>URI</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.uri}>
                  <td>
                    <Link to={`/stores/${encodeURIComponent(s.owner_app)}/${encodeURIComponent(s.name)}`}
                          style={{ fontWeight: "var(--fw-semi)" }}>
                      {s.name}
                    </Link>
                  </td>
                  <td><Badge>{s.owner_app}</Badge></td>
                  <td>{s.type ? <Tag hue={catFor(s.type)}>{s.type}</Tag> : <span className="ws-muted">–</span>}</td>
                  <td>
                    {s.host_project ? (
                      <Link to={`/projects/${encodeURIComponent(s.host_project)}`}>{s.host_project}</Link>
                    ) : <span className="ws-muted">–</span>}
                  </td>
                  <td><StatusDot status={tierKind(s.tier)}>{s.tier}</StatusDot></td>
                  <td><code style={{ fontSize: "var(--fs-xs)" }}>{s.uri}</code></td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableScroll>
      )}
    </div>
  );
}
