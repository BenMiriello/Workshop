import { useEffect, useMemo, useState } from "react";
import { Database, Check } from "lucide-react";
import {
  Modal,
  Button,
  Row,
  SectionLabel,
  Checkbox,
  StateBlock,
  Skeleton,
  Tag,
  StatusDot,
  catFor,
} from "@workshop/ui";
import { shortHttpError } from "../lib/httpError";

// GrantPicker — the Store broker (Phase 4).
//
// When an app or agent needs "a Store of type X", this presents the matching
// stores (GET /api/stores?type=X), the operator picks one and the verbs it may
// use, and we mint a scoped grant (POST /api/grants/mint). The returned grant's
// `id` is the capability token the requester then checks per invocation.
//
// This is the INTEROP mechanism, not a confirmation gate: it runs once when a
// binding is set up, never per action. No re-prompting.

type Store = {
  uri: string;
  owner_app: string;
  name: string;
  type: string | null;
  path: string;
};

type Grant = {
  id: string;
  from: string;
  to: string;
  store_uri: string;
  verbs: string[];
  caveats: Record<string, unknown>;
};

type Verb = "read" | "write" | "append";
const ALL_VERBS: Verb[] = ["read", "write", "append"];

export default function GrantPicker({
  open,
  onClose,
  type,
  to,
  fromApp = "",
  defaultVerbs = ["read"],
  onGranted,
}: {
  open: boolean;
  onClose: () => void;
  // The store type to broker (e.g. "Note"). Omit to offer every store.
  type?: string;
  // The requester the grant is minted TO (an agent slug / app id).
  to: string;
  // The store owner the grant is FROM (optional; the picked store's owner_app
  // is authoritative and used at mint time).
  fromApp?: string;
  defaultVerbs?: Verb[];
  onGranted?: (grant: Grant) => void;
}) {
  const [stores, setStores] = useState<Store[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [verbs, setVerbs] = useState<Verb[]>(defaultVerbs);
  const [minting, setMinting] = useState(false);

  const load = async () => {
    setErr(null);
    setStores(null);
    try {
      const qs = type ? `?type=${encodeURIComponent(type)}` : "";
      const r = await fetch(`/api/stores${qs}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const data = (await r.json()) as { stores: Store[] };
      setStores(Array.isArray(data.stores) ? data.stores : []);
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    if (open) {
      setPicked(null);
      setVerbs(defaultVerbs);
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, type]);

  const pickedStore = useMemo(
    () => stores?.find((s) => s.uri === picked) ?? null,
    [stores, picked],
  );

  const toggleVerb = (v: Verb) =>
    setVerbs((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));

  const mint = async () => {
    if (!pickedStore || verbs.length === 0) return;
    setMinting(true);
    setErr(null);
    try {
      const r = await fetch("/api/grants/mint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_app: pickedStore.owner_app || fromApp,
          to,
          store_uri: pickedStore.uri,
          verbs,
        }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const grant = (await r.json()) as Grant;
      onGranted?.(grant);
      onClose();
    } catch (e) {
      setErr(`Mint failed: ${String(e)}`);
    } finally {
      setMinting(false);
    }
  };

  const title = type ? `Pick a Store of type ${type}` : "Pick a Store";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      actions={
        <>
          <Button
            variant="primary"
            onClick={mint}
            loading={minting}
            disabled={!pickedStore || verbs.length === 0}
          >
            Grant access
          </Button>
          <Button variant="ghost" type="button" onClick={onClose} disabled={minting}>
            Cancel
          </Button>
        </>
      }
    >
      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-4)" }}>
        Gives <span className="ws-mono">{to || "the requester"}</span> access to the store you
        choose. Set once, not asked again per action.
      </p>

      {/* four states over the store list */}
      {err && !stores ? (
        <StateBlock
          variant="error"
          title="Couldn't load stores"
          hint={<code>/api/stores</code>}
          action={<Button onClick={load}>Retry</Button>}
        />
      ) : !stores ? (
        <>
          <Skeleton width="60%" />
          <Skeleton width="80%" style={{ marginTop: "var(--space-2)" }} />
        </>
      ) : stores.length === 0 ? (
        <StateBlock
          title={type ? `No stores of type ${type}` : "No stores declared"}
          hint="Nothing to grant over yet. Install an app that owns a store."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
          {stores.map((s) => {
            const on = s.uri === picked;
            return (
              <button
                key={s.uri}
                type="button"
                onClick={() => setPicked(s.uri)}
                aria-pressed={on}
                className={on ? "ws-card is-selected" : "ws-card"}
                style={{
                  textAlign: "left",
                  cursor: "pointer",
                  border: on ? "1px solid var(--accent)" : "1px solid var(--border)",
                  padding: "var(--space-3)",
                }}
              >
                <Row between style={{ alignItems: "center" }}>
                  <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    <Database size={16} strokeWidth={1.8} aria-hidden className="ws-muted" />
                    <span style={{ fontWeight: 600 }}>
                      {s.owner_app} · {s.name}
                    </span>
                    {s.type && <Tag hue={catFor(s.type)}>{s.type}</Tag>}
                  </Row>
                  {on && <Check size={16} strokeWidth={1.8} aria-hidden style={{ color: "var(--accent)" }} />}
                </Row>
                <div
                  className="ws-muted ws-mono"
                  style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-1)" }}
                >
                  {s.uri}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* verbs picker — only once a store is chosen */}
      {pickedStore && (
        <div style={{ marginTop: "var(--space-4)" }}>
          <SectionLabel>Verbs</SectionLabel>
          <Row wrap style={{ gap: "var(--space-3)", marginTop: "var(--space-2)" }}>
            {ALL_VERBS.map((v) => (
              <Checkbox
                key={v}
                checked={verbs.includes(v)}
                onChange={() => toggleVerb(v)}
                label={v}
              />
            ))}
          </Row>
          {verbs.length === 0 && (
            <div style={{ marginTop: "var(--space-2)" }}>
              <StatusDot status="warn">pick at least one verb</StatusDot>
            </div>
          )}
        </div>
      )}

      {err && stores && (
        <p className="ws-field__error" style={{ marginTop: "var(--space-3)" }}>{err}</p>
      )}
    </Modal>
  );
}
