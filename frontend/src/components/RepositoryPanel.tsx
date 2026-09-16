import { useCallback, useEffect, useState } from "react";
import { GitBranch, Plus, Link as LinkIcon } from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  Notice,
  Prop,
  Props,
  SectionLabel,
  Segmented,
  Skeleton,
  Stack,
  StateBlock,
  StatusDot,
  shortHttpError,
} from "@workshop/ui";

// The app Overview tab's repository panel: is this app's working tree connected
// to a remote, and the two operations that connect it.
//
// WHY IT EXISTS. `POST /api/projects/{slug}/connect-github` has been live and
// unreachable since ProjectDetail.tsx was deleted (ticket d12a0332): a working
// endpoint behind no button, and an app that could no longer be connected to a
// repository from the interface. This is the "wire it" half of that ticket's
// choice. The alternative it rejects is deleting the route, which would have
// removed the only path this product has to create a private repo for an app.
//
// CREATE AND LINK ARE TWO OPERATIONS, NOT ONE WITH A FLAG. Every tool that
// fused them ends up telling people to delete their remote by hand:
// app-separation-and-github-2026-08-20.md section 3.5. The segmented control
// says which one is about to happen before it happens.
//
// The credential is the account connected in Settings under Apps; nothing here
// asks for one, and a failure that is really "not signed in" comes back from
// the backend as its own sentence rather than as a git error.

type Repo = {
  provider: string;
  owner: string;
  name: string;
  remote_url: string;
  default_branch: string;
  connected_at: string;
};

type Project = { slug: string; repo?: Repo | null };

type Mode = "create" | "bind";

export default function RepositoryPanel({ slug }: { slug: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("create");
  const [remoteUrl, setRemoteUrl] = useState("");

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setProject((await r.json()) as Project);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [slug]);
  useEffect(() => { void load(); }, [load]);

  const connect = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/connect-github`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          mode === "create"
            ? { mode: "create" }
            : { mode: "bind", remote_url: remoteUrl.trim() }),
      });
      const parsed = await r.json().catch(() => null);
      if (!r.ok) {
        const d = (parsed as { detail?: unknown } | null)?.detail;
        throw new Error(typeof d === "string" ? d
          : shortHttpError(r.status, r.statusText, ""));
      }
      setProject(parsed as Project);
      setRemoteUrl("");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, mode, remoteUrl]);

  const repo = project?.repo ?? null;

  // Why Connect cannot be pressed, in words, or null when it can. One value
  // drives both the disabled flag and the sentence, so a greyed button always
  // carries the reason it is grey.
  const blocker = mode === "bind" && !remoteUrl.trim()
    ? "A remote URL is required to link an existing repository."
    : null;

  return (
    <section>
      <SectionLabel icon={<GitBranch aria-hidden />}>Repository</SectionLabel>

      {loadError ? (
        <StateBlock
          variant="error"
          title="Couldn't load the repository connection"
          hint={`${loadError} (/api/projects/${slug})`}
          action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
        />
      ) : !project ? (
        <Card><Stack gap="sm"><Skeleton width="40%" /><Skeleton width="25%" /></Stack></Card>
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

            {repo ? (
              <Stack gap="sm">
                <StatusDot status="ok">Connected</StatusDot>
                <Props>
                  <Prop label="Repository">
                    <a href={repo.remote_url.replace(/\.git$/, "")}
                       target="_blank" rel="noreferrer" className="ws-mono">
                      {repo.owner}/{repo.name}
                    </a>
                  </Prop>
                  <Prop label="Default branch">
                    <span className="ws-mono">{repo.default_branch}</span>
                  </Prop>
                </Props>
              </Stack>
            ) : (
              <Stack gap="sm">
                <StatusDot status="stopped">Not connected</StatusDot>
                <Field label="How">
                  <Segmented
                    value={mode}
                    onChange={(v) => setMode(v as Mode)}
                    options={[
                      { value: "create", label: "Create a new private repo" },
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
                  <Button size="sm" variant="primary" loading={busy}
                          disabled={!!blocker || busy}
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
    </section>
  );
}
