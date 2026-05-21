import { useCallback, useEffect, useMemo, useState } from "react";

type Account = {
  id: string;
  name: string;
  official_name?: string;
  type: string;
  subtype?: string;
  mask?: string;
  balance: {
    current: number | null;
    available: number | null;
    iso_currency_code: string | null;
    limit: number | null;
  };
};

type Item = {
  item_id: string;
  institution_name: string | null;
  accounts: Account[];
  error?: string;
};

type Transaction = {
  id: string;
  date: string;
  name: string;
  merchant_name?: string;
  amount: number;
  iso_currency_code?: string;
  category?: string;
  pending?: boolean;
  institution_name?: string;
};

type CacheMeta = { cached: boolean; ts: number; age_s: number; ttl_s: number };
type BalancesResp = { items: Item[]; _cache?: CacheMeta };
type TransactionsResp = { transactions: Transaction[]; _cache?: CacheMeta };

type ServiceProbe = { name: string; status: "up" | "down" | string; http_status?: number | null; latency_ms?: number | null; notes?: string | null };
type ServicesResp = { services: ServiceProbe[] };

const fmt = (n: number | null, ccy: string | null) => {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: ccy || "USD" }).format(n);
};

const fmtAge = (sec: number) => {
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
};

const REVEAL_KEY = "workshop:finance:revealed";

