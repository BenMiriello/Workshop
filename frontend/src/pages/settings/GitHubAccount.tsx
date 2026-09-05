import { useCallback, useEffect, useRef, useState } from "react";
import { GitPullRequest, Link2Off, ShieldCheck, Terminal } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  CopyButton,
  Field,
  Input,
  Notice,
  Row,
  SectionLabel,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  Tag,
  Toggle,
  catFor,
  shortHttpError,
} from "@workshop/ui";

// Settings -> Apps: the GitHub account this installation pushes and opens pull
// requests as.
//
// WHY IT IS HERE AND NOT ON A PAGE OF ITS OWN. A GitHub account is a thing this
// installation is connected to, which is the question the Apps tab already
// answers; the issue tracker sits beside it for the same reason. It gets a
// section, not a route.
//
// WHAT IT FIXES. `git push` from an agent session died with "could not read
// Username for 'https://github.com'" on a repository with 812 unpushed commits,
// and there was no surface anywhere in the product that could fix it: the
// credential lived in a `gh` login on disk that nobody had renewed. Backend
// reasoning is in backend/services/github_identity.py.
//
// THE TOKEN IS WRITE ONLY. It goes up once and never comes back; every field
// rendered here is a login, a scope name, a timestamp or a boolean.

const ENDPOINT = "/api/github";

type Status = {
  connected: boolean;
  login: string;
  source: string;
  scopes: string[];
  connected_at: string | null;
  client_id_set: boolean;
  device_available: boolean;
  helper_installed: boolean;
  helper_path: string;
};

type Device = {
  user_code: string;
  verification_uri: string;
  device_code: string;
  interval: number;
  expires_in: number;
};

const SOURCE_LABEL: Record<string, string> = {
  pat: "Personal access token",
  device: "Signed in with GitHub",
};

// What to generate, in the words GitHub's own form uses, so the operator can
// follow it without a second tab open. Fine-grained rather than classic is the
// decision recorded in ticket [SEC-G1]: it keeps the boundary against accounts
// this installation must not touch in the credential, not only in code.
const TOKEN_HELP =
  "Fine-grained token. Resource owner: your own account. Repository permissions: Contents read and write, Metadata read, Pull requests read and write. No organization access.";

function extractDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const d = (body as Record<string, unknown>).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") {
      const m = (d as Record<string, unknown>).message;
      if (typeof m === "string") return m;
    }
  }
  return fallback;
}

