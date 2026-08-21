import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import "./budget/budget.css";
import { Check, TriangleAlert, Pencil, Trash2, Plus, RefreshCw } from "lucide-react";
import {
  Button, Field, Input, PageHeader, Select, Tabs, shortHttpError,
  type AppChatDockRenderer,
} from "@workshop/ui";
import Projection from "./budget/Projection";

// Budget app — the planning layer over the raw Finance viewer. Implements the
// operator's mental model as a priority waterfall: income flows down through
// necessities -> prioritized debt payoffs -> prioritized savings buckets
// (emergency fund first, as the contingency plan) -> variable spend targets ->
// leftover. Live balances/transactions come from the shared finance cache; the
// editable plan lives in budget.json on the backend.

// ---- types (mirror backend budget_engine.compute output) ----
type Debt = {
  id: string; name: string; priority: number; balance: number; apr: number | null;
  cadence: string; strategy: string; target_date: string | null; promo_until: string | null;
  planned_per_period: number; planned_monthly: number; monthly_equivalent: number;
  required_monthly: number | null; months_remaining: number | null;
  amount_source: string; on_pace: boolean | null;
  note: string; account_mask: string | null;
};
type Saving = {
  id: string; name: string; priority: number; term: string | null; target: number;
  current: number; target_date: string | null; planned_monthly: number;
  required_monthly: number | null; months_remaining: number | null; progress: number | null;
  on_pace: boolean | null; note: string; account_mask: string | null;
};
type Variable = { id: string; name: string; target: number; actual: number; over: boolean; pct: number | null };
type Necessity = { id: string; name: string; monthly: number; note?: string };
type Summary = {
  income: number; necessities_planned: number; debt_planned: number; savings_planned: number;
  savings_required: number; savings_gap: number; available_after_fixed: number; goals_affordable: boolean;
  variable_target: number; committed: number; discretionary: number; leftover: number;
  shortfall: boolean; per_paycheck: number; paychecks_per_month: number;
};
type WaterfallSeg = { key: string; label: string; amount: number };
export type Budget = {
  income: { monthly: number; per_paycheck: number; paychecks_per_month: number; source?: string; pay_days?: number[] };
  debts: Debt[]; savings: Saving[]; variable: Variable[]; necessities: Necessity[];
  actuals: { necessities: number; unclassified: number; window_days: number };
  summary: Summary; waterfall: WaterfallSeg[];
  cache?: { stale: boolean; age_s: number };
};

// HOISTED. Constructing an Intl.NumberFormat is expensive (it resolves a
// locale), and this is called ~25 times per render of this page. There are only
// two shapes, so there only need to be two formatters, built once.
const USD_0 = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const USD_2 = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2,
});

const usd = (n: number | null | undefined, cents = false) =>
  n == null ? "–" : (cents ? USD_2 : USD_0).format(n);

// Stable hues per waterfall segment, deliberately ordered necessities->leftover.
// Categorical suite (--cat-*) — differentiation, not status (these are CSS
// background values, so var() resolves fine). See frontend/CLAUDE.md.
const SEG_COLORS: Record<string, string> = {
  necessities: "var(--cat-slate)",
  debt: "var(--cat-orchid)",
  savings: "var(--cat-turquoise)",
  variable: "var(--cat-blue)",
  leftover: "var(--cat-violet)",
};

// ---- Inline edit primitives ----
// The shipping pattern was: see a number on screen, click "Edit plan", scroll
// to find the row in the full editor, edit, click Save, wait for reload. That
// is too many steps for "set Groceries to $600". These two components turn any
// displayed number/text into a click-to-edit field that PATCHes the single
// changed field and tells the parent to reload. Optimistic — the input renders
// the new value immediately on save; failure rolls back.
type PatchTarget =
  | { section: "income"; field: string }
  | { section: "necessities" | "debts" | "savings" | "variable"; id: string; field: string };

async function patchPlan(t: PatchTarget, value: number | string | null): Promise<void> {
  const body: any = { section: t.section, field: t.field, value };
  if ("id" in t) body.id = t.id;
  const r = await fetch("/api/budget/plan", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
}

function InlineNumber({
  value, target, onSaved, placeholder = "–", display, ariaLabel,
}: {
  value: number; target: PatchTarget; onSaved: () => void;
  placeholder?: string; display?: (n: number) => string; ariaLabel?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value || ""));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const start = () => {
    setDraft(value ? String(value) : "");
    setErr(null);
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };
  const cancel = () => { setEditing(false); setErr(null); };
  const commit = async () => {
    const next = draft.trim() === "" ? 0 : parseFloat(draft);
    if (Number.isNaN(next)) { setErr("not a number"); return; }
    if (next === value) { cancel(); return; }
    setSaving(true); setErr(null);
    try {
      await patchPlan(target, next);
      setEditing(false);
      onSaved();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    const txt = value || value === 0 ? (display ? display(value) : String(value)) : placeholder;
    return (
      <button className="inline-edit" onClick={start} aria-label={ariaLabel || "edit"} title="Click to edit">
        {txt}
      </button>
    );
  }
  return (
    <span className="inline-edit-wrap">
      <input
        ref={inputRef}
        className="inline-edit-input"
        type="number"
        step="any"
        value={draft}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          else if (e.key === "Escape") { e.preventDefault(); cancel(); }
        }}
      />
      {saving && <span className="inline-edit-dot" aria-hidden />}
      {err && <span className="inline-edit-err" title={err}>!</span>}
    </span>
  );
}

