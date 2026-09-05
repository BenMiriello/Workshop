import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { KeyRound, RefreshCw } from "lucide-react";
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

// Settings -> Providers. Which model providers exist, which have a credential,
// which can actually run a turn, and the actions to add, replace or remove one.
//
// IT IS NOT A MODEL LIST AND THE TITLE NO LONGER SAYS IT IS. Called "Models" it
// promised a catalog, a default and a per-role choice, and delivered one
// subscription row and two empty key fields; the operator read the page as
// empty because the title had told him what to expect. Model choice is made per
// chat, in the chat. This page is the credentials behind it.
//
// THE SUBSCRIPTION ROW TAKES A CREDENTIAL NOW, and that is the change that made
// this page worth opening. It used to be read only, with a line telling the
// operator to go and run a command in a terminal, which is the opposite of
// "set it up through the app". Saving here stores the token in the owner's own
// partition and the next turn spends it, with no restart.
//
// The list is driven entirely by the API. The UI renders only what
// available_providers() returns: a provider whose authentication is prohibited
// on this deployment is ABSENT, never shown disabled, because offering a
// control that cannot legally work invites someone to ask for it to be enabled.
//
// A stored credential is never returned. The route gives presence, provenance,
// whether a turn can run on it, and the last four characters only.

const ENDPOINT = "/api/model-providers";

const SUBSCRIPTION = "subscription_oauth";

type ProviderRow = {
  id: string;
  label: string;
  auth_mode: string;
  stored: boolean;
  configured: boolean;
  usable: boolean;
  source: string | null;
  last4: string | null;
  console_url: string;
  restriction: string;
  runtime_ready: boolean;
  cli_state?: string | null;
  cli_detail?: string | null;
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

// One sentence saying where the credential this provider would use comes from.
// `source` exists so that "I pasted a token and it still says it reads the
// file" has an answer on the screen instead of in a log.
function sourceLine(row: ProviderRow): string | null {
  if (row.source === "principal") {
    return row.last4 ? `Set here, ending ${row.last4}.` : "Set here.";
  }
  if (row.source === "deployment") {
    return "From this machine's auth file. Save a token here to replace it.";
  }
  if (row.source === "cli") {
    return "The CLI is signed in with its own credentials. Save a token here to use that instead.";
  }
  return null;
}

function statusFor(row: ProviderRow): { tone: "ok" | "stopped"; text: string } {
  if (row.usable) return { tone: "ok", text: "Active" };
  if (row.configured) {
    // Configured but nothing routes to it. This is the case the page used to
    // report as ready, which made a key that cannot run a turn look exactly
    // like the subscription that runs every turn.
    return { tone: "stopped", text: "Stored, not in use" };
  }
  return {
    tone: "stopped",
    text: row.auth_mode === SUBSCRIPTION ? "Not signed in" : "Not configured",
  };
}

function CredentialRow({
  row,
  first,
  onRefresh,
}: {
  row: ProviderRow;
  first: boolean;
  onRefresh: () => void;
}) {
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [removing, setRemoving] = useState(false);

  const isSubscription = row.auth_mode === SUBSCRIPTION;
  const noun = isSubscription ? "token" : "key";
  const status = statusFor(row);
  const where = sourceLine(row);

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
      setEditing(false);
      onRefresh();
    } catch {
      setSaveError("Network error. Check your connection.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setRemoving(true);
    setSaveError(null);
    try {
      const r = await fetch(`${ENDPOINT}/${row.id}/credential`, { method: "DELETE" });
      if (!r.ok) setSaveError(extractDetail(await r.json()));
      onRefresh();
    } catch {
      setSaveError("Network error. Check your connection.");
    } finally {
      setRemoving(false);
      setConfirmOpen(false);
    }
  };

  return (
    <div
      style={{
        padding: "var(--space-3) 0",
        borderTop: first ? undefined : "1px solid var(--border)",
      }}
    >
      <Stack gap="sm">
        <Row style={{ justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
          <span style={{ fontSize: "var(--fs-sm)" }}>{row.label}</span>
          <StatusDot status={status.tone}>{status.text}</StatusDot>
        </Row>

        {where && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{where}</span>
        )}

        {row.configured && !row.usable && !isSubscription && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            No turn runs on this provider yet, so the key is stored and unused.
          </span>
        )}

        {isSubscription && row.source === "cli" && row.cli_detail && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            {row.cli_detail}
          </span>
        )}

        {saveError && <Notice tone="error">{saveError}</Notice>}

        {editing ? (
          <Stack gap="sm">
            <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
              <Input
                type="password"
                autoComplete="off"
                value={value}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setValue(e.target.value)}
                placeholder={`Paste the ${noun}`}
                style={{ flex: 1 }}
                disabled={saving}
              />
              <Button size="sm" onClick={save} disabled={!value.trim() || saving}>
                {saving ? "Checking" : "Save"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setValue("");
                  setSaveError(null);
                  setEditing(false);
                }}
                disabled={saving}
              >
                Cancel
              </Button>
            </Row>
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
              {isSubscription
                ? "Saving runs one short turn to check the token works, then stores it. Every turn after that uses it."
                : "Saving checks the key against the provider before storing it."}
            </span>
          </Stack>
        ) : (
          <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <KeyRound aria-hidden /> {row.stored ? `Replace ${noun}` : `Add ${noun}`}
            </Button>
            {row.stored && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirmOpen(true)}
                disabled={removing}
              >
                Remove
              </Button>
            )}
          </Row>
        )}

        <ConfirmDialog
          open={confirmOpen}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={remove}
          title={`Remove this ${noun}`}
          body={
            isSubscription
              ? `Remove the ${row.label} token stored here? Turns fall back to this machine's own sign-in, and if there is none they will stop working. This cannot be undone.`
              : `Remove the ${row.label}? This cannot be undone.`
          }
          confirmLabel="Remove"
          busy={removing}
        />

        {row.console_url && !row.stored && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            Get one at{" "}
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

  const subscriptionRows = (rows ?? []).filter((r) => r.auth_mode === SUBSCRIPTION);
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
        {!failed && subscriptionRows.length > 0 && (
          <section>
            <SectionLabel>Subscription</SectionLabel>
            <Card>
              {subscriptionRows.map((row, i) => (
                <CredentialRow
                  key={row.id}
                  row={row}
                  first={i === 0}
                  onRefresh={() => void load()}
                />
              ))}
            </Card>
          </section>
        )}

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

            {!failed && apiKeyRows.map((row, i) => (
              <CredentialRow
                key={row.id}
                row={row}
                first={i === 0}
                onRefresh={() => void load()}
              />
            ))}
          </Card>
        </section>
      </Stack>
    </div>
  );
}
