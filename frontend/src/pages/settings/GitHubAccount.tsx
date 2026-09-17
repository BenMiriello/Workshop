import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  FolderGit2,
  GitPullRequest,
  KeyRound,
  Link2,
  Link2Off,
  ShieldCheck,
  SquareAsterisk,
} from "lucide-react";
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
  Select,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  Tag,
  Toggle,
  catFor,
  shortHttpError,
} from "@workshop/ui";
import type { components } from "../../api/schema.gen";

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
// CONNECT IS ONE BUTTON NOW (docs/platform-redesign/
// github-connect-flow-2026-09-16.md). It registers an application from a
// generated manifest and then authorizes it, and neither leg asks the operator
// to copy anything. What used to be the whole screen, a pasted token and a
// hand-entered client id, is what is left when this installation has no address
// the forge could redirect back to.
//
// THE TOKEN IS WRITE ONLY. It goes up once and never comes back; every field
// rendered here is a login, a scope name, a timestamp or a boolean. So is the
// registered application's client secret, which no response carries at all.

const ENDPOINT = "/api/github";

// Named from the backend's own schema, never restated here: what this screen
// may know about the connection is a decision the route makes, and a copy of it
// would be the place the two drift apart.
type Defaults = components["schemas"]["DefaultsOut"];
type Status = components["schemas"]["StatusOut"];
type Device = components["schemas"]["DeviceStartOut"];
type Manifest = components["schemas"]["ManifestOut"];
type Authorize = components["schemas"]["AuthorizeOut"];