export default function Apps() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [txns, setTxns] = useState<Transaction[] | null>(null);
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [balCache, setBalCache] = useState<CacheMeta | null>(null);
  const [txnCache, setTxnCache] = useState<CacheMeta | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [accountsOpen, setAccountsOpen] = useState(false);
  const [txnsOpen, setTxnsOpen] = useState(false);
  // Privacy: hide $$$ by default. localStorage persists per session so once
  // operator reveals it stays revealed across navigations until tab closes
  // (sessionStorage so it resets when the browser closes — privacy default).
  const [revealed, setRevealed] = useState<boolean>(() => {
    try { return sessionStorage.getItem(REVEAL_KEY) === "1"; } catch { return false; }
  });
  const setRevealedPersist = (v: boolean) => {
    setRevealed(v);
    try { v ? sessionStorage.setItem(REVEAL_KEY, "1") : sessionStorage.removeItem(REVEAL_KEY); } catch {}
  };
  const mask = (v: string) => revealed ? v : v.replace(/[0-9]/g, "•");

  const load = useCallback(async (force: boolean = false) => {
    setRefreshing(true);
    setErr(null);
    try {
      const q = force ? "?refresh=1" : "";
      const [b, t, s] = await Promise.all([
        fetch(`/api/finance/balances${q}`).then((r) => r.json() as Promise<BalancesResp>),
        fetch(`/api/finance/transactions?days=30${force ? "&refresh=1" : ""}`).then((r) => r.json() as Promise<TransactionsResp>),
        fetch("/api/services/").then((r) => r.ok ? r.json() as Promise<ServicesResp> : { services: [] }),
      ]);
      setItems(b.items || []);
      setTxns(t.transactions || []);
      setServices(s.services || []);
      setBalCache(b._cache || null);
      setTxnCache(t._cache || null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(false); }, [load]);

  const findService = (needle: string) => services?.find((s) => s.name.includes(needle));

  const accounts = useMemo(() => items?.flatMap((i) => i.accounts.map((a) => ({ ...a, institution: i.institution_name }))) || [], [items]);

  const summary = useMemo(() => {
    if (!items) return null;
    const all = items.flatMap((i) => i.accounts);
    const liquid = all.filter((a) => a.type === "depository").reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const creditAvail = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.available ?? 0), 0);
    const creditUsed = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.current ?? 0), 0);
    return { liquid, creditAvail, creditUsed, accountCount: all.length };
  }, [items]);

  if (err) return <p className="err">Backend error: {err}</p>;
  if (!items || !txns) return <p>Loading apps…</p>;

  const oldestCacheAge = Math.max(balCache?.age_s ?? 0, txnCache?.age_s ?? 0);
  const cacheStale = oldestCacheAge > (balCache?.ttl_s ?? 300);

  return (
    <div>
      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem", gap: "0.5rem", flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>Finance</h2>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <span className="muted" style={{ fontSize: "0.8rem", color: cacheStale ? "var(--err)" : undefined }}>
              {oldestCacheAge > 0 ? `updated ${fmtAge(oldestCacheAge)}` : "fresh"}
            </span>
            <button onClick={() => setRevealedPersist(!revealed)} style={{ fontSize: "0.85rem", padding: "0.25rem 0.6rem" }} title={revealed ? "Hide values" : "Reveal values"}>
              {revealed ? "Hide" : "Reveal"}
            </button>
            <button onClick={() => load(true)} disabled={refreshing} style={{ fontSize: "0.85rem", padding: "0.25rem 0.6rem" }}>
              {refreshing ? "…" : "Refresh"}
            </button>
          </div>
        </div>

        {/* Top-line summary — always visible, no clicks needed */}
        {summary && (
          <div className="card" style={{ marginBottom: "0.75rem" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem 1rem" }}>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Liquid</p>
                <p style={{ margin: "0.1rem 0 0", fontSize: "1.4rem", fontWeight: 600 }}>{mask(fmt(summary.liquid, "USD"))}</p>
              </div>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Credit avail</p>
                <p style={{ margin: "0.1rem 0 0", fontSize: "1.4rem", fontWeight: 600 }}>{mask(fmt(summary.creditAvail, "USD"))}</p>
              </div>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Credit used</p>
                <p style={{ margin: "0.1rem 0 0", fontSize: "1rem" }}>{mask(fmt(summary.creditUsed, "USD"))}</p>
              </div>
              <div>
                <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Accounts</p>
                <p style={{ margin: "0.1rem 0 0", fontSize: "1rem" }}>{summary.accountCount} · {items.length} institution{items.length === 1 ? "" : "s"}</p>
              </div>
            </div>
          </div>
        )}

        {/* Accounts collapsed by default — operator complaint: "always all open" */}
        <details open={accountsOpen} onToggle={(e) => setAccountsOpen((e.target as HTMLDetailsElement).open)} style={{ marginBottom: "0.5rem" }}>
          <summary style={{ cursor: "pointer", padding: "0.5rem 0", userSelect: "none" }}>
            <span style={{ fontWeight: 500 }}>Accounts ({accounts.length})</span>
            <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.85rem" }}>{accountsOpen ? "collapse" : "expand"}</span>
          </summary>
          <ul className="cards" style={{ marginTop: "0.5rem" }}>
            {accounts.map((a) => (
              <li key={a.id} className="card">
                <h3 style={{ margin: 0, fontSize: "1rem" }}>{a.name}</h3>
                <p className="muted">{a.institution} · {a.subtype || a.type}{a.mask ? ` · ··${a.mask}` : ""}</p>
                <p style={{ fontSize: "1.15rem", margin: "0.25rem 0" }}>{mask(fmt(a.balance.current, a.balance.iso_currency_code))}</p>
                {a.balance.available != null && a.balance.available !== a.balance.current && (
                  <p className="muted">Available: {mask(fmt(a.balance.available, a.balance.iso_currency_code))}</p>
                )}
              </li>
            ))}
          </ul>
        </details>

        {/* Transactions collapsed by default */}
        <details open={txnsOpen} onToggle={(e) => setTxnsOpen((e.target as HTMLDetailsElement).open)}>
          <summary style={{ cursor: "pointer", padding: "0.5rem 0", userSelect: "none" }}>
            <span style={{ fontWeight: 500 }}>Recent transactions ({txns.length} · 30d)</span>
            <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.85rem" }}>{txnsOpen ? "collapse" : "expand"}</span>
          </summary>
          {txns.length === 0 ? (
            <p className="muted">No transactions.</p>
          ) : (
            <div className="table-scroll" style={{ marginTop: "0.5rem" }}>
              <table>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                    <th>Date</th><th>Name</th><th>Category</th><th style={{ textAlign: "right" }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {txns.slice(0, 50).map((t) => (
                    <tr key={t.id} style={{ borderBottom: "1px solid var(--border)", opacity: t.pending ? 0.6 : 1 }}>
                      <td className="muted">{t.date}</td>
                      <td>{t.merchant_name || t.name}</td>
                      <td className="muted">{t.category}</td>
                      <td style={{ textAlign: "right" }}>{mask(fmt(-t.amount, t.iso_currency_code || "USD"))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </details>
      </section>

      <section style={{ marginTop: "2rem" }}>
        <h2>Health</h2>
        <ul className="cards">
          <li className="card" style={{ padding: 0 }}>
            <a
              href={`http://${window.location.hostname}:3100`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: "block", padding: "1rem", color: "inherit", textDecoration: "none" }}
            >
              <h3 style={{ margin: 0, fontSize: "1rem" }}>Open Wearables ↗</h3>
              <p className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                {`http://${window.location.hostname}:3100`}
              </p>
              <p className="muted">Wearables data UI</p>
            </a>
          </li>
          <li className="card" style={{ padding: 0 }}>
            <a
              href={`http://${window.location.hostname}:8100/docs`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: "block", padding: "1rem", color: "inherit", textDecoration: "none" }}
            >
              <h3 style={{ margin: 0, fontSize: "1rem" }}>OW API docs ↗</h3>
              <p className="muted" style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.8rem" }}>
                {`http://${window.location.hostname}:8100/docs`}
              </p>
              {(() => {
                const probe = findService("open-wearables-backend");
                if (!probe) return <p className="muted">status: probing…</p>;
                return probe.status === "up"
                  ? <p className="muted" style={{ color: "#16a34a" }}>● up · {probe.http_status} · {probe.latency_ms}ms</p>
                  : <p className="err" style={{ fontSize: "0.8rem" }}>● down — {probe.notes || `HTTP ${probe.http_status}`}</p>;
              })()}
            </a>
          </li>
        </ul>
      </section>
    </div>
  );
}
