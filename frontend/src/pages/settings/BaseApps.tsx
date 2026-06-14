import { useEffect, useState } from "react";
import type { BaseApp } from "@shared/types";
import { Card, CardGrid, PageHeader, StateBlock } from "../../ui";

// Base Apps — the foundational tooling and services that make up the tenant
// (workshop itself, clawdbot, openclaw, infra repos). Tenant-level diagnostic
// view: name, branch, last commit per repo found under the tenant home.
// Previously lived at top-level /projects; moved under Settings per ADR-008
// to free "Projects" for the operator-built things.
export default function BaseApps() {
  const [items, setItems] = useState<BaseApp[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/base-apps/")
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        return r.json();
      })
      .then(setItems)
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <div>
      <PageHeader
        title="Base Apps"
        sub={items ? `${items.length} repo${items.length === 1 ? "" : "s"}` : undefined}
      />
      {err ? (
        <StateBlock variant="error" title="Couldn't load base apps" hint={<code>/api/base-apps/</code>} />
      ) : !items ? (
        <p className="ws-muted">Loading…</p>
      ) : items.length === 0 ? (
        <StateBlock title="No repos found" hint="No git repositories under the tenant home yet." />
      ) : (
        <CardGrid>
          {items.map((p) => (
            <li key={p.path}>
              <Card>
                <h2 className="ws-card__title">{p.name}</h2>
                <p className="ws-card__meta ws-mono">
                  {p.branch} · {p.commit?.slice(0, 8)}
                </p>
                <p style={{ marginTop: "var(--space-2)" }}>{p.subject}</p>
                <p className="ws-card__meta">{p.ts}</p>
              </Card>
            </li>
          ))}
        </CardGrid>
      )}
    </div>
  );
}
