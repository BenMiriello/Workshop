import { useCallback, useEffect, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import { Banknote, PiggyBank, TrainFront, Watch, Bird, MapPin, ExternalLink, type LucideProps } from "lucide-react";
import { Card, CardGrid, PageHeader, Row, StatusDot, type StatusKind } from "../ui";

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

// Card status tone maps to the design-system status kinds (or null for neutral
// muted text). "warn" doubles for degraded; "err" for unreachable.
type Tone = "ok" | "warn" | "err" | "muted";

const toneStatus = (tone: Tone | undefined): StatusKind | null => {
  switch (tone) {
    case "ok":
      return "ok";
    case "warn":
      return "warn";
    case "err":
      return "danger";
    default:
      return null;
  }
};

type AppCard = {
  key: string;
  Icon: ComponentType<LucideProps>;
  title: string;
  tagline: string;
  status: { text: string; tone?: Tone } | null;
  to?: string; // internal Workshop route — react-router Link
  href?: string; // same-origin path-prefixed app or external URL
  external?: boolean; // true => render an ExternalLink hint; we still navigate same-tab
};

function CardBody({ card }: { card: AppCard }) {
  const Icon = card.Icon;
  const kind = card.status ? toneStatus(card.status.tone) : null;
  return (
    <div style={{ padding: "var(--space-5)", height: "100%" }}>
      <Row style={{ marginBottom: "var(--space-2)" }}>
        <Icon size={22} strokeWidth={1.8} aria-hidden style={{ flexShrink: 0 }} />
        <h3 className="ws-card__title" style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
          {card.title}
          {card.external && <ExternalLink size={14} strokeWidth={1.8} aria-hidden style={{ color: "var(--muted)" }} />}
        </h3>
      </Row>
      <p className="ws-card__meta">{card.tagline}</p>
      {card.status &&
        (kind ? (
          <div style={{ marginTop: "var(--space-2)", fontSize: "var(--fs-xs)" }}>
            <StatusDot status={kind}>{card.status.text}</StatusDot>
          </div>
        ) : (
          <p className="ws-card__meta" style={{ marginTop: "var(--space-2)" }}>
            {card.status.text}
          </p>
        ))}
    </div>
  );
}

// Per-port HTTPS via Tailscale Serve for apps that remain their own origin.
// (Commute is now in-workshop at /apps/commute; Wearables stays external.)
const OW_URL = "https://debian.tail38e62e.ts.net:8445/";

export default function Apps() {
  const [financeStatus, setFinanceStatus] = useState<{ accountCount: number | null; ageS: number | null; stale: boolean } | null>(null);
  const [services, setServices] = useState<ServiceProbe[] | null>(null);
  const [birdStatus, setBirdStatus] = useState<{ species: number; today: number; available: boolean } | null>(null);
  const [locationStatus, setLocationStatus] = useState<{ ageS: number | null; available: boolean } | null>(null);

  const loadFinance = useCallback(async () => {
    try {
      const [statusR, balR] = await Promise.all([fetch("/api/finance/cache"), fetch("/api/finance/balances")]);
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

  const loadBirds = useCallback(async () => {
    try {
      const r = await fetch("/api/birds/summary");
      const s = r.ok ? await r.json() : null;
      if (s) setBirdStatus({ species: s.species_count || 0, today: s.today?.detections || 0, available: !!s.available });
      else setBirdStatus({ species: 0, today: 0, available: false });
    } catch {
      setBirdStatus({ species: 0, today: 0, available: false });
    }
  }, []);

  const loadLocation = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : { latest: null };
      const rec = j.latest as { ts?: string } | null;
      if (rec?.ts) {
        const ageS = Math.max(0, Math.round((Date.now() - Date.parse(rec.ts)) / 1000));
        setLocationStatus({ ageS, available: true });
      } else {
        setLocationStatus({ ageS: null, available: false });
      }
    } catch {
      setLocationStatus({ ageS: null, available: false });
    }
  }, []);

  useEffect(() => {
    loadFinance();
    loadServices();
    loadBirds();
    loadLocation();
  }, [loadFinance, loadServices, loadBirds, loadLocation]);

  const findService = (needle: string) => services?.find((s) => s.name.includes(needle));

  const cards: AppCard[] = [
    {
      key: "finance",
      Icon: Banknote,
      title: "Finance",
      tagline: "Balances, accounts, recent transactions",
      to: "/apps/finance",
      status:
        financeStatus == null
          ? { text: "loading…", tone: "muted" }
          : financeStatus.accountCount == null
            ? { text: "unavailable", tone: "err" }
            : {
                text: `${financeStatus.accountCount} account${financeStatus.accountCount === 1 ? "" : "s"} · ${financeStatus.ageS != null ? `updated ${fmtAge(financeStatus.ageS)}` : "no data yet"}`,
                tone: financeStatus.stale ? "warn" : "muted",
              },
    },
    {
      key: "budget",
      Icon: PiggyBank,
      title: "Budget",
      tagline: "Payoff plan, savings goals, projections",
      to: "/apps/budget",
      status: { text: "plan & simulate", tone: "muted" },
    },
    {
      key: "birds",
      Icon: Bird,
      title: "Yard Birds",
      tagline: "Backyard bird-song detections, live",
      to: "/apps/birds",
      status:
        birdStatus == null
          ? { text: "loading…", tone: "muted" }
          : !birdStatus.available
            ? { text: "no detections yet", tone: "muted" }
            : { text: `${birdStatus.species} species · ${birdStatus.today} today`, tone: "ok" },
    },
    {
      key: "location",
      Icon: MapPin,
      title: "Location",
      tagline: "Live position + history from OwnTracks",
      to: "/apps/location",
      status:
        locationStatus == null
          ? { text: "loading…", tone: "muted" }
          : !locationStatus.available
            ? { text: "no pings yet", tone: "muted" }
            : { text: `last ping ${fmtAge(locationStatus.ageS ?? 0)}`, tone: "ok" },
    },
    {
      key: "commute",
      Icon: TrainFront,
      title: "Commute",
      tagline: "Day-of departures + transit status",
      to: "/apps/commute",
      status: { text: "schedule & live status", tone: "muted" },
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
          ? ({ text: `up · ${probe.http_status} · ${probe.latency_ms}ms`, tone: "ok" } as const)
          : ({ text: `down — ${probe.notes || `HTTP ${probe.http_status}`}`, tone: "err" } as const);
      })(),
    },
  ];

  return (
    <div>
      <PageHeader title="Apps" sub={`${cards.length} app${cards.length === 1 ? "" : "s"}`} />
      <CardGrid>
        {cards.map((card) => (
          <li key={card.key}>
            <Card interactive pad0 style={{ overflow: "hidden" }}>
              {card.to ? (
                <Link to={card.to} style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}>
                  <CardBody card={card} />
                </Link>
              ) : (
                <a
                  href={card.href}
                  rel="noopener noreferrer"
                  style={{ display: "block", height: "100%", color: "inherit", textDecoration: "none" }}
                >
                  <CardBody card={card} />
                </a>
              )}
            </Card>
          </li>
        ))}
      </CardGrid>
    </div>
  );
}
