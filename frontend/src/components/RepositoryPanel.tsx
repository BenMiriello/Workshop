import { useCallback, useEffect, useState } from "react";
import { ExternalLink, GitBranch, Link as LinkIcon, Plus, Upload } from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  Notice,
  Row,
  Section,
  Segmented,
  Skeleton,
  Spacer,
  Stack,
  StateBlock,
  StatusDot,
  Toggle,
  shortHttpError,
  type StatusKind,
} from "@workshop/ui";
import type { components } from "../api/schema.gen";

// The app Overview tab's repository panel: the app's GitHub repository, whether
// Workshop keeps it pushed, and how it stands against GitHub.
//
// MANAGED OR LINKED. A repository Workshop created is pushed after each change
// by default; one the app was imported from or linked to is not. The switch is
// the operator's either way. backend/services/project_repo.py has the reasoning.
//
// CREATE AND LINK ARE TWO OPERATIONS, NOT ONE WITH A FLAG. Every tool that
// fused them ends up telling people to delete their remote by hand:
// app-separation-and-github-2026-08-20.md section 3.5. The segmented control
// says which one is about to happen before it happens.

type Repo = components["schemas"]["RepoOut"];
type Mode = "create" | "bind";

function detailOf(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  return typeof d === "string" ? d : fallback;
}

function standing(repo: Repo): { status: StatusKind; text: string } {
  if (repo.push?.state === "diverged" || repo.behind > 0) {
    return { status: "warn", text: "GitHub has changes this app doesn't have" };
  }
  if (repo.push?.state === "failed") return { status: "danger", text: "Push failed" };
  if (repo.ahead > 0) {
    return { status: "stopped", text: repo.ahead === 1 ? "1 change not on GitHub" : `${repo.ahead} changes not on GitHub` };
  }
  return { status: "ok", text: "Up to date" };
}

export default function RepositoryPanel({ slug }: { slug: string }) {
  const base = `/api/projects/${encodeURIComponent(slug)}`;
  const [repo, setRepo] = useState<Repo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("create");
  const [remoteUrl, setRemoteUrl] = useState("");

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const r = await fetch(`${base}/repo`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setRepo((await r.json()) as Repo);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [base]);
  useEffect(() => { void load(); }, [load]);

  const send = useCallback(async (key: string, path: string, method: "POST" | "PUT", body?: unknown) => {
    setBusy(key);
    setActionError(null);
    try {
      const r = await fetch(`${base}${path}`, {
        method,
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const parsed = await r.json().catch(() => null);
      if (!r.ok) throw new Error(detailOf(parsed, shortHttpError(r.status, r.statusText, "")));
      return parsed;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }, [base]);

  const connect = useCallback(async () => {
    const out = await send("connect", "/connect-github", "POST",
      mode === "create" ? { mode: "create" } : { mode: "bind", remote_url: remoteUrl.trim() });
    if (out) { setRemoteUrl(""); await load(); }
  }, [send, load, mode, remoteUrl]);

  const retryCreate = useCallback(async () => {
    setMode("create");
    if (await send("connect", "/connect-github", "POST", { mode: "create" })) await load();
  }, [send, load]);

  const setManaged = useCallback(async (managed: boolean) => {
    const out = await send("managed", "/repo/managed", "PUT", { managed });
    if (out) setRepo(out as Repo);
  }, [send]);

  const pushNow = useCallback(async () => {
    const out = await send("push", "/repo/push", "POST");
    if (out) setRepo(out as Repo);
  }, [send]);

  // Why Connect cannot be pressed, in words, or null when it can.
  const blocker = mode === "bind" && !remoteUrl.trim()
    ? "A remote URL is required to link an existing repository."
    : null;

  return (
    <Section label="Repository" icon={<GitBranch aria-hidden />}>
      {loadError ? (
        <StateBlock
          variant="error"
          title="Couldn't load the repository"
          hint={`${loadError} (${base}/repo)`}
          action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
        />
      ) : !repo ? (
        <Card><Stack gap="sm"><Skeleton width="40%" /><Skeleton width="25%" /></Stack></Card>
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

            {repo.connected ? (
              <Stack gap="sm">
                <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                  <div>
                    <div className="ws-mono"><strong>{repo.owner}/{repo.name}</strong></div>
                    <div className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{repo.default_branch}</div>
                  </div>
                  <Spacer />
                  {repo.web_url && (
                    <Button size="sm" variant="ghost"
                            onClick={() => window.open(repo.web_url, "_blank", "noopener")}>
                      <ExternalLink aria-hidden /> Open on GitHub
                    </Button>
                  )}
                </Row>

                <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                  <StatusDot status={standing(repo).status}>{standing(repo).text}</StatusDot>
                  {(repo.ahead > 0 || repo.push?.state === "failed") && repo.push?.state !== "diverged" && (
                    <Button size="sm" loading={busy === "push"} disabled={busy === "push"}
                            onClick={() => void pushNow()}>
                      <Upload aria-hidden /> Push now
                    </Button>
                  )}
                </Row>
                {repo.push?.state === "failed" && repo.push.detail && (
                  <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{repo.push.detail}</span>
                )}

                <Toggle
                  checked={repo.managed}
                  disabled={busy === "managed"}
                  label="Push changes to GitHub automatically"
                  onChange={(next) => void setManaged(next)}
                />
              </Stack>
            ) : (
              <Stack gap="sm">
                {repo.error ? (
                  <Notice tone="error" action={
                    <Button size="sm" loading={busy === "connect"} onClick={() => void retryCreate()}>
                      Retry
                    </Button>
                  }>
                    Couldn't create the GitHub repository. {repo.error}
                  </Notice>
                ) : (
                  <StatusDot status="stopped">Not connected</StatusDot>
                )}
                <Field label="How">
                  <Segmented
                    value={mode}
                    onChange={(v) => setMode(v as Mode)}
                    options={[
                      { value: "create", label: "Create a new repo" },
                      { value: "bind", label: "Link an existing repo" },
                    ]}
                  />
                </Field>
                {mode === "bind" && (
                  <Field label="Remote URL" required
                         help="The HTTPS URL of a repository you already own.">
                    <Input value={remoteUrl} autoComplete="off"
                           placeholder="https://.../owner/name.git"
                           onChange={(e) => setRemoteUrl(e.target.value)} />
                  </Field>
                )}
                <Field help={blocker}>
                  <Button size="sm" variant="primary" loading={busy === "connect"}
                          disabled={!!blocker || busy === "connect"}
                          onClick={() => void connect()}>
                    {mode === "create"
                      ? <><Plus aria-hidden /> Create and push</>
                      : <><LinkIcon aria-hidden /> Link</>}
                  </Button>
                </Field>
              </Stack>
            )}
          </Stack>
        </Card>
      )}
    </Section>
  );
}
