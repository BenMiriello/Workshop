import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import { Banknote, TrainFront, Watch, BookOpen, MapPin, ExternalLink, type LucideProps } from "lucide-react";

// Apps page: a uniform grid of cards. Every app — internal or external — looks
// the same here. Tap a card to enter the app. Privacy-on-glance: cards show
// generic status (counts / freshness / health) but never specific values like
// dollar amounts. Real data lives one tap deeper, on each app's full page.

type CacheMeta = { cached: boolean; ts: number; age_s: number; ttl_s: number };

type FinanceStatusEntry = { ts: number; age_s: number; ttl_s: number; size_bytes: number };
type FinanceStatusResp = {
  entries: Record<string, FinanceStatusEntry>;
};

type FinanceBalancesShape = { items: { accounts: unknown[] }[]; _cache?: CacheMeta };

type ServiceProbe = {
  name: string;
  status: "up" | "down" | string;
  http_status?: number | null;
  latency_ms?: number | null;
  notes?: string | null;
};
type ServicesResp = { services: ServiceProbe[] };

const fmtAge = (sec: number) => {
  if (sec <= 0) return "just now";
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
};

type Tone = "ok" | "warn" | "err" | "muted";

// One unified card shape. `to` is internal react-router; `href` is for cards
// that point at a non-Workshop URL but should still navigate same-tab so the
// experience feels like one app.
type AppCard = {
  key: string;
  Icon: ComponentType<LucideProps>;
  title: string;
  tagline: string;
  status: { text: string; tone?: Tone } | null;
  to?: string;        // internal Workshop route — react-router Link
  href?: string;      // same-origin path-prefixed app or external URL
  external?: boolean; // true => render an ExternalLink hint; we still navigate same-tab
};

const toneColor = (tone: Tone | undefined) => {
  switch (tone) {
    case "ok":   return "#16a34a";
    case "warn": return "var(--err)";   // err palette doubles as warn
    case "err":  return "var(--err)";
    case "muted":
    default:     return "var(--muted)";
  }
};

