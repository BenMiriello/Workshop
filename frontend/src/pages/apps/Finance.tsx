import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Skeleton,
  Stat,
  StateBlock,
  StatusPill,
  Table,
  TableScroll,
  TimeCell,
} from "../../ui";

// Finance app — shows Plaid-linked accounts and recent transactions.
// Data layer: backend cache (finance-cache.json, 10min TTL) + localStorage
// backup so the page never goes blank when the backend or Plaid is down.

const LS_BAL = "ws_finance_bal";
const LS_TXN = "ws_finance_txn";

function lsGet<T>(key: string): { data: T; ts: number } | null {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}
function lsSet<T>(key: string, data: T) {
  try {
    localStorage.setItem(key, JSON.stringify({ data, ts: Date.now() }));
  } catch {}
}

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

type CacheMeta = { cached: boolean; stale?: boolean; ts: number; age_s: number; ttl_s: number };
type BalancesResp = { items: Item[]; _cache?: CacheMeta };
type TransactionsResp = { transactions: Transaction[]; _cache?: CacheMeta };

const fmt = (n: number | null, ccy: string | null) => {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: ccy || "USD" }).format(n);
};

const fmtAge = (ms: number) => {
  const sec = ms / 1000;
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
};

export default function Finance() {
  // What to display — starts with localStorage, updated by live API
  const [items, setItems] = useState<Item[] | null>(null);
  const [txns, setTxns] = useState<Transaction[] | null>(null);
  // Backend cache metadata (freshness indicator when serving live data)
  const [balCache, setBalCache] = useState<CacheMeta | null>(null);
  const [, setTxnCache] = useState<CacheMeta | null>(null);
  // When the localStorage snapshot was saved (null = no local backup yet)
  const [localBalTs, setLocalBalTs] = useState<number | null>(null);
  // True while displaying localStorage data before a successful live fetch
  const [isLocal, setIsLocal] = useState(false);
  // Network/HTTP error (not per-item Plaid errors)
  const [fetchErr, setFetchErr] = useState<string | null>(null);
  // Per-institution errors when some (but not all) banks fail
  const [itemErrors, setItemErrors] = useState<{ name: string; error: string }[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadBalances = useCallback(async (force = false) => {
    try {
      const r = await fetch(`/api/finance/balances${force ? "?refresh=1" : ""}`);
      if (!r.ok) throw new Error(`HTTP ${r.status} from /api/finance/balances`);
      const b = (await r.json()) as BalancesResp;

      const goodItems = (b.items ?? []).filter((i) => (i.accounts?.length ?? 0) > 0);
      const failedItems = (b.items ?? []).filter((i) => i.error);

      setBalCache(b._cache ?? null);
      setItemErrors(
        failedItems.map((i) => ({ name: i.institution_name ?? i.item_id, error: i.error! })),
      );

      if (goodItems.length > 0) {
        // We have real data — show it, clear any prior error, save to localStorage
        setItems(b.items);
        setIsLocal(false);
        setFetchErr(null);
        lsSet(LS_BAL, b.items);
        setLocalBalTs(Date.now());
      } else {
        // All institutions failed — surface as error, keep whatever is displayed
        // (may be localStorage data pre-loaded below)
        setFetchErr(
          failedItems.length > 0
            ? `Plaid returned errors for all institutions (${failedItems.map((i) => i.institution_name).join(", ")})`
            : "No accounts returned from Plaid",
        );
      }

      // Backend cache is stale: a background refresh is in progress. Poll
      // again in 5s to pick up the fresh numbers.
      if (b._cache?.stale && !force) {
        if (staleTimer.current) clearTimeout(staleTimer.current);
        staleTimer.current = setTimeout(() => loadBalances(false), 5000);
      }
    } catch (e) {
      // Network/HTTP failure — show error but keep displaying whatever we have
      setFetchErr(String(e));
    }
  }, []);

  const loadTxns = useCallback(async (force = false) => {
    try {
      const r = await fetch(`/api/finance/transactions?days=30${force ? "&refresh=1" : ""}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const t = (await r.json()) as TransactionsResp;
      setTxns(t.transactions ?? []);
      setTxnCache(t._cache ?? null);
      lsSet(LS_TXN, t.transactions ?? []);
    } catch {
      // Transaction load failures are non-fatal; balance errors surface separately
    }
  }, []);

  useEffect(() => {
    // Pre-load from localStorage immediately so the page is never blank
    const localBal = lsGet<Item[]>(LS_BAL);
    const localTxn = lsGet<Transaction[]>(LS_TXN);
    if (localBal) {
      setItems(localBal.data);
      setLocalBalTs(localBal.ts);
      setIsLocal(true);
    }
    if (localTxn) {
      setTxns(localTxn.data);
    }
    // Then fetch live data
    loadBalances();
    loadTxns();
    return () => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
  }, [loadBalances, loadTxns]);

  const doRefresh = useCallback(
    async (force = false) => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
      setRefreshing(true);
      setFetchErr(null);
      setItemErrors([]);
      try {
        await Promise.all([loadBalances(force), loadTxns(force)]);
      } finally {
        setRefreshing(false);
      }
    },
    [loadBalances, loadTxns],
  );

  const accounts = useMemo(
    () =>
      items?.flatMap((i) => i.accounts.map((a) => ({ ...a, institution: i.institution_name }))) ??
      [],
    [items],
  );

  const summary = useMemo(() => {
    if (!items) return null;
    const all = items.flatMap((i) => i.accounts);
    if (all.length === 0) return null;
    const liquid = all
      .filter((a) => a.type === "depository")
      .reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const invest = all
      .filter((a) => a.type === "investment" || a.type === "brokerage")
      .reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const creditAvail = all
      .filter((a) => a.type === "credit")
      .reduce((s, a) => s + (a.balance.available ?? 0), 0);
    const creditUsed = all
      .filter((a) => a.type === "credit")
      .reduce((s, a) => s + (a.balance.current ?? 0), 0);
    const netWorth = liquid + invest - creditUsed;
    return { liquid, invest, creditAvail, creditUsed, netWorth, accountCount: all.length };
  }, [items]);

  const isLoading = items === null && txns === null;
  const isStale = balCache?.stale ?? false;

  // Data age label for the header subtitle
  const ageLabel = (() => {
    if (refreshing) return "refreshing…";
    if (isLoading) return "loading…";
    if (isLocal && !balCache) {
      // Showing localStorage data, no live fetch succeeded yet
      return localBalTs ? `saved ${fmtAge(Date.now() - localBalTs)}` : "saved data";
    }
    if (balCache) {
      const label = `updated ${fmtAge(balCache.age_s * 1000)}`;
      return label;
    }
    return "fresh";
  })();

  return (
    <div>
      <PageHeader
        title="Finance"
        sub={
          <span>
            {ageLabel}
            {isStale && !refreshing && (
              <>
                {" "}
                <StatusPill status="running">syncing</StatusPill>
              </>
            )}
            {isLocal && !refreshing && (
              <>
                {" "}
                <StatusPill status="warn">offline</StatusPill>
              </>
            )}
          </span>
        }
        actions={
          <>
            <AppGroupNav />
            <Button size="sm" onClick={() => doRefresh(true)} loading={refreshing}>
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      {/* Network / HTTP error — show alongside any data we have, not instead of it */}
      {fetchErr && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock
            variant="error"
            title="Couldn't load live data"
            hint={
              <>
                {fetchErr}
                {isLocal && (
                  <>
                    {" "}
                    — showing last saved data
                    {localBalTs ? ` from ${fmtAge(Date.now() - localBalTs)}` : ""}
                  </>
                )}
              </>
            }
            action={
              <Button size="sm" onClick={() => doRefresh(true)} loading={refreshing}>
                Retry
              </Button>
            }
          />
        </div>
      )}

      {/* Per-institution errors (partial failure — some banks work, others don't) */}
      {itemErrors.length > 0 && !fetchErr && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <StateBlock
            variant="error"
            title={`${itemErrors.length} institution${itemErrors.length > 1 ? "s" : ""} unavailable`}
            hint={itemErrors.map((ie) => `${ie.name}: ${ie.error}`).join(" · ")}
          />
        </div>
      )}

      {/* Loading skeleton — only when nothing at all is available */}
      {isLoading && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <Skeleton style={{ height: "80px", marginBottom: "var(--space-4)" }} />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "var(--space-3)" }}>
            {[1, 2, 3, 4].map((n) => (
              <Skeleton key={n} style={{ height: "60px" }} />
            ))}
          </div>
        </div>
      )}

      {/* Summary KPI tiles */}
      {summary && (
        <div style={{ marginBottom: "var(--space-5)" }}>
          <Card style={{ marginBottom: "var(--space-4)" }}>
            <SectionLabel>Net worth</SectionLabel>
            <div className="ws-stat__num">{fmt(summary.netWorth, "USD")}</div>
            <div className="ws-stat__sub">cash + investments − credit owed</div>
          </Card>
          <div
            className="ws-card-grid"
            style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}
          >
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

      {/* Accounts grid */}
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
                  <p
                    className="ws-num"
                    style={{ fontSize: "var(--fs-lg)", margin: "var(--space-2) 0 0" }}
                  >
                    {fmt(a.balance.current, a.balance.iso_currency_code)}
                  </p>
                  {a.balance.available != null &&
                    a.balance.available !== a.balance.current && (
                      <p className="ws-card__meta">
                        Available: {fmt(a.balance.available, a.balance.iso_currency_code)}
                      </p>
                    )}
                </Card>
              </li>
            ))}
          </CardGrid>
        </section>
      )}

      {/* Transactions */}
      {txns === null ? (
        items !== null ? (
          <p className="ws-muted">Loading transactions…</p>
        ) : null
      ) : (
        <TransactionsTable txns={txns} />
      )}

      <ChatDock
        contextKey="finance"
        title="Accounts assistant"
        placeholder="Ask about balances or recent spend…"
      />
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
        <StateBlock
          title="No transactions"
          hint="No spending in the last 30 days, or Plaid hasn't synced yet."
        />
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
