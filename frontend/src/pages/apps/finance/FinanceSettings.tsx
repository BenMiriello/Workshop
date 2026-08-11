import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Banknote } from "lucide-react";
import { ConfirmDialog, SectionLabel } from "@workshop/ui";
import { BankConnections } from "../PlaidLink";
import { SettingsSection, SettingsRow as Row } from "../AppSettings";

// Finance app settings — bank connections + Plaid response cache. Moved verbatim
// out of the platform Settings page (Phase 5 settings split). Renders inside the
// Finance page's <AppSettings> slot.

export default function FinanceSettings() {
  const [cache, setCache] = useState<Record<string, { age_s: number; ttl_s: number }> | null>(null);
  const [confirmInvalidate, setConfirmInvalidate] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/finance/cache");
      const j = r.ok ? await r.json() : { entries: {} };
      setCache(j.entries || {});
    } catch {
      setCache({});
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onInvalidate = async () => {
    setBusy(true);
    try {
      await fetch("/api/finance/cache/invalidate", { method: "POST" });
      await load();
    } finally {
      setBusy(false);
      setConfirmInvalidate(false);
    }
  };

  const entries = cache ? Object.entries(cache) : [];
  return (
    <>
      <section style={{ marginBottom: "var(--space-6)" }}>
        <SectionLabel icon={<Banknote aria-hidden />}>Banks</SectionLabel>
        <BankConnections />
      </section>
      <SettingsSection icon={Banknote} title="Finance">
        <Row label="cache">
          {entries.length === 0 ? "no entries" : `${entries.length} key${entries.length === 1 ? "" : "s"}`}
          <button onClick={() => setConfirmInvalidate(true)} style={{ marginLeft: "0.6rem", padding: "0.2rem 0.5rem", fontSize: "0.78rem", color: "var(--err)" }}>
            invalidate all
          </button>
        </Row>
        {entries.map(([k, v]) => (
          <Row key={k} label={k}>
            <span className="muted" style={{ fontSize: "0.85rem", fontFamily: "ui-monospace, monospace" }}>
              age {Math.round(v.age_s)}s · ttl {v.ttl_s}s
            </span>
          </Row>
        ))}
        <Row label="connected">
          <Link to="/apps/finance" style={{ color: "var(--accent)" }}>view balances + transactions ↗</Link>
        </Row>
      </SettingsSection>
      <ConfirmDialog
        open={confirmInvalidate}
        onCancel={() => setConfirmInvalidate(false)}
        onConfirm={onInvalidate}
        danger={false}
        title="Clear the cached bank data?"
        body="Drops every cached Plaid response. The next load fetches it fresh from your banks, which takes a few seconds."
        confirmLabel="Clear cache"
        busy={busy}
      />
    </>
  );
}
