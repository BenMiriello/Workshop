import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ChevronLeft, RefreshCw, Download, KeyRound, Shield, ExternalLink,
} from "lucide-react";
import { Button, Card, Row, Stack, PageHeader, SectionLabel, StateBlock, Notice, StatusDot, Badge, Tag, Skeleton, Field, Input, Textarea, shortHttpError } from "@workshop/ui";

import { tierKind as tierKindFor } from "../lib/stores";

type Store = {
  uri: string;
  owner_app: string;
  name: string;
  type?: string | null;
  path?: string;
  host_project?: string | null;
  tier?: string;
};

type Policy = {
  uri_prefix?: string;
  tier?: string;
  identity?: string;
  verbs?: string[];
  [k: string]: unknown;
};

type Grant = {
  id?: string;
  token?: string;
  identity?: string;
  uri?: string;
  verbs?: string[];
  expiry?: string | null;
  [k: string]: unknown;
};

// Store detail — the Store entity's home. Shows what the store IS (URI, owner
// app, host project, filesystem path, adapter type, policy tier), the policies
// that gate it, the grants pointing at it, and a read/list preview so the
// operator can see what's inside without going to a shell.
export default function StoreDetail() {
  const { app = "", name = "" } = useParams();
  const storeId = `${app}:${name}`;

  const [store, setStore] = useState<Store | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [contents, setContents] = useState<{ items?: unknown[]; text?: string; [k: string]: unknown } | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [readVerb, setReadVerb] = useState<"list" | "read" | "search">("list");
  const [readPath, setReadPath] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [reading, setReading] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [sr, pr, gr] = await Promise.all([
        fetch("/api/stores"),
        fetch("/api/stores/policies"),
        fetch("/api/grants"),
      ]);
      if (!sr.ok) throw new Error(`stores: ${shortHttpError(sr.status, sr.statusText, await sr.text().catch(() => ""))}`);
      const sj = await sr.json();
      const found = (sj.stores as Store[]).find((s) => s.owner_app === app && s.name === name);
      if (!found) { setErr("store not declared"); setLoading(false); return; }
      setStore(found);
      if (pr.ok) {
        const pj = await pr.json();
        // filter policies that match this URI prefix
        const uriBase = found.uri;
        setPolicies((pj.policies as Policy[]).filter((p) => (p.uri_prefix ?? "").startsWith(uriBase) || uriBase.startsWith(p.uri_prefix ?? "")));
      }
      if (gr.ok) {
        const gj = await gr.json();
        setGrants((gj.grants as Grant[] || []).filter((g) => (g.uri || "").startsWith(found.uri)));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [app, name]);
  useEffect(() => { load(); }, [load]);

  const read = useCallback(async () => {
    setReading(true);
    setBanner(null);
    try {
      const body: Record<string, unknown> = { verb: readVerb, selector: {} };
      if (readVerb === "search" && searchQ) body.selector = { query: searchQ };
      if (readPath) body.path = readPath;
      const r = await fetch(`/api/stores/${encodeURIComponent(storeId)}/read`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setContents(await r.json());
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
      setContents(null);
    } finally {
      setReading(false);
    }
  }, [readVerb, readPath, searchQ, storeId]);

  const doExport = useCallback(async () => {
    if (!store) return;
    setBanner(null);
    try {
      const r = await fetch(`/api/stores/export?uri=${encodeURIComponent(store.uri)}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      setBanner(`Exported to ${j.path}`);
    } catch (e) {
      setBanner(e instanceof Error ? e.message : String(e));
    }
  }, [store]);

  const tierKind = useMemo(() => tierKindFor(store?.tier), [store?.tier]);

  return (
    <div>
      {/* Same entity header as every other record page: a named back link, no
          decorative icon (everything on this route is a store), and the facts
          as one muted line instead of four chips. */}
      <PageHeader
        variant="entity"
        back={<Link to="/stores"><ChevronLeft aria-hidden /> Stores</Link>}
        title={storeId}
        meta={
          store && (
            <>
              <StatusDot status={tierKind}>{store.tier || "unknown"} tier</StatusDot>
              {store.type && <span>{store.type}</span>}
              <span>owned by {store.owner_app}</span>
              {store.host_project && (
                <Link to={`/projects/${encodeURIComponent(store.host_project)}`}>
                  {store.host_project}
                </Link>
              )}
              <span className="ws-mono">{store.uri}</span>
            </>
          )
        }
        actions={
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button size="sm" variant="ghost" onClick={load}><RefreshCw aria-hidden /> Refresh</Button>
            <Button size="sm" variant="ghost" onClick={doExport} disabled={!store}>
              <Download aria-hidden /> Export
            </Button>
          </Row>
        }
      />

      {banner && (
        <Notice action={<Button size="sm" variant="ghost" onClick={() => setBanner(null)}>Dismiss</Button>}>
          {banner}
        </Notice>
      )}

      {loading ? (
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /></Stack></Card>
      ) : err || !store ? (
        <StateBlock variant="error" title="Couldn't load store"
          hint={<code>{err || "not declared"}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>} />
      ) : (
        <Stack gap="lg">
          <section>
            <SectionLabel>Details</SectionLabel>
            <Card>
              <Row wrap style={{ gap: "var(--space-3)" }}>
                <Field label="URI"><code style={{ fontSize: "var(--fs-xs)" }}>{store.uri}</code></Field>
                <Field label="Filesystem path"><code style={{ fontSize: "var(--fs-xs)" }}>{store.path || "(none)"}</code></Field>
                <Field label="Type"><span>{store.type || "(none)"}</span></Field>
                <Field label="Tier"><span>{store.tier || "(unknown)"}</span></Field>
                {store.host_project && (
                  <Field label="Host project"><span>{store.host_project}</span></Field>
                )}
              </Row>
            </Card>
          </section>

          <section>
            <SectionLabel icon={<Shield aria-hidden />}>Policies matching this URI</SectionLabel>
            <Card>
              {policies.length === 0 ? (
                <StateBlock title="No matching policies"
                  hint="Stores without a policy fall to the default tier for their type. Add or edit policies in Settings › Grants." />
              ) : (
                <Stack gap="sm">
                  {policies.map((p, i) => (
                    <Row key={i} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <Badge>{p.tier || "unknown"}</Badge>
                      <code style={{ fontSize: "var(--fs-xs)" }}>{p.uri_prefix}</code>
                      {(p.verbs || []).length > 0 && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{(p.verbs || []).join(", ")}</span>
                      )}
                      {p.identity && <Tag hue="turquoise">{String(p.identity)}</Tag>}
                    </Row>
                  ))}
                </Stack>
              )}
            </Card>
          </section>

          <section>
            <SectionLabel icon={<KeyRound aria-hidden />}>Grants pointing at this store</SectionLabel>
            <Card>
              {grants.length === 0 ? (
                <StateBlock title="No active grants"
                  hint="Nothing has access to this store yet. Grant it from Settings › Grants." />
              ) : (
                <Stack gap="sm">
                  {grants.map((g, i) => (
                    <Row key={i} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      {g.identity && <Tag hue="orchid">{g.identity}</Tag>}
                      {(g.verbs || []).length > 0 && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{(g.verbs || []).join(", ")}</span>
                      )}
                      <code style={{ fontSize: "var(--fs-xs)" }}>{g.uri}</code>
                      {g.expiry && (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>until {g.expiry}</span>
                      )}
                    </Row>
                  ))}
                </Stack>
              )}
            </Card>
          </section>

          <section>
            <SectionLabel icon={<ExternalLink aria-hidden />}>Contents</SectionLabel>
            <Card>
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "flex-end", marginBottom: "var(--space-2)" }}>
                <Field label="Verb">
                  <select
                    value={readVerb}
                    onChange={(e) => setReadVerb(e.target.value as "list" | "read" | "search")}
                    className="ws-select"
                  >
                    <option value="list">list</option>
                    <option value="read">read</option>
                    <option value="search">search</option>
                  </select>
                </Field>
                {readVerb === "search" && (
                  <Field label="Query">
                    <Input value={searchQ} onChange={(e) => setSearchQ(e.target.value)} placeholder="what to find" />
                  </Field>
                )}
                {readVerb !== "search" && (
                  <Field label="Path (optional)">
                    <Input value={readPath} onChange={(e) => setReadPath(e.target.value)} placeholder="folder or item" />
                  </Field>
                )}
                <Button size="sm" variant="primary" onClick={read} loading={reading}>Read</Button>
              </Row>
              {contents == null ? (
                <StateBlock title="Nothing loaded yet" hint="Pick a verb + optional path and press Read." />
              ) : (
                <Textarea
                  rows={12}
                  readOnly
                  value={JSON.stringify(contents, null, 2)}
                />
              )}
            </Card>
          </section>
        </Stack>
      )}
    </div>
  );
}
