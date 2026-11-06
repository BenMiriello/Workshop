import { useCallback, useEffect, useState } from "react";
import { FolderGit2, GitPullRequest, Link2 } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
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
  Toggle,
  shortHttpError,
} from "@workshop/ui";
import type { components } from "../../api/schema.gen";
import { GitHubConnectDialog } from "./GitHubConnectDialog";
import { GitHubConnection } from "./GitHubConnection";

// Settings -> Connectors: the GitHub account this installation pushes and
// opens pull requests as.
//
// WHAT IT FIXES. `git push` from an agent session died with "could not read
// Username for 'https://github.com'" on a repository with 812 unpushed commits,
// and there was no surface anywhere in the product that could fix it: the
// credential lived in a `gh` login on disk that nobody had renewed. Backend
// reasoning is in backend/services/github_identity.py.
//
// CONNECT IS ONE BUTTON. It opens GitHubConnectDialog, which signs in with a
// device code and installs the product's GitHub App on the account. Nobody
// registers an application or pastes a token.
//
// THE TOKEN NEVER REACHES THIS PAGE. Every field rendered here is a login, a
// timestamp, a URL or a boolean.

const ENDPOINT = "/api/github";

// Named from the backend's own schema, never restated here: what this screen
// may know about the connection is a decision the route makes, and a copy of it
// would be the place the two drift apart.
type Defaults = components["schemas"]["DefaultsOut"];
type Status = components["schemas"]["StatusOut"];

function extractDetail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : fallback;
}

export default function GitHubAccount() {
  const [data, setData] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [branchDraft, setBranchDraft] = useState<string | null>(null);

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

  // Installing the app and changing its repositories happen on github.com, so
  // coming back to this page is when the connection may have changed.
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    window.addEventListener("pageshow", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("pageshow", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const send = useCallback(async (
    key: string,
    path: string,
    method: "PUT" | "DELETE",
    body?: unknown,
  ): Promise<Status | null> => {
    setBusy(key);
    setActionError(null);
    try {
      const r = await fetch(`${ENDPOINT}${path}`, {
        method,
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const parsed = await r.json().catch(() => null);
      if (!r.ok) throw new Error(extractDetail(parsed, shortHttpError(r.status, r.statusText, "")));
      setData(parsed as Status);
      return parsed as Status;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }, []);

  // The defaults decide what the NEXT app's repository looks like, so the
  // switches commit on change; the branch name is typed, so it keeps a Save.
  const saveDefaults = useCallback(async (patch: Partial<Defaults>) => {
    if (await send("defaults", "/defaults", "PUT", patch)) setBranchDraft(null);
  }, [send]);

  const retry = useCallback(async () => {
    setBusy("check");
    await load();
    setBusy(null);
  }, [load]);

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
                <Button size="sm" variant="ghost" onClick={() => setActionError(null)}>Dismiss</Button>
              }>
                {actionError}
              </Notice>
            )}

            {data.connected ? (
              <GitHubConnection
                data={data}
                busy={busy}
                onReconnect={() => setConnecting(true)}
                onRetry={() => void retry()}
                onHelper={(on) => void send("helper", "/helper", "PUT", { enabled: on })}
                onDisconnect={() => setConfirmOff(true)}
              />
            ) : (
              <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                <StatusDot status="stopped">Not connected</StatusDot>
                {data.device_available && (
                  <Button size="sm" variant="primary" onClick={() => setConnecting(true)}>
                    <Link2 aria-hidden /> Connect GitHub
                  </Button>
                )}
              </Row>
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

      <GitHubConnectDialog open={connecting} onClose={() => setConnecting(false)} onStatus={setData} />

      <ConfirmDialog
        open={confirmOff}
        title="Disconnect GitHub?"
        body={<span>Workshop and git on this computer stop using {data?.login || "this account"}.</span>}
        confirmLabel="Disconnect"
        onConfirm={async () => {
          setConfirmOff(false);
          await send("disconnect", "", "DELETE");
        }}
        onCancel={() => setConfirmOff(false)}
      />
    </section>
  );
}
