import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import ChatDock from "../../components/ChatDock";
import { AppGroupNav } from "../../components/appNav";
import {
  Button,
  Card,
  CardGrid,
  NumCell,
  PageHeader,
  SectionLabel,
  Stat,
  StateBlock,
  Table,
  TableScroll,
  TimeCell,
} from "../../ui";

// Full Finance app page. Opens from the Apps grid card. Privacy is handled at
// the *card* layer (no balances shown on the Apps grid). Once the operator has
// tapped into the app, they explicitly asked to see real data — no redaction
// here.

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
      if (!r.ok) throw new Error(`balances HTTP ${r.status}`);
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
      if (!r.ok) throw new Error(`transactions HTTP ${r.status}`);
      const t = (await r.json()) as TransactionsResp;
      setTxns(t.transactions || []);
      setTxnCache(t._cache || null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  const refresh = useCallback(
    async (force: boolean = false) => {
      setRefreshing(true);
      setErr(null);
      try {
        await Promise.all([loadBalances(force), loadTxns(force)]);
      } finally {
        setRefreshing(false);
      }
    },
    [loadBalances, loadTxns],
  );

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
    const invest = all
      .filter((a) => a.type === "investment" || a.type === "brokerage")
      .reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const creditAvail = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.available ?? 0), 0);
    const creditUsed = all.filter((a) => a.type === "credit").reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const netWorth = liquid + invest - creditUsed;
    return { liquid, invest, creditAvail, creditUsed, netWorth, accountCount: all.length };
  }, [items]);

  const oldestCacheAge = Math.max(balCache?.age_s ?? 0, txnCache?.age_s ?? 0);
  const financeLoading = items === null || txns === null;

  return (
    <div>
      <PageHeader
        title="Finance"
        sub={
          <span>
            {financeLoading ? "loading…" : oldestCacheAge > 0 ? `updated ${fmtAge(oldestCacheAge)}` : "fresh"}
          </span>
        }
        actions={
          <>
            <AppGroupNav />
            <Button size="sm" onClick={() => refresh(true)} loading={refreshing}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {err && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock variant="error" title="Couldn't load finance data" hint={err} />
        </div>
      )}

      {/* Summary KPI tiles. */}
      {summary && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <Card style={{ marginBottom: "var(--space-4)" }}>
            <SectionLabel>Net worth</SectionLabel>
            <div className="ws-stat__num">{fmt(summary.netWorth, "USD")}</div>
            <div className="ws-stat__sub">cash + investments − credit owed</div>
          </Card>
          <div className="ws-card-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
            <Stat label="Liquid" value={fmt(summary.liquid, "USD")} />
            <Stat label="Investments" value={fmt(summary.invest, "USD")} />
            <Stat label="Credit avail" value={fmt(summary.creditAvail, "USD")} />
            <Stat label="Credit used" value={fmt(summary.creditUsed, "USD")} />
            <Stat
              label="Accounts"
              value={summary.accountCount}
              sub={`${items?.length ?? 0} institution${(items?.length ?? 0) === 1 ? "" : "s"}`}
            />
          </div>
        </div>
      )}

      {/* Accounts. */}
      {items !== null && accounts.length > 0 && (
        <section style={{ marginBottom: "var(--space-6)" }}>
          <SectionLabel>Accounts</SectionLabel>
          <CardGrid>
            {accounts.map((a) => (
              <li key={a.id}>
                <Card>
                  <h3 className="ws-card__title">{a.name}</h3>
                  <p className="ws-card__meta">
                    {a.institution} · {a.subtype || a.type}
                    {a.mask ? ` · ··${a.mask}` : ""}
                  </p>
                  <p className="ws-num" style={{ fontSize: "var(--fs-lg)", margin: "var(--space-2) 0 0" }}>
                    {fmt(a.balance.current, a.balance.iso_currency_code)}
                  </p>
                  {a.balance.available != null && a.balance.available !== a.balance.current && (
                    <p className="ws-card__meta">Available: {fmt(a.balance.available, a.balance.iso_currency_code)}</p>
                  )}
                </Card>
              </li>
            ))}
          </CardGrid>
        </section>
      )}

      {/* Transactions. */}
      {txns === null ? (
        <p className="ws-muted">Loading transactions…</p>
      ) : (
        <TransactionsTable txns={txns} />
      )}

      <ChatDock contextKey="finance" title="Accounts assistant" placeholder="Ask about balances or recent spend…" />
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
      <SectionLabel>
        Transactions ({visible.length} of {txns.length} · 30d)
      </SectionLabel>
      {txns.length === 0 ? (
        <StateBlock title="No transactions" hint="No spending in the last 30 days, or Plaid hasn't synced yet." />
      ) : (
        <>
          <TableScroll>
            <Table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Name</th>
                  <th>Category</th>
                  <th style={{ textAlign: "right" }}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => (
                  <tr key={t.id} aria-disabled={t.pending || undefined}>
                    <TimeCell>{t.date}</TimeCell>
                    <td>{t.merchant_name || t.name}</td>
                    <td className="ws-muted">{t.category}</td>
                    <NumCell>{fmt(-t.amount, t.iso_currency_code || "USD")}</NumCell>
                  </tr>
                ))}
              </tbody>
            </Table>
          </TableScroll>
          {hasMore && (
            <div style={{ marginTop: "var(--space-3)", display: "flex", justifyContent: "center" }}>
              <Button onClick={() => setPages((p) => p + 1)}>
                Load {Math.min(TXN_PAGE_SIZE, txns.length - visible.length)} more
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
