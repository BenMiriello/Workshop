import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

// Full Finance app page. Opens from the Apps grid card. Privacy is handled at
// the *card* layer (no balances shown on the Apps grid). Once the operator has
// tapped into the app, they explicitly asked to see real data — no redaction
// here. The previous design redacted even when expanded, which was the bug.

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

export default function Finance() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [txns, setTxns] = useState<Transaction[] | null>(null);
  const [balCache, setBalCache] = useState<CacheMeta | null>(null);
  const [txnCache, setTxnCache] = useState<CacheMeta | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadBalances = useCallback(async (force: boolean = false) => {
    try {
      const q = force ? "?refresh=1" : "";
      const r = await fetch(`/api/finance/balances${q}`);
      const b = (await r.json()) as BalancesResp;
      setItems(b.items || []);
      setBalCache(b._cache || null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const loadTxns = useCallback(async (force: boolean = false) => {
    try {
      const r = await fetch(`/api/finance/transactions?days=30${force ? "&refresh=1" : ""}`);
      const t = (await r.json()) as TransactionsResp;
      setTxns(t.transactions || []);
      setTxnCache(t._cache || null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const refresh = useCallback(async (force: boolean = false) => {
    setRefreshing(true);
    setErr(null);
    try {
      await Promise.all([loadBalances(force), loadTxns(force)]);
    } finally {
      setRefreshing(false);
    }
  }, [loadBalances, loadTxns]);

  useEffect(() => {
    loadBalances(false);
    loadTxns(false);
  }, [loadBalances, loadTxns]);

  const accounts = useMemo(
    () => items?.flatMap((i) => i.accounts.map((a) => ({ ...a, institution: i.institution_name }))) || [],
    [items],
  );

  const summary = useMemo(() => {
    if (!items) return null;
    const all = items.flatMap((i) => i.accounts);
    const liquid = all.filter((a) => a.type === "depository").reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const creditAvail = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.available ?? 0), 0);
    const creditUsed = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.current ?? 0), 0);
    return { liquid, creditAvail, creditUsed, accountCount: all.length };
  }, [items]);

  const oldestCacheAge = Math.max(balCache?.age_s ?? 0, txnCache?.age_s ?? 0);
  const cacheStale = oldestCacheAge > (balCache?.ttl_s ?? 600);
  const financeLoading = items === null || txns === null;

  return (
    <div>
      {/* Header: back-link + title + freshness + refresh. Mobile-friendly: wraps. */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", gap: "0.5rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", minWidth: 0 }}>
          <Link to="/apps" style={{ color: "var(--muted)", textDecoration: "none", fontSize: "0.9rem", display: "inline-flex", alignItems: "center", gap: "0.2rem" }}>
            <ChevronLeft size={16} strokeWidth={1.8} aria-hidden /> Apps
          </Link>
          <h2 style={{ margin: 0 }}>Finance</h2>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <span className="muted" style={{ fontSize: "0.8rem", color: cacheStale ? "var(--err)" : undefined }}>
            {financeLoading ? "loading…" : oldestCacheAge > 0 ? `updated ${fmtAge(oldestCacheAge)}` : "fresh"}
          </span>
          <button onClick={() => refresh(true)} disabled={refreshing} style={{ fontSize: "0.85rem", padding: "0.25rem 0.6rem" }}>
            {refreshing ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {err && <p className="err" style={{ marginBottom: "1rem" }}>Backend error: {err}</p>}

      {/* Summary tiles — real data, the operator opened the app to see this. */}
      {items === null ? (
        <div className="card" style={{ marginBottom: "1rem", opacity: 0.6 }}>
          <p className="muted" style={{ margin: 0 }}>Loading balances…</p>
        </div>
      ) : summary && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem 1rem" }}>
            <div>
              <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Liquid</p>
              <p style={{ margin: "0.1rem 0 0", fontSize: "1.4rem", fontWeight: 600 }}>{fmt(summary.liquid, "USD")}</p>
            </div>
            <div>
              <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Credit avail</p>
              <p style={{ margin: "0.1rem 0 0", fontSize: "1.4rem", fontWeight: 600 }}>{fmt(summary.creditAvail, "USD")}</p>
            </div>
            <div>
              <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Credit used</p>
              <p style={{ margin: "0.1rem 0 0", fontSize: "1rem" }}>{fmt(summary.creditUsed, "USD")}</p>
            </div>
            <div>
              <p className="muted" style={{ margin: 0, fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>Accounts</p>
              <p style={{ margin: "0.1rem 0 0", fontSize: "1rem" }}>{summary.accountCount} · {items?.length ?? 0} institution{(items?.length ?? 0) === 1 ? "" : "s"}</p>
            </div>
          </div>
        </div>
      )}

      {/* Accounts — always visible inside the app (no more details/summary toggle). */}
      {items !== null && accounts.length > 0 && (
        <section style={{ marginBottom: "1.5rem" }}>
          <h3 style={{ margin: "0 0 0.5rem", fontSize: "1rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Accounts</h3>
          <ul className="cards">
            {accounts.map((a) => (
              <li key={a.id} className="card">
                <h3 style={{ margin: 0, fontSize: "1rem" }}>{a.name}</h3>
                <p className="muted">{a.institution} · {a.subtype || a.type}{a.mask ? ` · ··${a.mask}` : ""}</p>
                <p style={{ fontSize: "1.15rem", margin: "0.25rem 0" }}>{fmt(a.balance.current, a.balance.iso_currency_code)}</p>
                {a.balance.available != null && a.balance.available !== a.balance.current && (
                  <p className="muted">Available: {fmt(a.balance.available, a.balance.iso_currency_code)}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Transactions — also always visible, scroll-on-overflow on mobile. */}
      {txns === null ? (
        <p className="muted" style={{ opacity: 0.6 }}>Loading transactions…</p>
      ) : (
        <TransactionsTable txns={txns} />
      )}
    </div>
  );
}

const TXN_PAGE_SIZE = 25;

function TransactionsTable({ txns }: { txns: Transaction[] }) {
  const [pages, setPages] = useState(1);
  const visible = txns.slice(0, pages * TXN_PAGE_SIZE);
  const hasMore = visible.length < txns.length;
  return (
    <section>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: "1rem", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        Transactions ({visible.length} of {txns.length} · 30d)
      </h3>
      {txns.length === 0 ? (
        <p className="muted">No transactions.</p>
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                  <th>Date</th><th>Name</th><th>Category</th><th style={{ textAlign: "right" }}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => (
                  <tr key={t.id} style={{ borderBottom: "1px solid var(--border)", opacity: t.pending ? 0.6 : 1 }}>
                    <td className="muted">{t.date}</td>
                    <td>{t.merchant_name || t.name}</td>
                    <td className="muted">{t.category}</td>
                    <td style={{ textAlign: "right" }}>{fmt(-t.amount, t.iso_currency_code || "USD")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div style={{ marginTop: "0.7rem", display: "flex", justifyContent: "center" }}>
              <button onClick={() => setPages((p) => p + 1)}>
                Load {Math.min(TXN_PAGE_SIZE, txns.length - visible.length)} more
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