const SOURCE_LABEL: Record<string, string> = {
  pat: "Personal access token",
  device: "Signed in with GitHub",
  oauth: "Signed in with GitHub",
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
  const [branchDraft, setBranchDraft] = useState<string | null>(null);
  const [device, setDevice] = useState<Device | null>(null);
  const [devicePending, setDevicePending] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  // The manifest POST is a real form submission, not a fetch: the forge answers
  // it with a page for the operator to read and approve.
  const manifestForm = useRef<HTMLFormElement | null>(null);
  const manifestField = useRef<HTMLInputElement | null>(null);

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

  // RE-READ WHEN THIS PAGE COMES BACK INTO VIEW, because the sign-in finishes
  // somewhere this component cannot see.
  //
  // Three paths all end here and none of them re-mounts anything:
  //   * the operator goes to github.com, authorizes, and the browser restores
  //     this page from the back/forward cache, where `busy` is whatever it was
  //     when the navigation started -- a Connect button still spinning for a
  //     flow that is over;
  //   * they press Back from github.com without authorizing, same restore,
  //     same stuck control, and nothing else would ever clear it;
  //   * the native shell hands github.com to the SYSTEM browser
  //     (mobile/app/index.tsx sends any non-app origin to `Linking.openURL`),
  //     so the sign-in completes in Safari against a different cookie jar and
  //     the WebView is never navigated at all. It keeps saying "Not connected"
  //     for a connection that succeeded.
  //
  // `pageshow` covers the bfcache restore specifically (a plain `load` event
  // does not fire on one); `visibilitychange` covers coming back to the tab or
  // the app. Both are cheap: one GET that returns no secret.
  useEffect(() => {
    const refresh = () => { setBusy(null); void load(); };
    const onVisible = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("pageshow", refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("pageshow", refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  // A callback that could not finish lands back here with its sentence in the
  // query string, because the browser followed a redirect from the forge and
  // there is no response body it would ever have shown.
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const failed = params.get("github_error");
    if (!failed) return;
    setActionError(failed);
    const next = new URLSearchParams(params);
    next.delete("github_error");
    setParams(next, { replace: true });
  }, [params, setParams]);

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

  // ---- connect --------------------------------------------------------------
  // One button, two legs. With no application registered yet it POSTs a
  // generated manifest at the forge, which creates one and redirects back into
  // the authorize leg on its own. With one already registered it goes straight
  // to authorize, which is what a reconnect is.
  const connect = useCallback(async () => {
    if (!data) return;
    if (data.app_registered) {
      const out = await send("connect", "/authorize", "POST");
      if (!out) return;
      window.location.assign((out as Authorize).url);
      return;
    }
    const out = await send("connect", "/app/manifest", "POST");
    if (!out) return;
    const reg = out as Manifest;
    const form = manifestForm.current;
    const field = manifestField.current;
    if (!form || !field) return;
    form.action = reg.post_url;
    field.value = reg.manifest;
    form.submit();
  }, [data, send]);

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

  // ---- the global defaults --------------------------------------------------
  // View-shaped switches over values nothing existing depends on: they decide
  // what the NEXT app's repository looks like, so they commit on change rather
  // than behind an Edit and a Save. The branch name is typed, so it keeps a
  // Save: there is no sensible moment to commit a half-typed word.
  const saveDefaults = useCallback(async (patch: Partial<Defaults>) => {
    const out = await send("defaults", "/defaults", "PUT", patch);
    if (out) { setBranchDraft(null); setData(out as Status); }
  }, [send]);

  // ---- render ---------------------------------------------------------------

  const defaults = data?.defaults;
  const branch = branchDraft ?? defaults?.default_branch_name ?? "";
  const branchDirty =
    branchDraft !== null && branchDraft.trim() !== defaults?.default_branch_name;

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
                    {data.manifest_available ? (
                      <Row>
                        <Button size="sm" variant="primary" loading={busy === "connect"}
                                disabled={busy === "connect"}
                                onClick={() => void connect()}>
                          <Link2 aria-hidden /> Connect GitHub
                        </Button>
                      </Row>
                    ) : (
                      <Notice tone="warn">
                        This installation has no address GitHub can redirect back
                        to, so it cannot register an app. Paste a token instead.
                      </Notice>
                    )}

                    <SectionLabel icon={<KeyRound aria-hidden />}>Other ways in</SectionLabel>

                    {data.device_available && (
                      <Row>
                        <Button size="sm" loading={busy === "start"}
                                disabled={busy === "start"}
                                onClick={() => void startDevice()}>
                          <SquareAsterisk aria-hidden /> Sign in with a device code
                        </Button>
                      </Row>
                    )}

                    <Field label="Token" help={TOKEN_HELP}>
                      <Input type="password" value={token} autoComplete="off"
                             placeholder="github_pat_..."
                             onChange={(e) => setToken(e.target.value)} />
                    </Field>
                    <Row>
                      <Button size="sm" loading={busy === "token"}
                              disabled={!token.trim() || busy === "token"}
                              onClick={async () => {
                                const out = await send("token", "/token", "PUT",
                                  { token: token.trim() });
                                if (out) { setToken(""); setData(out as Status); }
                              }}>
                        <ShieldCheck aria-hidden /> Connect
                      </Button>
                    </Row>

                    <Field
                      label="GitHub app client ID"
                      optional
                      help={data.client_id_set ? "Saved." : "For a device sign-in."}
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
              </Stack>
            )}

            {defaults && <>
            <SectionLabel icon={<FolderGit2 aria-hidden />}>New repositories</SectionLabel>

            <Field label="Repository per app">
              <Toggle
                checked={defaults.auto_create_repo}
                disabled={busy === "defaults"}
                label="Create one for each new app"
                onChange={(next) => void saveDefaults({ auto_create_repo: next })}
              />
            </Field>

            <Field label="Visibility" htmlFor="gh-visibility">
              <Select id="gh-visibility" value={defaults.default_visibility}
                      disabled={busy === "defaults"}
                      onChange={(e) => void saveDefaults({ default_visibility: e.target.value })}>
                <option value="private">Private</option>
                <option value="public">Public</option>
              </Select>
            </Field>

            <Field label="Default branch" htmlFor="gh-branch">
              <Row style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <Input id="gh-branch" value={branch} autoComplete="off"
                       onChange={(e) => setBranchDraft(e.target.value)} />
                <Button size="sm" loading={busy === "defaults"}
                        disabled={!branchDirty || !branch.trim() || busy === "defaults"}
                        onClick={() => void saveDefaults({ default_branch_name: branch.trim() })}>
                  Save
                </Button>
              </Row>
            </Field>
            </>}
          </Stack>
        </Card>
      )}

      {/* The manifest leaves the browser as a form POST because the forge
          replies with a page the operator has to read and approve. Its action
          and its one field are filled in from the backend's answer at the
          moment of the click, so no state nonce is minted until it is used. */}
      <form ref={manifestForm} method="post" hidden>
        <input ref={manifestField} type="hidden" name="manifest" />
      </form>

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
