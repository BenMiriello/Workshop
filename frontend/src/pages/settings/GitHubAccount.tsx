import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, FolderGit2, GitPullRequest, Link2, Link2Off } from "lucide-react";
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
import { Installation } from "./GitHubInstallation";

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
// CONNECT IS ONE BUTTON. It starts GitHub's device sign-in against the
// product's own application: this card shows a short code, the operator
// approves it on github.com, and the card finishes by itself. Then the app is
// installed on the account (GitHubInstallation.tsx), which is what gives the
// sign-in its reach. Nobody registers an application or pastes a token.
//
// THE TOKEN NEVER REACHES THIS PAGE. Every field rendered here is a login, a
// scope name, a timestamp or a boolean.

const ENDPOINT = "/api/github";

// Named from the backend's own schema, never restated here: what this screen
// may know about the connection is a decision the route makes, and a copy of it
// would be the place the two drift apart.
type Defaults = components["schemas"]["DefaultsOut"];
type Status = components["schemas"]["StatusOut"];
type Device = components["schemas"]["DeviceStartOut"];

const SOURCE_LABEL: Record<string, string> = {
  pat: "Personal access token",
  device: "Signed in with GitHub",
  oauth: "Signed in with GitHub",
};

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

  const [branchDraft, setBranchDraft] = useState<string | null>(null);
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

  // RE-READ WHEN THIS PAGE COMES BACK INTO VIEW. The approval happens on
  // github.com, often in the system browser (the native shell hands any other
  // origin to it), so coming back is the moment the connection may have
  // changed. One GET that returns no secret.
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

  // ---- connecting -------------------------------------------------------------
  // The operator reads a code here, enters it on github.com, and this polls until
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

                {data.scopes.length > 0 && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    Scopes: {data.scopes.join(", ")}.
                  </span>
                )}

                {data.installation && (
                  <Installation
                    installation={data.installation}
                    installUrl={data.install_url}
                    checking={busy === "check"}
                    onCheck={async () => { setBusy("check"); await load(); setBusy(null); }}
                  />
                )}

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
                <StatusDot status="stopped">Not connected</StatusDot>

                {device ? (
                  <Stack gap="sm">
                    <Field label="Your code" help="Enter it on github.com and approve. This page finishes by itself.">
                      <CopyButton value={device.user_code} />
                    </Field>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <Button size="sm" variant="primary"
                              onClick={() => window.open(device.verification_uri, "_blank", "noopener")}>
                        <ExternalLink aria-hidden /> Open GitHub
                      </Button>
                      <Button size="sm" variant="ghost" onClick={cancelDevice}>Cancel</Button>
                    </Row>
                    {devicePending && <Notice tone="info">{devicePending}</Notice>}
                  </Stack>
                ) : data.device_available ? (
                  <Row>
                    <Button size="sm" variant="primary" loading={busy === "start"}
                            disabled={busy === "start"}
                            onClick={() => void startDevice()}>
                      <Link2 aria-hidden /> Connect GitHub
                    </Button>
                  </Row>
                ) : (
                  <Notice tone="warn">This installation has no GitHub application to sign in with.</Notice>
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
