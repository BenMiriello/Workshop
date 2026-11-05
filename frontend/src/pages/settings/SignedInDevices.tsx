// The account's signed-in browsers, one per device: what each is called, when it
// was last used, and a way to rename it or sign it out from here. Each row is a
// server-side session (GET/PATCH/DELETE /auth/sessions, backend/routes/auth.py).

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, Monitor, Pencil, Smartphone } from "lucide-react";
import { Button, ConfirmDialog, Input, Notice, Skeleton, StateBlock, SettingsRow as Row, timeAgo } from "@workshop/ui";
import type { components } from "../../api/schema.gen";
import { getJson, jsonOrFail } from "../../lib/http";
import { queryKeys } from "../../lib/queryKeys";
import { describeUserAgent } from "../../lib/userAgent";

type SessionRow = components["schemas"]["SessionRow"];
type SessionList = components["schemas"]["SessionList"];

const ENDPOINT = "/api/auth/sessions";
const SHOWN = 8;

export function deviceName(s: SessionRow): string {
  return s.label || describeUserAgent(s.user_agent);
}

export default function SignedInDevices() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: queryKeys.auth.sessions(),
    queryFn: () => getJson<SessionList>(ENDPOINT),
  });
  const rows = q.data?.sessions ?? null;
  const [error, setError] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [signingOut, setSigningOut] = useState<SessionRow | null>(null);
  const [busy, setBusy] = useState(false);
  const load = async () => { await qc.invalidateQueries({ queryKey: queryKeys.auth.sessions() }); };

  if (q.isError && !rows) {
    return (
      <Row label="signed in">
        <StateBlock variant="error" title="Couldn't load signed-in devices" hint={<code>GET {ENDPOINT}</code>}
          action={<Button size="sm" variant="ghost" onClick={() => q.refetch()}>Retry</Button>} />
      </Row>
    );
  }
  if (!rows) {
    return <Row label="signed in"><Skeleton width="60%" /></Row>;
  }
  if (rows.length === 0) {
    return (
      <Row label="signed in">
        <StateBlock title="No signed-in devices" hint="Each browser you sign in from appears here." />
      </Row>
    );
  }

  const visible = showAll ? rows : rows.slice(0, SHOWN);

  const onSignOut = async () => {
    if (!signingOut) return;
    setBusy(true);
    try {
      await jsonOrFail(await fetch(`${ENDPOINT}/${signingOut.id}`, { method: "DELETE" }));
      await load();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
      setSigningOut(null);
    }
  };

  return (
    <Row label={`signed in (${rows.length})`}>
      <span style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", minWidth: 0 }}>
        {error && <Notice tone="error" where={ENDPOINT}>That change did not save.</Notice>}
        {visible.map((s) => (
          <DeviceLine key={s.id} session={s} onRenamed={load} onSignOut={() => setSigningOut(s)} />
        ))}
        {rows.length > SHOWN && (
          <Button size="sm" variant="ghost" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Show fewer" : `Show all ${rows.length}`}
          </Button>
        )}
      </span>
      <ConfirmDialog
        open={!!signingOut}
        onCancel={() => setSigningOut(null)}
        onConfirm={onSignOut}
        title="Sign this device out?"
        body={`"${signingOut ? deviceName(signingOut) : ""}" will need your password to sign in again.`}
        confirmLabel="Sign out device"
        busy={busy}
      />
    </Row>
  );
}

function DeviceLine({ session, onRenamed, onSignOut }: {
  session: SessionRow;
  onRenamed: () => Promise<void>;
  onSignOut: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(session.label ?? "");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const Icon = /iPhone|iPad|Android/.test(session.user_agent ?? "") ? Smartphone : Monitor;
  const seen = session.last_seen ?? session.created_at;

  const save = async () => {
    setSaving(true);
    try {
      await jsonOrFail(await fetch(`${ENDPOINT}/${session.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      }));
      setEditing(false);
      setFailed(false);
      await onRenamed();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <span style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <Input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120}
            placeholder={describeUserAgent(session.user_agent)} aria-label="Device name"
            style={{ maxWidth: 220 }} autoFocus />
          <Button size="sm" variant="primary" loading={saving} disabled={saving} onClick={save}>Save</Button>
          <Button size="sm" variant="ghost" disabled={saving}
            onClick={() => { setEditing(false); setLabel(session.label ?? ""); setFailed(false); }}>Cancel</Button>
        </span>
        {failed && <Notice tone="error" where={ENDPOINT}>The name did not save.</Notice>}
      </span>
    );
  }

  return (
    <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
      <Icon size={14} strokeWidth={1.8} aria-hidden style={{ flex: "none" }} />
      <span style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
        <span>{deviceName(session)}</span>
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
          {session.current ? "this device" : seen ? `seen ${timeAgo(seen)}` : ""}
        </span>
      </span>
      <span style={{ display: "inline-flex", gap: "var(--space-1)", flex: "none" }}>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}
          aria-label={`Rename ${deviceName(session)}`} title="Rename">
          <Pencil size={14} strokeWidth={1.8} aria-hidden />
        </Button>
        {!session.current && (
          <Button size="sm" variant="danger-ghost" onClick={onSignOut}>
            <LogOut size={14} strokeWidth={1.8} aria-hidden /> Sign out
          </Button>
        )}
      </span>
    </span>
  );
}
