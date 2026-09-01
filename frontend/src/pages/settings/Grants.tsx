import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, ShieldCheck, ArrowRight, Users, Network, Database, Download } from "lucide-react";
import { ActionBar, Button, Card, ConfirmDialog, PageHeader, Row, SectionLabel, Select, Input, Notice, StateBlock, StatusDot, Skeleton, Tag, catFor, shortHttpError } from "@workshop/ui";
import GrantPicker from "../../components/GrantPicker";

// Platform / Grants: operator-facing access-control editor.
// app-platform-plan.md §3.2 + workshop-v1/01-agents-and-chat.md §2.
//
// grants.json governs CROSS-APP and GUEST access only. The operator is always
// fully permitted; these rows exist so a shared/guest surface (e.g. Birds for a
// friend) reaches exactly what it should and nothing else. Default-deny: an
// app's `needs` is satisfied only by a matching grant row.
//
// Config write → explicit Save (ActionBar). Draft rows edit freely; Save commits
// the whole doc via PUT; Cancel reverts to the loaded baseline.

type GrantMode = "read" | "rw";
type Grant = { from: string; to: string; capability: string; mode: GrantMode };
type Guest = { id: string; app: string; capabilities: string[] };
type GrantsDoc = { grants: Grant[]; guests: Guest[] };

// Phase-4 resource-scoped grants: per-invocation capability tokens over Stores.
// Read-only here (minted through the GrantPicker broker); revoke is the one write.
type ResourceGrant = {
  id: string;
  from: string;
  to: string;
  store_uri: string;
  subtree?: boolean;
  verbs: string[];
  caveats?: Record<string, unknown>;
  created_at?: string;
};

// A declared Store: a named, typed handle onto an app's data (GET /api/stores).
type StoreRow = {
  uri: string;
  owner_app: string;
  name: string;
  type: string | null;
  path: string;
};

type Manifest = {
  title: string;
  sensitivity?: string;
  provides?: string[];
  needs?: string[];
};
type Manifests = Record<string, Manifest>;

const MAX_ROWS = 200; // overflow guard for the grants table

