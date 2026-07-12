import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceArea, ReferenceLine, ResponsiveContainer,
} from "recharts";
import type { Budget } from "../Budget";
import { CHART_SERIES, ChartFrame, ChartTooltip } from "../../../ui";

// Chart series colors — the categorical suite (brand violet first), never the
// status hues, so a trending line is never misread as good/bad. See ui/chart.
const SERIES = {
  netWorth: CHART_SERIES[0], // violet (brand)
  cash: CHART_SERIES[1], // turquoise
  debt: CHART_SERIES[2], // orchid
  invest: CHART_SERIES[3], // blue
} as const;

// Projection simulator. Projects the operator's financial trajectory forward,
// month by month, and lets them interactively reshape the future:
//   - timescale (1–30 yr)
//   - scenario: steady / income growth / employment gap / custom
//   - a pessimistic↔optimistic market-return gradient (drawn as an uncertainty
//     cone around projected net worth)
//   - a save-rate multiplier
// All math is client-side and pure, so every slider move recomputes instantly.

// ---- model ----
type DebtSim = { bal: number; payment: number };
type Params = {
  months: number;
  baseIncome: number;     // monthly, today
  incomeGrowth: number;   // annual fraction (e.g. 0.03)
  gapStart: number;       // month index; -1 = no gap
  gapLength: number;      // months
  saveMult: number;       // multiplier on planned contributions
  burn: number;           // monthly necessities + variable spend
  startCash: number;
  startInvest: number;
  cashContrib: number;    // monthly planned into cash savings buckets
  investContrib: number;  // monthly planned into the invest bucket
  debts: DebtSim[];
};

type Point = {
  t: number; cash: number; invest: number; debt: number; netWorth: number;
};

// Run the deterministic simulation once at a given annual investment return.
// Returns one Point per month (t = 0 is today). Net worth = cash + invest − debt.
function runOne(p: Params, annualReturn: number): Point[] {
  let cash = p.startCash;
  let invest = p.startInvest;
  const debts = p.debts.map((d) => ({ ...d }));
  const rM = Math.pow(1 + annualReturn, 1 / 12) - 1;
  const pts: Point[] = [];

  for (let t = 0; t <= p.months; t++) {
    const totalDebt = debts.reduce((a, d) => a + Math.max(d.bal, 0), 0);
    pts.push({ t, cash, invest, debt: totalDebt, netWorth: cash + invest - totalDebt });

    // advance one month
    invest *= 1 + rM;
    const inGap = p.gapStart >= 0 && t >= p.gapStart && t < p.gapStart + p.gapLength;
    const income = inGap ? 0 : p.baseIncome * Math.pow(1 + p.incomeGrowth, t / 12);

    let paidDebt = 0;
    if (!inGap) {
      for (const d of debts) {
        if (d.bal > 0) { const pay = Math.min(d.payment, d.bal); d.bal -= pay; paidDebt += pay; }
      }
      const cc = p.cashContrib * p.saveMult;
      const ic = p.investContrib * p.saveMult;
      cash += cc;
      invest += ic;
      cash += income - p.burn - paidDebt - cc - ic; // leftover (may be negative)
    } else {
      cash -= p.burn; // no income — living costs drain the cushion (runway)
    }
  }
  return pts;
}

const RETURN_BAND = 0.04; // ± around the central return → the uncertainty cone

type Row = {
  t: number; date: string; year: number;
  netWorth: number; cash: number; invest: number; debt: number;
  range: [number, number];
};

function simulate(p: Params, annualReturn: number, startDate: Date) {
  const mid = runOne(p, annualReturn);
  const low = runOne(p, annualReturn - RETURN_BAND);
  const high = runOne(p, annualReturn + RETURN_BAND);
  const rows: Row[] = mid.map((m, i) => {
    const d = new Date(startDate.getFullYear(), startDate.getMonth() + m.t, 1);
    return {
      t: m.t,
      date: d.toLocaleDateString("en-US", { month: "short", year: "2-digit" }),
      year: d.getFullYear(),
      netWorth: Math.round(m.netWorth),
      cash: Math.round(m.cash),
      invest: Math.round(m.invest),
      debt: Math.round(m.debt),
      range: [Math.round(low[i].netWorth), Math.round(high[i].netWorth)],
    };
  });
  return rows;
}