function InlineText({
  value, target, onSaved, placeholder = "–", ariaLabel, nullable = false,
}: {
  value: string | null; target: PatchTarget; onSaved: () => void;
  placeholder?: string; ariaLabel?: string; nullable?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const start = () => {
    setDraft(value ?? "");
    setErr(null);
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };
  const cancel = () => { setEditing(false); setErr(null); };
  const commit = async () => {
    const trimmed = draft.trim();
    const next = trimmed === "" && nullable ? null : trimmed;
    if (next === (value ?? "")) { cancel(); return; }
    if (next === value) { cancel(); return; }
    setSaving(true); setErr(null);
    try {
      await patchPlan(target, next);
      setEditing(false);
      onSaved();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button className="inline-edit" onClick={start} aria-label={ariaLabel || "edit"} title="Click to edit">
        {value || <span className="muted">{placeholder}</span>}
      </button>
    );
  }
  return (
    <span className="inline-edit-wrap">
      <input
        ref={inputRef}
        className="inline-edit-input"
        type="text"
        value={draft}
        disabled={saving}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          else if (e.key === "Escape") { e.preventDefault(); cancel(); }
        }}
      />
      {saving && <span className="inline-edit-dot" aria-hidden />}
      {err && <span className="inline-edit-err" title={err}>!</span>}
    </span>
  );
}

// Inline editor for a monthly-stored value that's rendered in either monthly
// or per-paycheck units. The displayed/edited number is `monthlyValue * scale`;
// on commit, the input is divided back by `scale` so persistence stays in the
// canonical monthly basis. For income specifically we patch a different field
// (`per_paycheck` vs `monthly`) so the operator's mental model — "I bumped my
// paycheck" — round-trips cleanly without scale-conversion losing precision.
function ScaledInlineNumber({
  monthlyValue, scale, paychecks, target, onSaved, format, ariaLabel, placeholder,
}: {
  monthlyValue: number; scale: number; paychecks: number;
  target: PatchTarget; onSaved: () => void;
  format: (n: number) => string; ariaLabel?: string; placeholder?: string;
}) {
  const displayed = monthlyValue * scale;
  // For income, when scale=1/paychecks (per-paycheck mode), we patch
  // per_paycheck directly with the edited value. Otherwise (monthly mode or
  // any non-income row), we divide the input by `scale` to get the monthly
  // equivalent and patch the monthly-storage field.
  const isPerPaycheckIncome = target.section === "income" && target.field === "per_paycheck";
  const adapter: PatchTarget = target;
  const transformOnCommit = (entered: number): number => {
    if (isPerPaycheckIncome) return entered;
    if (scale === 0) return entered;
    return entered / scale;
  };
  // The patch helper takes the post-transform value; wrap InlineNumber so it
  // sees the displayed value and we intercept the save call.
  return (
    <ScaledInner
      value={displayed}
      target={adapter}
      transform={transformOnCommit}
      onSaved={onSaved}
      display={format}
      ariaLabel={ariaLabel}
      placeholder={placeholder}
      // include paychecks in deps so the wrapper re-renders when toggling units
      key={`${isPerPaycheckIncome}-${paychecks}`}
    />
  );
}

function ScaledInner({
  value, target, transform, onSaved, display, ariaLabel, placeholder,
}: {
  value: number; target: PatchTarget; transform: (n: number) => number;
  onSaved: () => void; display: (n: number) => string;
  ariaLabel?: string; placeholder?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value || ""));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const start = () => {
    setDraft(value ? String(Math.round(value * 100) / 100) : "");
    setErr(null);
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };
  const cancel = () => { setEditing(false); setErr(null); };
  const commit = async () => {
    const entered = draft.trim() === "" ? 0 : parseFloat(draft);
    if (Number.isNaN(entered)) { setErr("not a number"); return; }
    const next = transform(entered);
    if (Math.abs(next - (target.section === "income" && target.field === "per_paycheck" ? value : value)) < 0.005 && next === value) {
      cancel(); return;
    }
    setSaving(true); setErr(null);
    try {
      await patchPlan(target, next);
      setEditing(false);
      onSaved();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button className="inline-edit" onClick={start} aria-label={ariaLabel || "edit"} title="Click to edit">
        {value || value === 0 ? display(value) : (placeholder || "–")}
      </button>
    );
  }
  return (
    <span className="inline-edit-wrap">
      <input
        ref={inputRef}
        className="inline-edit-input"
        type="number"
        step="any"
        value={draft}
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          else if (e.key === "Escape") { e.preventDefault(); cancel(); }
        }}
      />
      {saving && <span className="inline-edit-dot" aria-hidden />}
      {err && <span className="inline-edit-err" title={err}>!</span>}
    </span>
  );
}

