// Plaid Link for the Workshop finance app — owner-scoped bank linking.
//
// Each user connects their OWN banks. The flow:
//   1. POST /api/finance/link/token  -> a Plaid link_token for this user
//   2. window.Plaid.create({token, onSuccess}) -> the hosted Link UI
//   3. POST /api/finance/link/exchange {public_token} -> backend stores the
//      access token owner-scoped + encrypted; never reaches this client.
//
// The Plaid Link script is loaded from Plaid's CDN on first use (no npm dep).
// fetch() is the auth-patched window.fetch (sends the session cookie + CSRF
// header), so these calls are authenticated as the signed-in user.
import { useCallback, useEffect, useState } from "react";
import { Landmark, Plus, Trash2, RefreshCw } from "lucide-react";

import { Button, Card, ConfirmDialog, SectionLabel, StateBlock, StatusDot } from "@workshop/ui";

const PLAID_SRC = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

declare global {
  interface Window {
    Plaid?: {
      create: (opts: {
        token: string;
        onSuccess: (public_token: string) => void;
        onExit?: (err: unknown) => void;
      }) => { open: () => void };
    };
  }
}

function loadPlaidScript(): Promise<void> {
  if (window.Plaid) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${PLAID_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("Plaid Link failed to load")));
      if (window.Plaid) resolve();
      return;
    }
    const s = document.createElement("script");
    s.src = PLAID_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Plaid Link failed to load"));
    document.head.appendChild(s);
  });
}

export function ConnectBankButton({
  onLinked,
  label = "Connect a bank",
  variant = "primary",
  size = "sm",
}: {
  onLinked?: () => void;
  label?: string;
  variant?: "primary" | "default" | "ghost";
  size?: "sm" | "lg";
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const open = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      await loadPlaidScript();
      const r = await fetch("/api/finance/link/token", { method: "POST" });
      if (!r.ok) throw new Error("Could not start bank linking");
      const { link_token } = await r.json();
      if (!link_token || !window.Plaid) throw new Error("Plaid Link unavailable");
      const handler = window.Plaid.create({
        token: link_token,
        onSuccess: async (public_token: string) => {
          try {
            const ex = await fetch("/api/finance/link/exchange", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ public_token }),
            });
            if (!ex.ok) throw new Error("Link exchange failed");
            onLinked?.();
          } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        },
        onExit: () => setBusy(false),
      });
      handler.open();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [onLinked]);

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
      <Button size={size} variant={variant} loading={busy} disabled={busy} onClick={open}>
        <Plus size={16} strokeWidth={1.8} aria-hidden /> {label}
      </Button>
      {err && <span style={{ color: "var(--danger)", fontSize: "var(--fs-xs)" }}>{err}</span>}
    </span>
  );
}

type LinkedItem = {
  item_id: string;
  institution_name?: string | null;
  accounts?: { id?: string; name?: string; mask?: string | null }[];
  linked_at?: string | null;
};

/** Manage the signed-in user's linked banks: list, connect, unlink. */
export function BankConnections() {
  const [items, setItems] = useState<LinkedItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState<LinkedItem | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch("/api/finance/items");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setItems(Array.isArray(j.items) ? j.items : []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setItems([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onUnlink = async () => {
    if (!unlinking) return;
    setBusy(true);
    try {
      await fetch(`/api/finance/items/${unlinking.item_id}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
      setUnlinking(null);
    }
  };

  return (
    <Card>
      <SectionLabel icon={<Landmark aria-hidden />}>Connected banks</SectionLabel>

      {items === null ? (
        <div className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>loading…</div>
      ) : err ? (
        <StateBlock
          variant="error"
          title="Couldn't load connected banks"
          hint={err}
          action={<Button size="sm" onClick={load}><RefreshCw size={16} strokeWidth={1.8} aria-hidden /> Retry</Button>}
        />
      ) : items.length === 0 ? (
        <StateBlock
          title="No banks connected"
          hint="Connect a bank to pull balances and transactions into Finance and Budget. Your bank credentials go to Plaid, never to Workshop."
          action={<ConnectBankButton onLinked={load} label="Connect your first bank" />}
        />
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            {items.map((it) => (
              <div
                key={it.item_id}
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-3)", flexWrap: "wrap", padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}
              >
                <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
                  <StatusDot status="ok">{it.institution_name || "Bank"}</StatusDot>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {(it.accounts?.length ?? 0)} account{(it.accounts?.length ?? 0) === 1 ? "" : "s"}
                  </span>
                </span>
                <Button size="sm" variant="danger-ghost" onClick={() => setUnlinking(it)}>
                  <Trash2 size={16} strokeWidth={1.8} aria-hidden /> Unlink
                </Button>
              </div>
            ))}
          </div>
          <div style={{ marginTop: "var(--space-3)" }}>
            <ConnectBankButton onLinked={load} variant="default" />
          </div>
        </>
      )}

      <ConfirmDialog
        open={!!unlinking}
        onCancel={() => setUnlinking(null)}
        onConfirm={onUnlink}
        title="Unlink this bank?"
        body={`Removes "${unlinking?.institution_name || "this bank"}" and revokes Workshop's access at Plaid. Its balances and transactions stop appearing. You can connect it again later.`}
        confirmLabel="Unlink"
        busy={busy}
      />
    </Card>
  );
}