const usd0 = (n: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
const compact = (n: number) => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const pct = (f: number) => `${(f * 100).toFixed(1)}%`;

const TIMESCALES = [
  { label: "1y", months: 12 }, { label: "3y", months: 36 }, { label: "5y", months: 60 },
  { label: "10y", months: 120 }, { label: "30y", months: 360 },
];

type Scenario = "steady" | "growth" | "gap" | "custom";

export default function Projection({ budget }: { budget: Budget }) {
  const startDate = useMemo(() => new Date(), []);
  const [months, setMonths] = useState(60);
  const [scenario, setScenario] = useState<Scenario>("steady");

  // sliders
  const [retSlider, setRetSlider] = useState(50);       // 0..100 → annual return
  const [incomeGrowth, setIncomeGrowth] = useState(0.03);
  const [gapStart, setGapStart] = useState(6);
  const [gapLength, setGapLength] = useState(0);
  const [saveMult, setSaveMult] = useState(1);

  // series visibility
  const [show, setShow] = useState({ cash: true, invest: true, debt: true });

  // Starting investments — sum of Plaid investment/brokerage accounts (Fidelity).
  const [startInvest, setStartInvest] = useState<number>(0);
  useEffect(() => {
    fetch("/api/finance/balances").then((r) => r.json()).then((b) => {
      const items = b.items || [];
      let inv = 0;
      for (const it of items) for (const a of it.accounts || []) {
        if (a.type === "investment" || a.type === "brokerage") inv += a.balance?.current || 0;
      }
      setStartInvest(Math.round(inv));
    }).catch(() => {});
  }, []);

  // Map the 0..100 gradient slider to an annual return: 0%→-3%, 50%→5%, 100%→11%.
  const annualReturn = useMemo(() => -0.03 + (retSlider / 100) * 0.14, [retSlider]);

  // Plan-derived flows. Cash buckets = non-invest savings; invest bucket = term "long".
  const params: Params = useMemo(() => {
    const s = budget.summary;
    const cashBuckets = budget.savings.filter((x) => x.term !== "long");
    const investBucket = budget.savings.filter((x) => x.term === "long");
    const startCash = cashBuckets.reduce((a, x) => a + (x.current || 0), 0);
    const cashContrib = cashBuckets.reduce((a, x) => a + (x.planned_monthly || 0), 0);
    const investContrib = investBucket.reduce((a, x) => a + (x.planned_monthly || 0), 0);
    // Burn = planned spend, but fall back to *detected* spend per category when
    // no target is set yet (the seed plan has $0 targets). Otherwise the model
    // would assume all unbudgeted income is saved and project a fantasy curve.
    const variableActual = budget.variable.reduce((a, v) => a + (v.actual || 0), 0);
    const necessitiesEff = s.necessities_planned > 0 ? s.necessities_planned : budget.actuals.necessities;
    const variableEff = s.variable_target > 0 ? s.variable_target : variableActual;
    const burn = necessitiesEff + variableEff;
    return {
      months, baseIncome: s.income, incomeGrowth,
      gapStart: scenario === "gap" || (scenario === "custom" && gapLength > 0) ? gapStart : -1,
      gapLength: scenario === "gap" || scenario === "custom" ? gapLength : 0,
      saveMult, burn, startCash, startInvest, cashContrib, investContrib,
      debts: budget.debts.map((d) => ({ bal: d.balance, payment: d.monthly_equivalent })),
    };
  }, [budget, months, incomeGrowth, gapStart, gapLength, saveMult, scenario, startInvest]);

  const rows = useMemo(() => simulate(params, annualReturn, startDate), [params, annualReturn, startDate]);

  // Derived headline stats.
  const stats = useMemo(() => {
    const end = rows[rows.length - 1];
    const citiDebt = budget.debts.find((d) => d.id === "citi");
    const citiIdx = (() => {
      // re-run citi alone for clear-month (debt series is the sum; fine to use total hitting <= other debts)
      let bal = citiDebt?.balance ?? 0; const pay = citiDebt?.monthly_equivalent ?? 0;
      if (pay <= 0) return null;
      for (let m = 0; m <= params.months; m++) { if (bal <= 0) return m; bal -= pay; }
      return null;
    })();
    const emergencyTarget = budget.savings.find((s) => s.term === "contingency")?.target ?? 0;
    const emergIdx = emergencyTarget > 0 ? rows.findIndex((r) => r.cash >= emergencyTarget) : -1;
    const gapActive = params.gapStart >= 0 && params.gapLength > 0;
    const gapMinCash = gapActive
      ? Math.min(...rows.filter((r) => r.t >= params.gapStart && r.t <= params.gapStart + params.gapLength).map((r) => r.cash))
      : null;
    const dateAt = (idx: number | null) => idx == null || idx < 0 ? null
      : new Date(startDate.getFullYear(), startDate.getMonth() + idx, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
    return {
      endNetWorth: end.netWorth, endLow: end.range[0], endHigh: end.range[1],
      citiClear: dateAt(citiIdx), emergFund: dateAt(emergIdx),
      gapMinCash, gapRanOut: gapMinCash != null && gapMinCash < 0,
    };
  }, [rows, params, budget, startDate]);

  const applyScenario = (s: Scenario) => {
    setScenario(s);
    if (s === "steady") { setIncomeGrowth(0.03); setGapLength(0); setSaveMult(1); }
    else if (s === "growth") { setIncomeGrowth(0.08); setGapLength(0); setSaveMult(1); }
    else if (s === "gap") { setIncomeGrowth(0.03); setGapLength(4); setGapStart(6); setSaveMult(1); }
  };

  const gapEndDate = rows.find((r) => r.t === params.gapStart + params.gapLength)?.date;
  const gapStartDate = rows.find((r) => r.t === params.gapStart)?.date;
  const yearTicks = useMemo(() => rows.filter((r) => r.t % 12 === 0).map((r) => r.date), [rows]);

  return (
    <div>
      {/* Controls */}
      <section className="card" style={{ marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.85rem" }}>
          <div className="scn-btns">
            {(["steady", "growth", "gap", "custom"] as Scenario[]).map((s) => (
              <button key={s} className={scenario === s ? "active" : ""} onClick={() => applyScenario(s)}>
                {s === "steady" ? "Steady" : s === "growth" ? "Income growth" : s === "gap" ? "Employment gap" : "Custom"}
              </button>
            ))}
          </div>
          <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
            {TIMESCALES.map((ts) => (
              <button key={ts.label} onClick={() => setMonths(ts.months)}
                style={{ background: months === ts.months ? "var(--accent)" : "transparent", color: months === ts.months ? "var(--accent-fg)" : "var(--muted)", border: "none", padding: "0.3rem 0.6rem", fontSize: "0.82rem", cursor: "pointer" }}>
                {ts.label}
              </button>
            ))}
          </div>
        </div>

        <div className="ctl-grid">
          {/* Market return gradient */}
          <div>
            <div className="ctl-label">
              <span>Market return</span>
              <span className="ctl-val" style={{ color: annualReturn < 0 ? "var(--danger)" : annualReturn < 0.04 ? "var(--warn)" : "var(--ok)" }}>
                {pct(annualReturn)}/yr
              </span>
            </div>
            <input type="range" className="grad" min={0} max={100} value={retSlider}
              onChange={(e) => { setRetSlider(+e.target.value); if (scenario !== "custom") setScenario("custom"); }} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.68rem", color: "var(--muted)", marginTop: "0.2rem" }}>
              <span>pessimistic</span><span>middle</span><span>optimistic</span>
            </div>
          </div>

          {/* Income growth */}
          <div>
            <div className="ctl-label"><span>Income growth</span><span className="ctl-val">{pct(incomeGrowth)}/yr</span></div>
            <input type="range" className="slider" min={-5} max={15} step={0.5} value={incomeGrowth * 100}
              onChange={(e) => { setIncomeGrowth(+e.target.value / 100); if (scenario !== "custom") setScenario("custom"); }} />
          </div>

          {/* Save multiplier */}
          <div>
            <div className="ctl-label"><span>Save / invest rate</span><span className="ctl-val">{saveMult.toFixed(2)}×</span></div>
            <input type="range" className="slider" min={0} max={3} step={0.05} value={saveMult}
              onChange={(e) => { setSaveMult(+e.target.value); if (scenario !== "custom") setScenario("custom"); }} />
          </div>

          {/* Employment gap */}
          <div>
            <div className="ctl-label"><span>Employment gap</span><span className="ctl-val">{gapLength === 0 ? "none" : `${gapLength} mo @ +${gapStart} mo`}</span></div>
            <input type="range" className="slider" min={0} max={24} step={1} value={gapLength}
              onChange={(e) => { setGapLength(+e.target.value); if (scenario !== "gap") setScenario(scenario === "steady" || scenario === "growth" ? "custom" : scenario); }} />
            {gapLength > 0 && (
              <>
                <div className="ctl-label" style={{ marginTop: "0.4rem" }}><span>Gap starts in</span><span className="ctl-val">{gapStart} mo</span></div>
                <input type="range" className="slider" min={0} max={Math.max(1, months - 1)} step={1} value={gapStart}
                  onChange={(e) => setGapStart(+e.target.value)} />
              </>
            )}
          </div>
        </div>

        {/* Series toggles + starting investments */}
        <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: "0.75rem", marginTop: "0.9rem", alignItems: "center" }}>
          <div style={{ display: "flex", gap: "0.9rem", fontSize: "0.8rem", color: "var(--muted)" }}>
            {([["cash", SERIES.cash, "Cash"], ["invest", SERIES.invest, "Investments"], ["debt", SERIES.debt, "Debt"]] as const).map(([k, c, lbl]) => (
              <label key={k} style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem", cursor: "pointer" }}>
                <input type="checkbox" checked={show[k]} onChange={(e) => setShow((v) => ({ ...v, [k]: e.target.checked }))} />
                <i style={{ width: 10, height: 10, borderRadius: 2, background: c, display: "inline-block" }} /> {lbl}
              </label>
            ))}
          </div>
          <label style={{ fontSize: "0.78rem", color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: "0.4rem" }}>
            Starting investments
            <input className="num-in" style={{ width: 110 }} type="number" value={startInvest} onChange={(e) => setStartInvest(+e.target.value || 0)} />
          </label>
        </div>
      </section>

      {/* Chart */}
      <section className="card" style={{ marginBottom: "1rem" }}>
        <p className="sec-h">Net worth projection · {(months / 12).toFixed(0)}yr</p>
        <ChartFrame style={{ width: "100%", height: 340 }}>
          <ResponsiveContainer>
            <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="nwFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={SERIES.netWorth} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={SERIES.netWorth} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" ticks={yearTicks} tick={{ fontSize: 11, fill: "var(--muted)" }} interval="preserveStartEnd" />
              <YAxis tickFormatter={(v) => compact(v as number)} tick={{ fontSize: 11, fill: "var(--muted)" }} width={48} />
              <Tooltip
                isAnimationActive={false}
                content={({ active, payload, label }: any) => (
                  <ChartTooltip
                    active={active}
                    label={label}
                    showName
                    payload={(payload ?? []).map((p: any) => ({
                      ...p,
                      name: p.name === "range" ? "Net worth range" : p.name,
                    }))}
                    format={(v) =>
                      Array.isArray(v) ? `${usd0(v[0])} – ${usd0(v[1])}` : usd0(v as number)
                    }
                  />
                )}
              />
              {/* Employment-gap shaded region */}
              {params.gapStart >= 0 && params.gapLength > 0 && gapStartDate && gapEndDate && (
                <ReferenceArea x1={gapStartDate} x2={gapEndDate} fill="var(--err)" fillOpacity={0.08} stroke="var(--err)" strokeOpacity={0.3} />
              )}
              <ReferenceLine y={0} stroke="var(--muted)" strokeOpacity={0.5} />
              {/* Uncertainty cone (pessimistic..optimistic returns) */}
              <Area type="monotone" dataKey="range" name="range" stroke="none" fill="url(#nwFill)" isAnimationActive={false} />
              {/* Central net worth */}
              <Line type="monotone" dataKey="netWorth" name="Net worth" stroke={SERIES.netWorth} strokeWidth={2.5} dot={false} isAnimationActive={false} />
              {show.cash && <Line type="monotone" dataKey="cash" name="Cash" stroke={SERIES.cash} strokeWidth={1.5} dot={false} isAnimationActive={false} />}
              {show.invest && <Line type="monotone" dataKey="invest" name="Investments" stroke={SERIES.invest} strokeWidth={1.5} dot={false} isAnimationActive={false} />}
              {show.debt && <Line type="monotone" dataKey="debt" name="Debt" stroke={SERIES.debt} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />}
            </ComposedChart>
          </ResponsiveContainer>
        </ChartFrame>
      </section>

      {/* Headline stats */}
      <div className="stat-row" style={{ marginBottom: "1rem" }}>
        <div className="stat">
          <p className="k">Net worth in {(months / 12).toFixed(0)}y</p>
          <p className="v">{usd0(stats.endNetWorth)}</p>
          <p className="muted" style={{ margin: "0.15rem 0 0", fontSize: "0.72rem" }}>{usd0(stats.endLow)} – {usd0(stats.endHigh)}</p>
        </div>
        <div className="stat">
          <p className="k">Citi cleared</p>
          <p className="v">{stats.citiClear ?? "not on this plan"}</p>
        </div>
        <div className="stat">
          <p className="k">Emergency fund hit</p>
          <p className="v">{stats.emergFund ?? "—"}</p>
        </div>
        {params.gapStart >= 0 && params.gapLength > 0 && (
          <div className="stat">
            <p className="k">Cash low in gap</p>
            <p className="v" style={{ color: stats.gapRanOut ? "var(--err)" : undefined }}>
              {stats.gapMinCash != null ? usd0(stats.gapMinCash) : "—"}
            </p>
            {stats.gapRanOut && <p className="muted" style={{ margin: "0.15rem 0 0", fontSize: "0.72rem", color: "var(--err)" }}>runs out — bigger cushion needed</p>}
          </div>
        )}
      </div>

      <p className="muted" style={{ fontSize: "0.76rem" }}>
        Projection assumes your current plan continues: monthly burn (your set targets, or detected spend where no
        target is set yet), planned debt payments until each balance clears, and planned contributions split between
        cash and investments. The shaded cone spans market returns ±{(RETURN_BAND * 100).toFixed(0)}% around the slider.
        Set real necessity and category targets on the Plan tab to sharpen this.
      </p>
    </div>
  );
}
