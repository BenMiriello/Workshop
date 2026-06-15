import { useEffect, useMemo, useState } from "react";
import { Plus, Trash2, ShieldCheck, ArrowRight, Users, Network } from "lucide-react";
import {
  ActionBar,
  Button,
  Card,
  PageHeader,
  Row,
  SectionLabel,
  Select,
  Input,
  StateBlock,
  StatusDot,
  Skeleton,
  Tag,
  catFor,
} from "../../ui";

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
  const [manifests, setManifests] = useState<Manifests | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);

  const load = async () => {
    setErr(null);
    try {
      const [g, m] = await Promise.all([
        fetch("/api/base-apps/grants").then(async (r) => {
          if (!r.ok) throw new Error(`grants: ${r.status} ${await r.text()}`);
          return r.json() as Promise<GrantsDoc>;
        }),
        fetch("/api/base-apps/manifests").then(async (r) => {
          if (!r.ok) throw new Error(`manifests: ${r.status} ${await r.text()}`);
          return r.json() as Promise<{ apps: Manifests }>;
        }),
      ]);
      const normalized: GrantsDoc = {
        grants: Array.isArray(g.grants) ? g.grants : [],
        guests: Array.isArray(g.guests) ? g.guests : [],
      };
      setDoc(normalized);
      setBaseline(JSON.stringify(normalized));
      setManifests(m.apps || {});
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    load();
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
      const r = await fetch("/api/base-apps/grants", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
      <div style={{ maxWidth: 880 }}>
        <PageHeader title="Grants" />
        <StateBlock
          variant="error"
          title="Couldn't load grants"
          hint={<code>/api/base-apps/grants</code>}
          action={<Button onClick={load}>Retry</Button>}
        />
      </div>
    );
  }
  if (!doc || !manifests) {
    return (
      <div style={{ maxWidth: 880 }}>
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
    <div style={{ maxWidth: 880 }}>
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
            You are always fully permitted across every app. These grants only govern
            cross-app and guest access (default-deny): a shared surface reaches another
            app's data only when a row below allows it.
          </span>
        </Row>
      </Card>

      {/* 1) Capability map --------------------------------------------------- */}
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Network size={14} strokeWidth={1.8} aria-hidden />}>Capability map</SectionLabel>
        <Card>
          {apps.length === 0 ? (
            <StateBlock title="No apps declared" hint={<>No manifests found. Apps declare provides/needs in <code>apps.json</code>.</>} />
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

      <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-3)" }}>
        Source view per app: <code>/api/base-apps/permissions/&lt;app&gt;</code> (operator perspective).
      </p>
    </div>
  );
}