function CardBody({ card }: { card: AppCard }) {
  const Icon = card.Icon;
  return (
    <div style={{ padding: "1rem", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.4rem" }}>
        <Icon size={22} strokeWidth={1.8} aria-hidden style={{ flexShrink: 0 }} />
        <h3 style={{ margin: 0, fontSize: "1rem", display: "flex", alignItems: "center", gap: "0.25rem" }}>
          {card.title}
          {card.external && (
            <ExternalLink size={14} strokeWidth={1.8} aria-hidden style={{ color: "var(--muted)" }} />
          )}
        </h3>
      </div>
      <p className="muted" style={{ margin: "0 0 0.5rem" }}>{card.tagline}</p>
      {card.status && (
        <p style={{ margin: 0, fontSize: "0.8rem", color: toneColor(card.status.tone) }}>{card.status.text}</p>
      )}
    </div>
  );
}

// Per-port HTTPS via Tailscale Serve. Each Workshop app is a peer on its
// own port — no app is "the root". The launcher at :443 lists all three.
const COMMUTE_URL = "https://debian.tail38e62e.ts.net:8444/";
const OW_URL = "https://debian.tail38e62e.ts.net:8445/";
const OW_DOCS_URL = `http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:8100/docs`;

type LocationLatest = {
  ts?: string;
  lat?: number;
  lng?: number;
  accuracy_m?: number;
  source?: string;
} | null;

export default function Apps() {
  const [financeStatus, setFinanceStatus] = useState<{ accountCount: number | null; ageS: number | null; stale: boolean } | null>(null);
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [loc, setLoc] = useState<LocationLatest>(null);

  // Load finance ACCOUNT COUNT + freshness only — never the dollar values. This
  // is what differentiates the Apps grid from the Finance app page itself.
  const loadFinance = useCallback(async () => {
    try {
      // /finance/cache gives us per-key TS + TTL without re-fetching from Plaid.
      const [statusR, balR] = await Promise.all([
        fetch("/api/finance/cache"),
        fetch("/api/finance/balances"),
      ]);
      const status = statusR.ok ? ((await statusR.json()) as FinanceStatusResp) : { entries: {} };
      const bal = balR.ok ? ((await balR.json()) as FinanceBalancesShape) : { items: [] };
      const accountCount = (bal.items || []).reduce((s, it) => s + (it.accounts?.length || 0), 0);
      const entry = status.entries?.["balances"];
      const ageS = entry ? Math.max(0, entry.age_s) : null;
      const ttlS = entry?.ttl_s ?? 600;
      setFinanceStatus({ accountCount, ageS, stale: ageS != null && ageS > ttlS });
    } catch {
      setFinanceStatus({ accountCount: null, ageS: null, stale: false });
    }
  }, []);

  const loadServices = useCallback(async () => {
    try {
      const r = await fetch("/api/services/");
      const s = r.ok ? ((await r.json()) as ServicesResp) : { services: [] };
      setServices(s.services || []);
    } catch {
      setServices([]);
    }
  }, []);

  const loadLocation = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : { latest: null };
      setLoc(j.latest as LocationLatest);
    } catch {
      setLoc(null);
    }
  }, []);

  useEffect(() => {
    loadFinance();
    loadServices();
    loadLocation();
    const t = setInterval(loadLocation, 60_000);
    return () => clearInterval(t);
  }, [loadFinance, loadServices, loadLocation]);

  const findService = (needle: string) => services?.find((s) => s.name.includes(needle));

  // Build the cards list. Privacy: finance card shows account count + freshness,
  // NOT dollar amounts. To see real data, tap into the app.
  const cards: AppCard[] = [
    {
      key: "finance",
      Icon: Banknote,
      title: "Finance",
      tagline: "Balances, accounts, recent transactions",
      to: "/apps/finance",
      status: financeStatus == null
        ? { text: "loading…", tone: "muted" }
        : financeStatus.accountCount == null
          ? { text: "unavailable", tone: "err" }
          : {
              text: `${financeStatus.accountCount} account${financeStatus.accountCount === 1 ? "" : "s"} · ${financeStatus.ageS != null ? `updated ${fmtAge(financeStatus.ageS)}` : "no data yet"}`,
              tone: financeStatus.stale ? "warn" : "muted",
            },
    },
    {
      key: "commute",
      Icon: TrainFront,
      title: "Commute",
      tagline: "Day-of departures + weather",
      href: COMMUTE_URL,
      external: true,
      status: { text: "tap to open", tone: "muted" },
    },
    {
      key: "wearables",
      Icon: Watch,
      title: "Open Wearables",
      tagline: "Wearables data UI",
      href: OW_URL,
      external: true,
      status: (() => {
        const probe = findService("open-wearables-backend");
        if (services === null) return { text: "probing…", tone: "muted" } as const;
        if (!probe) return { text: "no probe configured", tone: "muted" } as const;
        return probe.status === "up"
          ? { text: `up · ${probe.http_status} · ${probe.latency_ms}ms`, tone: "ok" } as const
          : { text: `down — ${probe.notes || `HTTP ${probe.http_status}`}`, tone: "err" } as const;
      })(),
    },
    {
      key: "ow-docs",
      Icon: BookOpen,
      title: "OW API docs",
      tagline: "Open Wearables backend reference",
      href: OW_DOCS_URL,
      external: true,
      status: { text: "tap to open", tone: "muted" },
    },
    {
      key: "location",
      Icon: MapPin,
      title: "Location",
      tagline: "Phone position (Owntracks iOS)",
      to: "/apps/location",
      status: (() => {
        if (loc === null) return { text: "no data yet — install Owntracks", tone: "muted" } as const;
        if (!loc?.ts) return { text: "no data yet", tone: "muted" } as const;
        const ageS = Math.max(0, Math.round((Date.now() - Date.parse(loc.ts)) / 1000));
        const stale = ageS > 60 * 60; // > 1 hour with no ping → stale
        return {
          text: `${loc.source || "unknown"} · updated ${fmtAge(ageS)}`,
          tone: stale ? "warn" : "ok",
        } as const;
      })(),
    },
  ];

  return (
    <div>
      <p className="muted" style={{ marginBottom: "1rem" }}>{cards.length} app{cards.length === 1 ? "" : "s"}</p>
      <ul className="cards">
        {cards.map((card) => (
          <li key={card.key} className="card" style={{ padding: 0, overflow: "hidden" }}>
            {card.to ? (
              <Link to={card.to} style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}>
                <CardBody card={card} />
              </Link>
            ) : (
              // No target=_blank. Operator wants every card to feel like one app —
              // including the path-prefixed sibling Vite apps (Commute) and the
              // cross-origin tools. Same-tab navigation across origins is fine for
              // a phone-first dashboard; back button returns to Workshop.
              <a
                href={card.href}
                rel="noopener noreferrer"
                style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}
              >
                <CardBody card={card} />
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