export default function Grants() {
  const [doc, setDoc] = useState<GrantsDoc | null>(null);
  const [baseline, setBaseline] = useState<string>(""); // JSON of loaded doc, for dirty + revert
  const [resourceGrants, setResourceGrants] = useState<ResourceGrant[]>([]);
  const [manifests, setManifests] = useState<Manifests | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [revoking, setRevoking] = useState<ResourceGrant | null>(null);

  // Stores catalog (independent async state; not gated by the grants doc load).
  const [stores, setStores] = useState<StoreRow[] | null>(null);
  const [storesErr, setStoresErr] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exported, setExported] = useState<Record<string, string>>({});

  // "Mint a grant" broker (GrantPicker). The requester is operator-supplied here
  // (an agent slug / app id); AgentDetail pre-scopes it instead.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [mintTo, setMintTo] = useState("");

  const load = async () => {
    setErr(null);
    try {
      // Prefer the new /api/grants router (returns app-level doc + resource
      // grants). It supersedes the /base-apps/grants alias.
      const [g, m] = await Promise.all([
        fetch("/api/grants").then(async (r) => {
          if (!r.ok) throw new Error(`grants: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))}`);
          return r.json() as Promise<GrantsDoc & { resource_grants?: ResourceGrant[] }>;
        }),
        fetch("/api/base-apps/manifests").then(async (r) => {
          if (!r.ok) throw new Error(`manifests: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))}`);
          return r.json() as Promise<{ apps: Manifests }>;
        }),
      ]);
      const normalized: GrantsDoc = {
        grants: Array.isArray(g.grants) ? g.grants : [],
        guests: Array.isArray(g.guests) ? g.guests : [],
      };
      setDoc(normalized);
      setBaseline(JSON.stringify(normalized));
      setResourceGrants(Array.isArray(g.resource_grants) ? g.resource_grants : []);
      setManifests(m.apps || {});
    } catch (e) {
      setErr(String(e));
    }
  };

  const loadStores = async () => {
    setStoresErr(null);
    setStores(null);
    try {
      const r = await fetch("/api/stores");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const data = (await r.json()) as { stores: StoreRow[] };
      setStores(Array.isArray(data.stores) ? data.stores : []);
    } catch (e) {
      setStoresErr(String(e));
    }
  };

  const exportStore = async (uri: string) => {
    setExporting(uri);
    setStoresErr(null);
    try {
      const r = await fetch(`/api/stores/export?uri=${encodeURIComponent(uri)}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const data = (await r.json()) as { path: string };
      setExported((cur) => ({ ...cur, [uri]: data.path }));
    } catch (e) {
      setStoresErr(`Export failed: ${String(e)}`);
    } finally {
      setExporting(null);
    }
  };

  const revokeResource = async (grant: ResourceGrant) => {
    setErr(null);
    try {
      const r = await fetch("/api/grants/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: grant.id }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setResourceGrants((cur) => cur.filter((g) => g.id !== grant.id));
    } catch (e) {
      setErr(`Revoke failed: ${String(e)}`);
    } finally {
      setRevoking(null);
    }
  };

  useEffect(() => {
    load();
    loadStores();
  }, []);

  const dirty = doc != null && JSON.stringify(doc) !== baseline;

  // Apps that declare a need (grant targets) and apps that provide (grant sources).
  const apps = useMemo(() => {
    if (!manifests) return [];
    return Object.entries(manifests).map(([id, mf]) => ({ id, ...mf }));
  }, [manifests]);

  const providerOptions = useMemo(
    () => apps.filter((a) => (a.provides?.length ?? 0) > 0),
    [apps],
  );
  const consumerOptions = useMemo(
    () => apps.filter((a) => (a.needs?.length ?? 0) > 0),
    [apps],
  );

  // capabilities a given provider offers (for the capability picker)
  const capsForProvider = (fromId: string): string[] =>
    manifests?.[fromId]?.provides ?? [];

  // Is a given app's need satisfied by some grant in the current draft?
  const needSatisfied = (toId: string, capability: string): boolean =>
    !!doc?.grants.some((gr) => gr.to === toId && gr.capability === capability);

  const save = async () => {
    if (!doc) return;
    setSaving(true);
    setErr(null);
    try {
      const r = await fetch("/api/grants", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setBaseline(JSON.stringify(doc));
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
    } catch (e) {
      setErr(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    if (!baseline) return;
    setDoc(JSON.parse(baseline) as GrantsDoc);
  };

  // ---- grant row mutators (draft only; nothing writes until Save) ----------
  const addGrant = () => {
    const from = providerOptions[0]?.id ?? "";
    const capability = from ? capsForProvider(from)[0] ?? "" : "";
    const to = consumerOptions.find((c) => c.id !== from)?.id ?? "";
    setDoc((d) => (d ? { ...d, grants: [...d.grants, { from, to, capability, mode: "read" }] } : d));
  };
  const updateGrant = (i: number, patch: Partial<Grant>) => {
    setDoc((d) => {
      if (!d) return d;
      const grants = d.grants.slice();
      const next = { ...grants[i], ...patch };
      // when the provider changes, snap the capability to one it actually offers
      if (patch.from && !capsForProvider(patch.from).includes(next.capability)) {
        next.capability = capsForProvider(patch.from)[0] ?? "";
      }
      grants[i] = next;
      return { ...d, grants };
    });
  };
  const removeGrant = (i: number) => {
    setDoc((d) => (d ? { ...d, grants: d.grants.filter((_, j) => j !== i) } : d));
  };

  // ---- guest row mutators --------------------------------------------------
  const allCapabilities = useMemo(() => {
    const set = new Set<string>();
    apps.forEach((a) => (a.provides ?? []).forEach((c) => set.add(c)));
    return Array.from(set).sort();
  }, [apps]);

  const shareableApps = useMemo(
    () => apps.filter((a) => a.sensitivity === "shareable"),
    [apps],
  );

  const addGuest = () => {
    const app = shareableApps[0]?.id ?? apps[0]?.id ?? "";
    setDoc((d) => (d ? { ...d, guests: [...d.guests, { id: "", app, capabilities: [] }] } : d));
  };
  const updateGuest = (i: number, patch: Partial<Guest>) => {
    setDoc((d) => {
      if (!d) return d;
      const guests = d.guests.slice();
      guests[i] = { ...guests[i], ...patch };
      return { ...d, guests };
    });
  };
  const toggleGuestCap = (i: number, cap: string) => {
    setDoc((d) => {
      if (!d) return d;
      const guests = d.guests.slice();
      const cur = guests[i].capabilities;
      const has = cur.includes(cap);
      guests[i] = { ...guests[i], capabilities: has ? cur.filter((c) => c !== cap) : [...cur, cap] };
      return { ...d, guests };
    });
  };
  const removeGuest = (i: number) => {
    setDoc((d) => (d ? { ...d, guests: d.guests.filter((_, j) => j !== i) } : d));
  };

  // ---- four async states ---------------------------------------------------
  if (err && !doc) {
    return (
      <div>
        <PageHeader title="Grants" />
        <StateBlock
          variant="error"
          title="Couldn't load grants"
          hint={<code>/api/grants</code>}
          action={<Button onClick={load}>Retry</Button>}
        />
      </div>
    );
  }
  if (!doc || !manifests) {
    return (
      <div>
        <PageHeader title="Grants" />
        <Card>
          <Skeleton width="40%" />
          <Skeleton width="70%" style={{ marginTop: "var(--space-3)" }} />
          <Skeleton width="55%" style={{ marginTop: "var(--space-2)" }} />
        </Card>
      </div>
    );
  }

  const overflow = doc.grants.length > MAX_ROWS;

  return (
    <div>
      <PageHeader
        title="Platform · Grants"
        sub={`${doc.grants.length} grant${doc.grants.length === 1 ? "" : "s"} · ${doc.guests.length} guest${doc.guests.length === 1 ? "" : "s"}`}
      />

      {/* Operator-always-permitted note. Frames grants as the sharing boundary,
          not a guardrail on the operator's own access. */}
      <Card flat style={{ marginBottom: "var(--space-6)" }}>
        <Row style={{ alignItems: "flex-start", gap: "var(--space-3)" }}>
          <ShieldCheck size={16} strokeWidth={1.8} aria-hidden style={{ marginTop: 2, color: "var(--accent)", flexShrink: 0 }} />
          <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
            You always have full access to every app. These grants only control what one app
            can reach in another, and nothing is allowed until a row below allows it.
          </span>
        </Row>
      </Card>

      {/* 1) Capability map --------------------------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Network size={14} strokeWidth={1.8} aria-hidden />}>Capability map</SectionLabel>
        <Card>
          {apps.length === 0 ? (
            <StateBlock title="No apps declared" hint="Nothing here until an app declares what it offers and what it needs." />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {apps.map((a) => (
                <div
                  key={a.id}
                  style={{ paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)" }}
                >
                  <Row between wrap style={{ alignItems: "baseline" }}>
                    <span style={{ fontWeight: 600 }}>
                      {a.title}{" "}
                      <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", fontWeight: 400 }}>{a.id}</span>
                    </span>
                    <StatusDot status={a.sensitivity === "shareable" ? "warn" : "ok"}>
                      {a.sensitivity === "shareable" ? "shareable" : "private"}
                    </StatusDot>
                  </Row>
                  <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>provides</span>
                    {(a.provides?.length ?? 0) === 0 ? (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>none</span>
                    ) : (
                      a.provides!.map((c) => (
                        <Tag key={c} hue={catFor(c)}>{c}</Tag>
                      ))
                    )}
                  </Row>
                  <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>needs</span>
                    {(a.needs?.length ?? 0) === 0 ? (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>none</span>
                    ) : (
                      a.needs!.map((c) =>
                        needSatisfied(a.id, c) ? (
                          <StatusDot key={c} status="ok">{c}</StatusDot>
                        ) : (
                          <StatusDot key={c} status="danger">{c} · ungranted</StatusDot>
                        ),
                      )
                    )}
                  </Row>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>

      {/* 2) Cross-app grants ------------------------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ alignItems: "center" }}>
          <SectionLabel icon={<ArrowRight size={14} strokeWidth={1.8} aria-hidden />}>Cross-app grants</SectionLabel>
          <Button size="sm" onClick={addGrant} disabled={providerOptions.length === 0}>
            <Plus aria-hidden /> Add grant
          </Button>
        </Row>
        <Card>
          {doc.grants.length === 0 ? (
            <StateBlock
              title="No grants"
              hint="Add a row to let one app read (or read/write) a capability another app provides. Without a grant, cross-app access is denied."
            />
          ) : overflow ? (
            <StateBlock
              title="Too many grants to edit safely"
              hint={`${doc.grants.length} rows exceeds the ${MAX_ROWS}-row editor cap. Trim grants.json on disk.`}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {doc.grants.map((g, i) => {
                const caps = capsForProvider(g.from);
                return (
                  <Row key={i} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    <Select
                      aria-label="From (provider)"
                      value={g.from}
                      onChange={(e) => updateGrant(i, { from: e.target.value })}
                    >
                      {providerOptions.length === 0 && <option value="">no providers</option>}
                      {providerOptions.map((a) => (
                        <option key={a.id} value={a.id}>{a.title}</option>
                      ))}
                    </Select>
                    <ArrowRight size={14} strokeWidth={1.8} aria-hidden className="ws-muted" />
                    <Select
                      aria-label="To (consumer)"
                      value={g.to}
                      onChange={(e) => updateGrant(i, { to: e.target.value })}
                    >
                      <option value="">select app…</option>
                      {apps.map((a) => (
                        <option key={a.id} value={a.id}>{a.title}</option>
                      ))}
                    </Select>
                    <Select
                      aria-label="Capability"
                      value={g.capability}
                      onChange={(e) => updateGrant(i, { capability: e.target.value })}
                    >
                      {caps.length === 0 && <option value="">no capabilities</option>}
                      {caps.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </Select>
                    <Select
                      aria-label="Mode"
                      value={g.mode}
                      onChange={(e) => updateGrant(i, { mode: e.target.value as GrantMode })}
                    >
                      <option value="read">read</option>
                      <option value="rw">read/write</option>
                    </Select>
                    <Button size="sm" variant="danger-ghost" icon aria-label="Remove grant" onClick={() => removeGrant(i)}>
                      <Trash2 aria-hidden />
                    </Button>
                  </Row>
                );
              })}
            </div>
          )}
        </Card>
      </section>

      {/* 3) Guests ----------------------------------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between style={{ alignItems: "center" }}>
          <SectionLabel icon={<Users size={14} strokeWidth={1.8} aria-hidden />}>Guest scopes</SectionLabel>
          <Button size="sm" onClick={addGuest} disabled={apps.length === 0}>
            <Plus aria-hidden /> Add guest
          </Button>
        </Row>
        <Card>
          {doc.guests.length === 0 ? (
            <StateBlock
              title="No guests"
              hint="A guest scope grants an external viewer a named app plus the capabilities they may read (e.g. a friend viewing Yard Birds)."
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
              {doc.guests.map((gu, i) => (
                <div
                  key={i}
                  style={{ paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)" }}
                >
                  <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    <Input
                      aria-label="Guest id"
                      placeholder="guest id (e.g. alex)"
                      value={gu.id}
                      onChange={(e) => updateGuest(i, { id: e.target.value })}
                      style={{ maxWidth: "16ch" }}
                    />
                    <Select
                      aria-label="Guest app"
                      value={gu.app}
                      onChange={(e) => updateGuest(i, { app: e.target.value })}
                    >
                      {apps.map((a) => (
                        <option key={a.id} value={a.id}>{a.title}</option>
                      ))}
                    </Select>
                    <Button size="sm" variant="danger-ghost" icon aria-label="Remove guest" onClick={() => removeGuest(i)}>
                      <Trash2 aria-hidden />
                    </Button>
                  </Row>
                  <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>can read</span>
                    {allCapabilities.length === 0 ? (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>no capabilities declared</span>
                    ) : (
                      allCapabilities.map((c) => {
                        const on = gu.capabilities.includes(c);
                        return on ? (
                          <button
                            key={c}
                            type="button"
                            onClick={() => toggleGuestCap(i, c)}
                            style={{ all: "unset", cursor: "pointer" }}
                            aria-pressed={true}
                            aria-label={`Remove ${c}`}
                          >
                            <Tag hue={catFor(c)}>{c}</Tag>
                          </button>
                        ) : (
                          <Button key={c} size="sm" variant="ghost" onClick={() => toggleGuestCap(i, c)} aria-pressed={false}>
                            <Plus aria-hidden /> {c}
                          </Button>
                        );
                      })
                    )}
                  </Row>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>

      {/* 4) Store-scoped grants (Phase 4) ----------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <Row between wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
          <SectionLabel icon={<Database size={14} strokeWidth={1.8} aria-hidden />}>
            Store grants
          </SectionLabel>
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <Input
              aria-label="Requester"
              placeholder="requester (agent slug / app id)"
              value={mintTo}
              onChange={(e) => setMintTo(e.target.value)}
              style={{ maxWidth: "22ch" }}
            />
            <Button size="sm" onClick={() => setPickerOpen(true)} disabled={!mintTo.trim()}>
              <Plus aria-hidden /> Mint a grant
            </Button>
          </Row>
        </Row>
        <Card>
          {resourceGrants.length === 0 ? (
            <StateBlock
              title="No store grants"
              hint="Grants appear here when an app or agent is given access to a store. There is nothing to add by hand."
            />
          ) : (
            // OVERFLOW SHOWS THE CONTENT. This branch used to return a region
            // reading "Too many grants to list safely" and render NONE of them,
            // so 673 store grants meant the page showed zero and offered no way
            // to see one. A long list is not a safety problem, and rule 9 says
            // overflow is paginated or scrolled, never withheld. The cap still
            // bounds what is drawn; the notice says what was left out and the
            // filter above narrows to it.
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {resourceGrants.length > MAX_ROWS && (
                <Notice>
                  Showing the first {MAX_ROWS} of {resourceGrants.length}. Use the
                  filter to narrow to the ones you want.
                </Notice>
              )}
              {resourceGrants.slice(0, MAX_ROWS).map((g) => {
                const caveatKeys = Object.keys(g.caveats || {});
                return (
                  <div
                    key={g.id}
                    style={{ paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)" }}
                  >
                    <Row between wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <span style={{ fontWeight: 600 }}>{g.to || "(anyone)"}</span>
                        <ArrowRight size={14} strokeWidth={1.8} aria-hidden className="ws-muted" />
                        <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{g.store_uri}</span>
                        {g.subtree && <Tag hue={catFor("subtree")}>subtree</Tag>}
                      </Row>
                      <Button
                        size="sm"
                        variant="danger-ghost"
                        icon
                        aria-label="Revoke grant"
                        onClick={() => setRevoking(g)}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </Row>
                    <Row wrap style={{ marginTop: "var(--space-2)", gap: "var(--space-2)", alignItems: "center" }}>
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>verbs</span>
                      {g.verbs.length === 0 ? (
                        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>none</span>
                      ) : (
                        g.verbs.map((v) => <Tag key={v} hue={catFor(v)}>{v}</Tag>)
                      )}
                      {caveatKeys.length > 0 && (
                        <>
                          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", minWidth: "4.5rem" }}>caveats</span>
                          {caveatKeys.map((k) => (
                            <StatusDot key={k} status="warn">
                              {k}: {String((g.caveats || {})[k])}
                            </StatusDot>
                          ))}
                        </>
                      )}
                    </Row>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </section>

      {/* 5) Stores catalog (Phase 4) ---------------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Database size={14} strokeWidth={1.8} aria-hidden />}>
          Stores
        </SectionLabel>
        <Card>
          {storesErr && !stores ? (
            <StateBlock
              variant="error"
              title="Couldn't load stores"
              hint={<code>/api/stores</code>}
              action={<Button onClick={loadStores}>Retry</Button>}
            />
          ) : !stores ? (
            <>
              <Skeleton width="50%" />
              <Skeleton width="70%" style={{ marginTop: "var(--space-2)" }} />
            </>
          ) : stores.length === 0 ? (
            <StateBlock
              title="No stores declared"
              hint="Install an app that owns a store to see it here."
            />
          ) : stores.length > MAX_ROWS ? (
            <StateBlock
              title="Too many stores to list safely"
              hint={`${stores.length} stores exceeds the ${MAX_ROWS}-row cap.`}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              {stores.map((s) => (
                <div
                  key={s.uri}
                  style={{ paddingBottom: "var(--space-3)", borderBottom: "1px solid var(--border)" }}
                >
                  <Row between wrap style={{ alignItems: "center", gap: "var(--space-2)" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <Database size={16} strokeWidth={1.8} aria-hidden className="ws-muted" />
                      <span style={{ fontWeight: 600 }}>{s.owner_app} · {s.name}</span>
                      {s.type && <Tag hue={catFor(s.type)}>{s.type}</Tag>}
                    </Row>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => exportStore(s.uri)}
                      loading={exporting === s.uri}
                    >
                      <Download aria-hidden /> Export
                    </Button>
                  </Row>
                  <div className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-1)" }}>
                    {s.uri}
                  </div>
                  <div className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-1)" }}>
                    {s.path}
                  </div>
                  {exported[s.uri] && (
                    <div style={{ marginTop: "var(--space-2)" }}>
                      <StatusDot status="ok">exported to {exported[s.uri]}</StatusDot>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {storesErr && stores && (
            <p className="ws-field__error" style={{ marginTop: "var(--space-3)" }}>{storesErr}</p>
          )}
        </Card>
      </section>

      <GrantPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        to={mintTo}
        onGranted={() => load()}
      />

      <ConfirmDialog
        open={revoking != null}
        onCancel={() => setRevoking(null)}
        onConfirm={() => revoking && revokeResource(revoking)}
        title="Revoke store grant?"
        confirmLabel="Revoke"
        danger
        body={
          <>
            The token for <span className="ws-mono">{revoking?.to}</span> over{" "}
            <span className="ws-mono">{revoking?.store_uri}</span> stops working
            immediately. This can't be undone; a new grant must be minted to restore access.
          </>
        }
      />

      {/* ONLY WHILE THERE IS SOMETHING TO SAVE. This was unconditional, and the
          bar is `position: sticky; bottom: 0` with an opaque background, so on
          a page this long it sat across the STORES card at every scroll
          position, hiding a path line and a card header, with both its buttons
          disabled because nothing was dirty. Rule 7 puts the bar with the dirty
          state; a permanent bar is chrome that costs a row of content forever. */}
      {(dirty || flash || (err && doc)) && (
      <ActionBar flash={flash ? "Saved ✓" : dirty ? "unsaved changes" : undefined}>
        <Button variant="primary" onClick={save} loading={saving} disabled={!dirty}>
          Save
        </Button>
        <Button variant="ghost" type="button" onClick={cancel} disabled={!dirty || saving}>
          Cancel
        </Button>
        {err && doc && (
          <span className="ws-field__error" style={{ marginLeft: "var(--space-3)" }}>{err}</span>
        )}
      </ActionBar>
      )}

      <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-3)" }}>
        Source view per app: <code>/api/base-apps/permissions/&lt;app&gt;</code> (operator perspective).
      </p>
    </div>
  );
}