export default function GitHubAccount() {
  const [data, setData] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);

  // Write only. Held just long enough to submit, then dropped: this value never
  // comes back from the server and nothing here keeps a copy of it.
  const [token, setToken] = useState("");
  const [clientId, setClientId] = useState("");
  const [device, setDevice] = useState<Device | null>(null);
  const [devicePending, setDevicePending] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const r = await fetch(ENDPOINT);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setData((await r.json()) as Status);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const send = useCallback(async (
    key: string,
    path: string,
    method: "POST" | "PUT" | "DELETE",
    body?: unknown,
  ): Promise<unknown | null> => {
    setBusy(key);
    setActionError(null);
    try {
      const r = await fetch(`${ENDPOINT}${path}`, {
        method,
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const parsed = await r.json().catch(() => null);
      if (!r.ok) {
        throw new Error(extractDetail(
          parsed, shortHttpError(r.status, r.statusText, "")));
      }
      return parsed;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }, []);

  // ---- device authorization grant -------------------------------------------
  // The operator reads a code here, types it on github.com, and this polls until
  // GitHub says yes. GitHub's own interval is honoured rather than a guess, and
  // `authorization_pending` is a state of the flow rather than a failure, so it
  // renders as a line and not as an error.
  const pollOnce = useCallback((code: string, intervalMs: number) => {
    pollTimer.current = window.setTimeout(async () => {
      const out = await send("device", "/device/poll", "POST", { device_code: code });
      if (!out) { setDevice(null); setDevicePending(null); return; }
      const res = out as { status: string; detail: string } & Partial<Status>;
      if (res.status === "connected") {
        setDevice(null);
        setDevicePending(null);
        setData(res as Status);
        return;
      }
      setDevicePending(res.detail);
      pollOnce(code, intervalMs);
    }, intervalMs);
  }, [send]);

  const startDevice = useCallback(async () => {
    const out = await send("start", "/device", "POST");
    if (!out) return;
    const d = out as Device;
    setDevice(d);
    setDevicePending("Waiting for you to enter the code on github.com.");
    // GitHub adds five seconds to the interval on a slow_down, and answering
    // faster than the interval is what earns one. Start one interval out.
    pollOnce(d.device_code, Math.max(d.interval, 5) * 1000);
  }, [send, pollOnce]);

  const cancelDevice = useCallback(() => {
    stopPolling();
    setDevice(null);
    setDevicePending(null);
  }, [stopPolling]);

  // ---- render ---------------------------------------------------------------

  return (
    <section>
      <SectionLabel icon={<GitPullRequest aria-hidden />}>GitHub</SectionLabel>

      {loadError ? (
        <StateBlock
          variant="error"
          title="Couldn't load the GitHub connection"
          hint={`${loadError} (/api/github)`}
          action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
        />
      ) : !data ? (
        <Card><Stack gap="sm"><Skeleton width="45%" /><Skeleton width="30%" /></Stack></Card>
      ) : (
        <Card>
          <Stack gap="sm">
            {actionError && (
              <Notice tone="error" action={
                <Button size="sm" variant="ghost" onClick={() => setActionError(null)}>
                  Dismiss
                </Button>
              }>
                {actionError}
              </Notice>
            )}

            {data.connected ? (
              <Stack gap="sm">
                <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                  <StatusDot status="ok">
                    Connected as <span className="ws-mono">{data.login || "an account"}</span>
                  </StatusDot>
                  {data.source && (
                    <Tag hue={catFor(data.source)}>
                      {SOURCE_LABEL[data.source] ?? data.source}
                    </Tag>
                  )}
                </Row>

                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  {data.scopes.length
                    ? `Scopes: ${data.scopes.join(", ")}.`
                    : "No classic scopes, which is what a fine-grained token looks like."}
                </span>

                {/* A view-shaped switch over a reversible action, so it commits
                    on change rather than behind an Edit and a Save. Nothing
                    existing is at risk: turning it off restores whatever the
                    host had configured before this installation touched it. */}
                <Field
                  label="Shell git push"
                  help={
                    data.helper_installed
                      ? `git and gh in any session on this host authenticate as this account, through ${data.helper_path}.`
                      : "Off. A git push typed in a session has no credential and will fail asking for a username."
                  }
                >
                  <Toggle
                    checked={data.helper_installed}
                    disabled={busy === "helper"}
                    label="Use this account for git in a shell"
                    onChange={async (next) => {
                      const out = await send("helper", "/helper", "PUT", { enabled: next });
                      if (out) setData(out as Status);
                    }}
                  />
                </Field>

                <Row>
                  <Button size="sm" variant="danger-ghost" onClick={() => setConfirmOff(true)}>
                    <Link2Off aria-hidden /> Disconnect
                  </Button>
                </Row>
              </Stack>
            ) : (
              <Stack gap="sm">
                <StatusDot status="stopped">
                  Not connected. Pushes and pull requests have no credential.
                </StatusDot>

                {device ? (
                  <Stack gap="sm">
                    <Field label="Your code" help="Enter this on github.com, then come back. This page is waiting.">
                      <CopyButton value={device.user_code} />
                    </Field>
                    <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                      <a href={device.verification_uri} target="_blank" rel="noreferrer">
                        Open github.com to enter it
                      </a>
                      <Button size="sm" variant="ghost" onClick={cancelDevice}>Cancel</Button>
                    </Row>
                    {devicePending && <Notice tone="info">{devicePending}</Notice>}
                  </Stack>
                ) : (
                  <Stack gap="sm">
                    {data.device_available && (
                      <Row>
                        <Button size="sm" variant="primary" loading={busy === "start"}
                                disabled={busy === "start"}
                                onClick={() => void startDevice()}>
                          <GitPullRequest aria-hidden /> Sign in with GitHub
                        </Button>
                      </Row>
                    )}

                    <Field label={data.device_available ? "Or paste a token" : "Token"}
                           required={!data.device_available}
                           help={TOKEN_HELP}>
                      <Input type="password" value={token} autoComplete="off"
                             placeholder="github_pat_..."
                             onChange={(e) => setToken(e.target.value)} />
                    </Field>
                    <Row>
                      <Button size="sm" variant={data.device_available ? "ghost" : "primary"}
                              loading={busy === "token"}
                              disabled={!token.trim() || busy === "token"}
                              onClick={async () => {
                                const out = await send("token", "/token", "PUT",
                                  { token: token.trim() });
                                if (out) { setToken(""); setData(out as Status); }
                              }}>
                        <ShieldCheck aria-hidden /> Connect
                      </Button>
                    </Row>

                    {/* The device grant needs a registered application's client
                        id, and registering one is an action on github.com that
                        cannot be performed from here. Saying so is better than
                        a button that fails for reasons nobody can see. */}
                    <Field
                      label="GitHub app client ID"
                      optional
                      help={
                        data.client_id_set
                          ? "Saved. Sign in with GitHub is available above."
                          : "Optional. Register an app on github.com with device flow enabled, then save its Client ID here to sign in without pasting a token."
                      }
                    >
                      <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <Input value={clientId} autoComplete="off" placeholder="Iv1..."
                               onChange={(e) => setClientId(e.target.value)} />
                        <Button size="sm" loading={busy === "clientid"}
                                disabled={!clientId.trim() || busy === "clientid"}
                                onClick={async () => {
                                  const out = await send("clientid", "/client-id", "PUT",
                                    { client_id: clientId.trim() });
                                  if (out) { setClientId(""); setData(out as Status); }
                                }}>
                          Save
                        </Button>
                      </Row>
                    </Field>
                  </Stack>
                )}

                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  <Terminal aria-hidden size={14} /> Connecting also offers to
                  authenticate git in a shell, so a push from an agent session
                  works without a token on disk.
                </span>
              </Stack>
            )}
          </Stack>
        </Card>
      )}

      <ConfirmDialog
        open={confirmOff}
        title="Disconnect this GitHub account?"
        body={
          <span>
            The token is deleted from this machine and cannot be shown again, and
            git in a shell stops authenticating as this account. Nothing on
            github.com is touched and no local repository changes.
          </span>
        }
        confirmLabel="Disconnect"
        onConfirm={async () => {
          setConfirmOff(false);
          const out = await send("disconnect", "", "DELETE");
          if (out) setData(out as Status);
        }}
        onCancel={() => setConfirmOff(false)}
      />
    </section>
  );
}