export default function Budget({
  groupNav,
  renderChatDock,
}: {
  groupNav?: ReactNode;
  renderChatDock?: AppChatDockRenderer;
}) {
  const [tab, setTab] = useState<"plan" | "projection">("plan");
  const [data, setData] = useState<Budget | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [perPaycheck, setPerPaycheck] = useState(false);
  const [editing, setEditing] = useState(false);

  // Re-poll timer for stale-while-revalidate: when the backend served cached
  // data and kicked a background Plaid refresh, it flags cache.stale — we poll
  // again shortly to swap in the fresh numbers without a blocking spinner.
  const repoll = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async (opts?: { refresh?: boolean; quiet?: boolean }) => {
    if (!opts?.quiet) setRefreshing(true);
    setErr(null);
    try {
      // refresh=1 forces a blocking live (per-bank) pull; normal loads hit the
      // fast cached-balances path and render instantly.
      const r = await fetch(`/api/budget${opts?.refresh ? "?refresh=1" : ""}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = (await r.json()) as Budget;
      setData(d);
      if (repoll.current) { clearTimeout(repoll.current); repoll.current = null; }
      if (d.cache?.stale) {
        // Background refresh is in flight on the server — pick up fresh numbers.
        repoll.current = setTimeout(() => load({ quiet: true }), 4000);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      if (!opts?.quiet) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    return () => { if (repoll.current) clearTimeout(repoll.current); };
  }, [load]);

  const div = data?.summary.paychecks_per_month || 1;
  const scale = perPaycheck ? 1 / div : 1;
  const periodLabel = perPaycheck ? "per paycheck" : "per month";

  return (
    <div>
      <PageHeader
        title="Budget"
        sub={data?.cache?.stale && !refreshing ? "updating…" : undefined}
        actions={
          <>
            {groupNav}
            <Button size="sm" onClick={() => load({ refresh: true })} loading={refreshing} title="Pull fresh live balances from your banks">
              <RefreshCw aria-hidden /> Refresh
            </Button>
          </>
        }
      />

      <div style={{ marginBottom: "var(--space-5)" }}>
        <Tabs
          items={[
            { value: "plan", label: "Plan" },
            { value: "projection", label: "Projection" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {err && <p className="err" style={{ marginBottom: "1rem" }}>Backend error: {err}</p>}
      {!data && !err && <p className="muted">Loading budget…</p>}

      {data && tab === "plan" && (
        <>
          {/* Monthly / per-paycheck toggle + edit */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", gap: "0.5rem", flexWrap: "wrap" }}>
            <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
              <button onClick={() => setPerPaycheck(false)} style={segBtn(!perPaycheck)}>Monthly</button>
              <button onClick={() => setPerPaycheck(true)} style={segBtn(perPaycheck)}>Per paycheck</button>
            </div>
            <button onClick={() => setEditing((v) => !v)} style={{ fontSize: "0.85rem", padding: "0.3rem 0.7rem", display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
              <Pencil size={14} strokeWidth={1.8} /> {editing ? "Close editor" : "Edit plan"}
            </button>
          </div>

          <p className="muted" style={{ fontSize: "0.78rem", margin: "0 0 0.75rem", opacity: 0.85 }}>
            Click any underlined value to edit it inline: income, debts, savings goals, targets, names, notes. Use <em>Edit plan</em> for adding/removing rows or bulk changes.
          </p>
          <WaterfallHero data={data} scale={scale} periodLabel={periodLabel} onSaved={load} />
          <NecessitiesSection necessities={data.necessities} scale={scale} onSaved={load} />
          <DebtsSection debts={data.debts} scale={scale} onSaved={load} />
          <SavingsSection savings={data.savings} summary={data.summary} scale={scale} onSaved={load} />
          <SpendingSection variable={data.variable} necessitiesActual={data.actuals.necessities} windowDays={data.actuals.window_days} unclassified={data.actuals.unclassified} scale={scale} onSaved={load} />

          {editing && <EditForm onSaved={() => { setEditing(false); load(); }} onCancel={() => setEditing(false)} />}
        </>
      )}

      {data && tab === "projection" && <Projection budget={data} />}

      {/* In-app assistant, query the budget or tell it to change the plan.
          renderChatDock is supplied by the shell (apps/registry.tsx), gated by
          the app's manifest ui.agent_button setting. */}
      {renderChatDock?.({
        title: "Budget assistant",
        placeholder: "Ask about or change your budget…",
        onAction: () => load(),
      })}
    </div>
  );
}

const segBtn = (active: boolean): React.CSSProperties => ({
  background: active ? "var(--accent)" : "transparent",
  color: active ? "var(--accent-fg)" : "var(--muted)",
  border: "none", padding: "0.3rem 0.7rem", fontSize: "0.85rem", cursor: "pointer",
});

function Pace({ ok, behindLabel = "behind", okLabel = "on track" }: { ok: boolean | null; behindLabel?: string; okLabel?: string }) {
  if (ok == null) return <span className="pill neutral">no deadline</span>;
  return ok
    ? <span className="pill ok"><Check size={12} strokeWidth={2.5} /> {okLabel}</span>
    : <span className="pill behind"><TriangleAlert size={12} strokeWidth={2.5} /> {behindLabel}</span>;
}

// ---- Waterfall hero: income split into one stacked allocation strip ----
function WaterfallHero({ data, scale, periodLabel, onSaved }: { data: Budget; scale: number; periodLabel: string; onSaved: () => void }) {
  const s = data.summary;
  const income = s.income * scale;
  // Segment widths are based on positive allocations; leftover shown only if >=0.
  const segs = data.waterfall.filter((w) => w.key !== "leftover" || w.amount >= 0);
  const denom = Math.max(income, segs.reduce((a, w) => a + Math.max(w.amount * scale, 0), 0)) || 1;
  // Inline edit on income works in *monthly* terms regardless of per-paycheck
  // toggle. The displayed scaled value reads from `income`; the editor commits
  // to the underlying monthly field. Per-paycheck mode shows monthly /
  // paychecks_per_month — editing converts back on commit.
  const paychecks = data.summary.paychecks_per_month || 2;
  const incomeMonthly = data.summary.income;

  return (
    <section className="card" style={{ marginBottom: "1.25rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "0.5rem" }}>
        <div>
          <p className="sec-h" style={{ marginBottom: "0.2rem" }}>Income {periodLabel}</p>
          <p style={{ margin: 0, fontSize: "1.8rem", fontWeight: 700 }}>
            <ScaledInlineNumber
              monthlyValue={incomeMonthly}
              scale={scale}
              paychecks={paychecks}
              target={{ section: "income", field: scale === 1 ? "monthly" : "per_paycheck" }}
              onSaved={onSaved}
              ariaLabel="edit income"
              format={(n) => usd(n)}
            />
          </p>
        </div>
        <div style={{ textAlign: "right" }}>
          <p className="sec-h" style={{ marginBottom: "0.2rem" }}>Safe to spend</p>
          <p style={{ margin: 0, fontSize: "1.4rem", fontWeight: 600, color: s.discretionary >= 0 ? "var(--fg)" : "var(--err)" }}>
            {usd(s.discretionary * scale)}
          </p>
        </div>
      </div>

      <div className="seg" style={{ marginTop: "0.9rem" }}>
        {segs.map((w) => {
          const amt = Math.max(w.amount * scale, 0);
          const basis = (amt / denom) * 100;
          if (basis < 0.5) return null;
          return (
            <span key={w.key} style={{ flex: `0 0 ${basis}%`, background: SEG_COLORS[w.key] }} title={`${w.label}: ${usd(w.amount * scale)}`}>
              {/* Only label a segment wide enough to hold the amount on a phone
                  (~16% of ~358px); narrower slices are covered by the legend below. */}
              {basis > 16 ? usd(amt) : ""}
            </span>
          );
        })}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem 0.9rem", marginTop: "0.6rem" }}>
        {data.waterfall.map((w) => (
          <span key={w.key} style={{ fontSize: "0.75rem", color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: SEG_COLORS[w.key], display: "inline-block" }} />
            {w.label} {usd(w.amount * scale)}
          </span>
        ))}
      </div>
      {s.shortfall && (
        <p className="err" style={{ marginTop: "0.7rem", marginBottom: 0, fontSize: "0.85rem" }}>
          Planned allocations exceed income. Trim a category or lower a contribution.
        </p>
      )}
    </section>
  );
}

// ---- Necessities: fixed monthly obligations, all inline-editable ----
function NecessitiesSection({ necessities, scale, onSaved }: { necessities: Necessity[]; scale: number; onSaved: () => void }) {
  if (!necessities.length) return null;
  const paychecks = scale === 1 ? 2 : Math.round(1 / scale);
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h3 className="sec-h">Necessities</h3>
      <div className="card">
        <div style={{ display: "grid", gap: "0.55rem" }}>
          {necessities.map((n) => (
            <div key={n.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "0.92rem" }}>
                <InlineText
                  value={n.name}
                  target={{ section: "necessities", id: n.id, field: "name" }}
                  onSaved={onSaved}
                  ariaLabel={`rename ${n.name}`}
                />
              </span>
              <span style={{ fontSize: "0.95rem", fontWeight: 500 }}>
                <ScaledInlineNumber
                  monthlyValue={n.monthly}
                  scale={scale}
                  paychecks={paychecks}
                  target={{ section: "necessities", id: n.id, field: "monthly" }}
                  onSaved={onSaved}
                  format={(v) => usd(v)}
                  ariaLabel={`edit ${n.name} amount`}
                  placeholder="set amount"
                />
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const CADENCE_UNIT: Record<string, string> = {
  daily: "day", weekly: "week", biweekly: "two weeks",
  monthly: "month", quarterly: "quarter", yearly: "year",
};

function payoffSummary(d: Debt): string {
  const unit = CADENCE_UNIT[d.cadence] ?? "month";
  if (d.strategy === "pay_in_full") {
    if (d.amount_source === "live_balance") return `Pay in full each ${unit} · ${usd(d.planned_per_period, true)} live balance`;
    if (d.amount_source === "trailing_avg") return `Pay in full each ${unit} · ~${usd(d.planned_per_period, true)} trailing avg`;
    return `Pay in full each ${unit}`;
  }
  if (d.strategy === "payoff_by_date") {
    const amt = d.planned_per_period > 0 ? ` · ~${usd(d.planned_per_period, true)}/${unit}` : "";
    const left = d.months_remaining != null ? ` · ${Math.ceil(d.months_remaining)} mo left` : "";
    return `Pay off by ${d.target_date ?? "–"}${amt}${left}`;
  }
  // fixed_amount
  const amt = d.planned_per_period > 0 ? `${usd(d.planned_per_period)} per ${unit}` : "no payment set";
  const left = d.months_remaining != null ? ` · clears in ~${Math.ceil(d.months_remaining)} mo` : "";
  return `${amt}${left}`;
}

// ---- Payoffs: cadence + strategy-aware display ----
function DebtsSection({ debts, onSaved }: { debts: Debt[]; scale: number; onSaved: () => void }) {
  if (!debts.length) return null;
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h3 className="sec-h">Payoffs</h3>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        {debts.map((d) => (
          <div key={d.id} className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
              <div style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>
                  <InlineText
                    value={d.name}
                    target={{ section: "debts", id: d.id, field: "name" }}
                    onSaved={onSaved}
                    ariaLabel={`rename ${d.name}`}
                  />
                </span>
                {d.account_mask && <span className="muted" style={{ fontSize: "0.8rem" }}> ··{d.account_mask}</span>}
              </div>
              <span style={{ fontSize: "1.15rem", fontWeight: 600 }}>
                <InlineNumber
                  value={d.balance}
                  target={{ section: "debts", id: d.id, field: "balance" }}
                  onSaved={onSaved}
                  display={(v) => usd(v, true)}
                  ariaLabel={`edit ${d.name} balance`}
                />
              </span>
            </div>
            <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.82rem" }}>
              {payoffSummary(d)}
            </p>
            {d.apr != null && (
              <p className="muted" style={{ margin: "0.2rem 0 0", fontSize: "0.78rem" }}>
                {d.apr === 0 ? "0% promo" : (
                  <><InlineNumber value={d.apr} target={{ section: "debts", id: d.id, field: "apr" }} onSaved={onSaved} display={(v) => `${v}%`} ariaLabel="edit APR" /> APR</>
                )}
                {d.promo_until && ` until ${d.promo_until}`}
              </p>
            )}
            <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.78rem", opacity: 0.85 }}>
              <InlineText
                value={d.note || ""}
                target={{ section: "debts", id: d.id, field: "note" }}
                onSaved={onSaved}
                nullable
                placeholder="add note"
                ariaLabel={`edit ${d.name} note`}
              />
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---- Savings buckets: emergency (contingency) first ----
function SavingsSection({ savings, summary, scale, onSaved }: { savings: Saving[]; summary: Summary; scale: number; onSaved: () => void }) {
  if (!savings.length) return null;
  const paychecks = scale === 1 ? 2 : Math.round(1 / scale);
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h3 className="sec-h">Savings & goals</h3>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        {savings.map((s) => (
          <div key={s.id} className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
              <div style={{ minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>
                  <InlineText
                    value={s.name}
                    target={{ section: "savings", id: s.id, field: "name" }}
                    onSaved={onSaved}
                    ariaLabel={`rename ${s.name}`}
                  />
                </span>
                {s.term === "contingency" && <span className="pill neutral" style={{ marginLeft: "0.4rem" }}>contingency</span>}
              </div>
              <span style={{ fontSize: "0.95rem" }}>
                <InlineNumber
                  value={s.current}
                  target={{ section: "savings", id: s.id, field: "current" }}
                  onSaved={onSaved}
                  display={(v) => usd(v)}
                  ariaLabel={`edit ${s.name} current balance`}
                  placeholder="$0"
                />
                {" / "}
                <InlineNumber
                  value={s.target}
                  target={{ section: "savings", id: s.id, field: "target" }}
                  onSaved={onSaved}
                  display={(v) => usd(v)}
                  ariaLabel={`edit ${s.name} goal`}
                  placeholder="set goal"
                />
              </span>
            </div>

            {s.target > 0 && (
              <div className="bar" style={{ marginTop: "0.6rem", ["--pct" as string]: s.progress ?? 0, ["--c" as string]: "var(--ok-solid)" }}>
                <i />
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.45rem", gap: "0.5rem", flexWrap: "wrap" }}>
              <span className="muted" style={{ fontSize: "0.8rem" }}>
                Saving{" "}
                <ScaledInlineNumber
                  monthlyValue={s.planned_monthly}
                  scale={scale}
                  paychecks={paychecks}
                  target={{ section: "savings", id: s.id, field: "planned_monthly" }}
                  onSaved={onSaved}
                  format={(v) => usd(v)}
                  ariaLabel={`edit ${s.name} contribution`}
                  placeholder="set contribution"
                />
                {s.required_monthly != null && ` of ${usd(s.required_monthly * scale)} needed`}
                {" · by "}
                <InlineText
                  value={s.target_date}
                  target={{ section: "savings", id: s.id, field: "target_date" }}
                  onSaved={onSaved}
                  nullable
                  placeholder="YYYY-MM-DD"
                  ariaLabel={`edit ${s.name} deadline`}
                />
              </span>
              {(s.target > 0 || s.required_monthly != null) && (
                <Pace ok={s.on_pace} behindLabel={s.required_monthly ? `+${usd((s.required_monthly - s.planned_monthly) * scale)}/mo` : "behind"} />
              )}
            </div>
            {/* Note: inline-editable. Renders as a placeholder + pencil hint if
                empty so the operator can see it exists. */}
            <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.78rem", opacity: 0.85 }}>
              <InlineText
                value={s.note || ""}
                target={{ section: "savings", id: s.id, field: "note" }}
                onSaved={onSaved}
                nullable
                placeholder="add note"
                ariaLabel={`edit ${s.name} note`}
              />
            </p>
          </div>
        ))}
      </div>

      {/* Aggregate goal-funding rollup: total monthly needed across every dated
          goal vs what's planned, and whether income can actually cover it. */}
      {(summary.savings_required > 0 || summary.savings_planned > 0) && (
        <div className="card" style={{ marginTop: "0.75rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600 }}>All goals combined</span>
            <span style={{ fontSize: "0.95rem" }}>
              Saving {usd(summary.savings_planned * scale)} of {usd(summary.savings_required * scale)} needed
            </span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.45rem", gap: "0.5rem", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              {usd(summary.available_after_fixed * scale)} free after necessities + debt. {summary.goals_affordable
                ? "enough to cover required savings + your spending targets"
                : "NOT enough to fund all goals on time AND hit spending targets; stretch a date or trim a target"}
            </span>
            {summary.savings_gap > 0
              ? <Pace ok={false} behindLabel={`+${usd(summary.savings_gap * scale)}/mo to fully fund`} />
              : <Pace ok={true} behindLabel="" />}
          </div>
        </div>
      )}
    </section>
  );
}

// ---- Variable spend vs targets (actuals from transactions) ----
function SpendingSection({ variable, necessitiesActual, windowDays, unclassified, scale, onSaved }: { variable: Variable[]; necessitiesActual: number; windowDays: number; unclassified: number; scale: number; onSaved: () => void }) {
  const paychecks = scale === 1 ? 2 : Math.round(1 / scale);
  return (
    <section style={{ marginBottom: "1.5rem" }}>
      <h3 className="sec-h">Spending vs target · last {windowDays}d</h3>
      <div className="card">
        <div style={{ display: "grid", gap: "0.85rem" }}>
          {variable.map((v) => {
            const pct = v.target > 0 ? Math.min(v.actual / v.target, 1) : 0;
            return (
              <div key={v.id}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", marginBottom: "0.3rem", gap: "0.5rem", flexWrap: "wrap" }}>
                  <span>
                    <InlineText
                      value={v.name}
                      target={{ section: "variable", id: v.id, field: "name" }}
                      onSaved={onSaved}
                      ariaLabel={`rename ${v.name}`}
                    />
                  </span>
                  <span className={v.over ? "" : "muted"} style={{ color: v.over ? "var(--err)" : undefined }}>
                    {usd(v.actual, true)}{" / "}
                    <ScaledInlineNumber
                      monthlyValue={v.target}
                      scale={scale}
                      paychecks={paychecks}
                      target={{ section: "variable", id: v.id, field: "target" }}
                      onSaved={onSaved}
                      format={(n) => usd(n)}
                      ariaLabel={`edit ${v.name} target`}
                      placeholder="set target"
                    />
                  </span>
                </div>
                <div className="bar" style={{ ["--pct" as string]: pct, ["--c" as string]: v.over ? "var(--danger-solid)" : "var(--cat-blue)" }}>
                  <i />
                </div>
              </div>
            );
          })}
        </div>
        <p className="muted" style={{ margin: "0.85rem 0 0", fontSize: "0.78rem" }}>
          Necessities (rent/utilities) detected: {usd(necessitiesActual, true)}
          {unclassified > 0 && ` · other/unclassified: ${usd(unclassified, true)}`}
          . Transfers and card payments excluded. Click any value to edit.
        </p>
      </div>
    </section>
  );
}

// ---- Edit form field primitives (module scope so they don't remount on every
// keystroke — defining them inside the render made inputs lose focus mid-type) ----
const newId = () =>
  typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : `id-${Math.floor(Math.random() * 1e8)}`;

// These wrap the design-system Field/Input/Select rather than the hand-rolled
// `.ef-field` + `.num-in` pair they used to. That pair was a parallel
// implementation of the same controls — its own border, hover, focus ring and
// select caret — so it drifted from every other field in the app and had to be
// fixed separately whenever the system changed.
function EfNum({ label, value, onChange, prefix = "$" }: { label: string; value: number; onChange: (n: number) => void; prefix?: string }) {
  return (
    <Field label={prefix ? `${label} (${prefix})` : label}>
      <Input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(e.target.value === "" ? 0 : parseFloat(e.target.value))}
      />
    </Field>
  );
}
function EfText({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (s: string) => void; placeholder?: string }) {
  return (
    <Field label={label}>
      <Input type="text" value={value || ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}
function EfSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (s: string) => void; options: [string, string][] }) {
  return (
    <Field label={label}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </Select>
    </Field>
  );
}
function DelBtn({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} title="Remove" aria-label="Remove">
      <Trash2 size={15} strokeWidth={1.8} />
    </Button>
  );
}

// ---- Full plan editor: add / rename / delete / set targets & timelines on
// every section. Whole plan is PUT back; the engine recomputes over whatever
// rows exist, so nothing here is fixed. ----
function EditForm({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const [plan, setPlan] = useState<any | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/budget/plan").then((r) => r.json()).then(setPlan).catch((e) => setErr(String(e)));
  }, []);

  const mutate = (fn: (p: any) => void) =>
    setPlan((prev: any) => { const next = structuredClone(prev); fn(next); return next; });
  const nextPriority = (arr: any[]) => arr.reduce((m, x) => Math.max(m, x.priority || 0), 0) + 1;

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const r = await fetch("/api/budget/plan", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(plan) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onSaved();
    } catch (e) { setErr(String(e)); } finally { setSaving(false); }
  };

  if (err && !plan) return <p className="err">{err}</p>;
  if (!plan) return <p className="muted">Loading editor…</p>;

  return (
    <section className="card" style={{ marginTop: "1rem" }}>
      <h3 className="sec-h">Edit plan</h3>

      {/* Income */}
      <p className="sec-h" style={{ marginTop: "0.6rem" }}>Income</p>
      <div className="ef-row">
        <div className="ef-grid">
          <EfNum label="Per paycheck" value={plan.income.per_paycheck} onChange={(n) => mutate((p) => { p.income.per_paycheck = n; p.income.monthly = n * (p.income.paychecks_per_month || 2); })} />
          <EfNum label="Paychecks / month" prefix="#" value={plan.income.paychecks_per_month} onChange={(n) => mutate((p) => { p.income.paychecks_per_month = n; p.income.monthly = (p.income.per_paycheck || 0) * n; })} />
          <EfNum label="= monthly income" value={plan.income.monthly} onChange={(n) => mutate((p) => { p.income.monthly = n; })} />
        </div>
      </div>

      {/* Necessities */}
      <p className="sec-h" style={{ marginTop: "1rem" }}>Necessities (fixed monthly)</p>
      {plan.necessities.map((n: any, i: number) => (
        <div className="ef-row" key={n.id}>
          <div className="ef-grid">
            <EfText label="Name" value={n.name} onChange={(v) => mutate((p) => { p.necessities[i].name = v; })} />
            <EfNum label="Monthly" value={n.monthly} onChange={(v) => mutate((p) => { p.necessities[i].monthly = v; })} />
          </div>
          <DelBtn onClick={() => mutate((p) => p.necessities.splice(i, 1))} />
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={() => mutate((p) => p.necessities.push({ id: newId(), name: "", monthly: 0, note: "" }))}><Plus size={14} strokeWidth={2} /> Add necessity</Button>

      {/* Payoffs */}
      <p className="sec-h" style={{ marginTop: "1.2rem" }}>Payoffs</p>
      {plan.debts.map((d: any, i: number) => (
        <div className="ef-row" key={d.id}>
          <div className="ef-grid">
            <EfText label="Name" value={d.name} onChange={(v) => mutate((p) => { p.debts[i].name = v; })} />
            <EfSelect label="Cadence" value={d.cadence || "monthly"} options={[["daily","Daily"],["weekly","Weekly"],["biweekly","Biweekly"],["monthly","Monthly"],["quarterly","Quarterly"],["yearly","Yearly"]]} onChange={(v) => mutate((p) => { p.debts[i].cadence = v; })} />
            <EfSelect label="Strategy" value={d.strategy || "fixed_amount"} options={[["fixed_amount","Fixed amount"],["pay_in_full","Pay in full"],["payoff_by_date","Pay off by date"]]} onChange={(v) => mutate((p) => { p.debts[i].strategy = v; })} />
            {d.strategy === "fixed_amount" && (
              <EfNum label="Amount / period" value={d.planned_per_period ?? d.planned_monthly ?? 0} onChange={(v) => mutate((p) => { p.debts[i].planned_per_period = v; p.debts[i].planned_monthly = v; })} />
            )}
            {d.strategy === "payoff_by_date" && (
              <EfText label="Target date" placeholder="YYYY-MM-DD" value={d.target_date || d.promo_until || ""} onChange={(v) => mutate((p) => { p.debts[i].target_date = v || null; p.debts[i].promo_until = v || null; })} />
            )}
            <EfNum label="Balance" value={d.balance} onChange={(v) => mutate((p) => { p.debts[i].balance = v; })} />
            <EfNum label="APR %" prefix="" value={d.apr ?? 0} onChange={(v) => mutate((p) => { p.debts[i].apr = v; })} />
            <EfNum label="Priority" prefix="#" value={d.priority} onChange={(v) => mutate((p) => { p.debts[i].priority = v; })} />
          </div>
          <DelBtn onClick={() => mutate((p) => p.debts.splice(i, 1))} />
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={() => mutate((p) => p.debts.push({ id: newId(), name: "", priority: nextPriority(p.debts), balance: 0, account_mask: null, apr: 0, promo_until: null, cadence: "monthly", strategy: "fixed_amount", target_date: null, planned_per_period: 0, planned_monthly: 0, note: "" }))}><Plus size={14} strokeWidth={2} /> Add payoff</Button>

      {/* Savings & goals */}
      <p className="sec-h" style={{ marginTop: "1.2rem" }}>Savings & goals</p>
      {plan.savings.map((s: any, i: number) => (
        <div className="ef-row" key={s.id}>
          <div className="ef-grid">
            <EfText label="Name" value={s.name} onChange={(v) => mutate((p) => { p.savings[i].name = v; })} />
            <EfSelect label="Horizon" value={s.term || "short"} options={[["contingency", "Contingency"], ["short", "Short-term"], ["med", "Mid-term"], ["long", "Long / invest"]]} onChange={(v) => mutate((p) => { p.savings[i].term = v; })} />
            <EfNum label="Target" value={s.target} onChange={(v) => mutate((p) => { p.savings[i].target = v; })} />
            <EfNum label="Current" value={s.current} onChange={(v) => mutate((p) => { p.savings[i].current = v; })} />
            <EfNum label="Saving / month" value={s.planned_monthly} onChange={(v) => mutate((p) => { p.savings[i].planned_monthly = v; })} />
            <EfText label="By date" placeholder="YYYY-MM-DD" value={s.target_date} onChange={(v) => mutate((p) => { p.savings[i].target_date = v || null; })} />
            <EfNum label="Priority" prefix="#" value={s.priority} onChange={(v) => mutate((p) => { p.savings[i].priority = v; })} />
          </div>
          <DelBtn onClick={() => mutate((p) => p.savings.splice(i, 1))} />
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={() => mutate((p) => p.savings.push({ id: newId(), name: "", priority: nextPriority(p.savings), term: "short", target: 0, current: 0, account_mask: null, target_date: null, planned_monthly: 0, note: "" }))}><Plus size={14} strokeWidth={2} /> Add goal</Button>

      {/* Variable categories */}
      <p className="sec-h" style={{ marginTop: "1.2rem" }}>Spending categories (monthly target)</p>
      {plan.variable.map((v: any, i: number) => (
        <div className="ef-row" key={v.id}>
          <div className="ef-grid">
            <EfText label="Name" value={v.name} onChange={(val) => mutate((p) => { p.variable[i].name = val; })} />
            <EfNum label="Monthly target" value={v.target} onChange={(val) => mutate((p) => { p.variable[i].target = val; })} />
            <EfText label="Plaid match (advanced, comma-sep)" value={(v.match || []).join(", ")} onChange={(val) => mutate((p) => { p.variable[i].match = val.split(",").map((s: string) => s.trim().toUpperCase().replace(/\s+/g, "_")).filter(Boolean); })} />
          </div>
          <DelBtn onClick={() => mutate((p) => p.variable.splice(i, 1))} />
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={() => mutate((p) => p.variable.push({ id: newId(), name: "", target: 0, match: [] }))}><Plus size={14} strokeWidth={2} /> Add category</Button>

      <div className="form-actions-sticky" style={{ marginTop: "1.2rem" }}>
        <button className="primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save plan"}</button>
        <button onClick={onCancel} disabled={saving}>Cancel</button>
        {err && <span className="err">{err}</span>}
      </div>
    </section>
  );
}
