import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  Input,
  Notice,
  PageHeader,
  Row,
  SectionLabel,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
} from "@workshop/ui";

// Settings -> Providers. Which model providers exist, which have a key stored,
// and the actions to add or remove a key.
//
// IT IS NOT A MODEL LIST AND THE TITLE NO LONGER SAYS IT IS. Called "Models" it
// promised a catalog, a default and a per-role choice, and delivered one
// subscription row and two empty key fields; the operator read the page as
// empty because the title had told him what to expect. Model choice is made per
// chat, in the chat. This page is the credentials behind it.
//
// The list is driven entirely by the API -- the UI renders only what
// available_providers() returns. A provider whose authentication is
// prohibited on this deployment is absent, never shown disabled.
//
// A stored key is never returned. The route gives presence, label, and the
// last four characters only.

const ENDPOINT = "/api/model-providers";

type ProviderRow = {
  id: string;
  label: string;
  auth_mode: string;
  stored: boolean;
  ready: boolean;
  last4: string | null;
  console_url: string;
  restriction: string;
};

function extractDetail(body: unknown): string {
  if (body && typeof body === "object") {
    const d = (body as Record<string, unknown>).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") {
      const m = (d as Record<string, unknown>).message;
      if (typeof m === "string") return m;
    }
  }
  return "An error occurred.";
}

function SubscriptionRow({ row }: { row: ProviderRow }) {
  return (
    <div style={{ padding: "var(--space-3) 0" }}>
      <Row style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
        <span style={{ fontSize: "var(--fs-sm)" }}>{row.label}</span>
        <StatusDot status={row.ready ? "ok" : "stopped"}>
          {row.ready ? "Active" : "Not signed in"}
        </StatusDot>
      </Row>
      {!row.ready && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--space-1)" }}>
          Run <span className="ws-mono">claude auth login</span> to sign in.
        </p>
      )}
    </div>
  );
}

function ApiKeyRow({ row, onRefresh }: { row: ProviderRow; onRefresh: () => void }) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [removing, setRemoving] = useState(false);

  const save = async () => {
    if (!value.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const r = await fetch(`${ENDPOINT}/${row.id}/credential`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: value.trim() }),
      });
      if (!r.ok) {
        setSaveError(extractDetail(await r.json()));
        return;
      }
      setValue("");
      onRefresh();
    } catch {
      setSaveError("Network error. Check your connection.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setRemoving(true);
    try {
      await fetch(`${ENDPOINT}/${row.id}/credential`, { method: "DELETE" });
      onRefresh();
    } catch {
      // silent — the refresh will show current state
    } finally {
      setRemoving(false);
      setConfirmOpen(false);
    }
  };

  return (
    <div style={{ padding: "var(--space-3) 0", borderTop: "1px solid var(--border)" }}>
      <Stack gap="sm">
        <Row style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
          <span style={{ fontSize: "var(--fs-sm)" }}>{row.label}</span>
          {row.stored ? (
            <StatusDot status="ok">
              Key added{row.last4 ? ` · ···${row.last4}` : ""}
            </StatusDot>
          ) : (
            <StatusDot status="stopped">Not configured</StatusDot>
          )}
        </Row>

        {saveError && (
          <Notice tone="error">{saveError}</Notice>
        )}

        {row.stored ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmOpen(true)}
              disabled={removing}
            >
              Remove key
            </Button>
            <ConfirmDialog
              open={confirmOpen}
              onCancel={() => setConfirmOpen(false)}
              onConfirm={remove}
              title="Remove API key"
              body={`Remove the ${row.label}? This cannot be undone.`}
              confirmLabel="Remove"
              busy={removing}
            />
          </>
        ) : (
          <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <Input
              value={value}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setValue(e.target.value)}
              placeholder={`Paste your ${row.label}`}
              style={{ flex: 1 }}
              disabled={saving}
            />
            <Button
              size="sm"
              onClick={save}
              disabled={!value.trim() || saving}
            >
              {saving ? "Verifying" : "Save"}
            </Button>
          </Row>
        )}

        {row.console_url && !row.stored && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            Get a key at{" "}
            <a href={row.console_url} target="_blank" rel="noopener noreferrer">
              {row.console_url}
            </a>
          </span>
        )}
      </Stack>
    </div>
  );
}

export default function ModelProviders() {
  const [rows, setRows] = useState<ProviderRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(`${r.status}`);
      setRows(await r.json());
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const subscriptionRows = (rows ?? []).filter((r) => r.auth_mode === "subscription_oauth");
  const apiKeyRows = (rows ?? []).filter((r) => r.auth_mode === "api_key");

  return (
    <div>
      <PageHeader
        title="Providers"
        sub="Where this installation gets its models. Which model a chat uses is picked in that chat."
        actions={
          <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw aria-hidden /> {loading ? "Checking" : "Refresh"}
          </Button>
        }
      />

      <Stack>
        {/* Subscription provider */}
        {!failed && subscriptionRows.length > 0 && (
          <section>
            <SectionLabel>Subscription</SectionLabel>
            <Card>
              {subscriptionRows.map((row) => (
                <SubscriptionRow key={row.id} row={row} />
              ))}
            </Card>
          </section>
        )}

        {/* API key providers */}
        <section>
          <SectionLabel>API keys</SectionLabel>
          <Card>
            {loading && !rows && (
              <Stack gap="sm">
                <Skeleton width="55%" /><Skeleton width="40%" />
              </Stack>
            )}

            {failed && (
              <StateBlock
                variant="error"
                title="Could not load providers"
                hint={`GET ${ENDPOINT} failed (${failed}).`}
                action={
                  <Button size="sm" variant="ghost" onClick={() => void load()}>
                    Try again
                  </Button>
                }
              />
            )}

            {!failed && rows !== null && apiKeyRows.length === 0 && (
              <StateBlock
                title="No key providers available"
                hint="This deployment does not offer any API key providers."
              />
            )}

            {!failed && apiKeyRows.map((row) => (
              <ApiKeyRow key={row.id} row={row} onRefresh={() => void load()} />
            ))}
          </Card>
        </section>
      </Stack>
    </div>
  );
}
