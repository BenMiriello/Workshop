import { useCallback, useEffect, useState } from "react";
import { Link2, Link2Off, RefreshCw, Check, X, Clock, Inbox } from "lucide-react";
import { Button, Card, ConfirmDialog, Field, Input, PageHeader, Row, SectionLabel, Select, Skeleton, Stack, StateBlock, StatusDot, StatusPill, Tag, catFor, shortHttpError } from "@workshop/ui";
import { scopeLabel } from "../../lib/tasks";

// Settings -> Trackers: the one external ticket integration, and its triage.
//
// Design: docs/platform-redesign/ticket-lifecycle-and-integration-2026-08-10.md
//
// There is deliberately almost nothing to configure. One provider connection for
// the system (not one per project), per-scope targets inside it, and one mode
// knob. Everything else that looked like a setting collapsed into a decision
// already made: which tickets publish is answered by approval, whether drafts
// publish is answered by "a draft is not work yet", and whether to delete remote
// issues on disconnect is answered by "never".

type Connection = {
  provider: string | null;
  credential_source: string;
  mode: string;
  filter: { kind: string; label: string; query: string };
  targets: Record<string, string>;
  cursors: Record<string, string>;
  connected_at: string | null;
  last_poll: string | null;
  last_error: string | null;
};

type Credential = {
  source: string;
  ready: boolean;
  detail: string;
  scopes: string[];
};

// How the credential is obtained. The storage for a pasted token existed from
// the start and nothing could reach it: the connect form never sent
// `credential_source`, so every binding quietly used the local `gh` login.
const SOURCE_LABEL: Record<string, string> = {
  gh: "Local gh CLI login",
  pat: "Personal access token",
  device_flow: "Sign in with GitHub",
};

type Candidate = {
  id: string;
  scope: string;
  provider: string;
  key: string;
  url: string;
  title: string;
  labels: string[];
  remote_state: string;
  state: string;
};

type Payload = {
  connection: Connection;
  credential: Credential | null;
  providers_built: string[];
  modes: string[];
  credential_sources: Credential[];
  triage: Record<string, number>;
};

const MODE_HELP: Record<string, string> = {
  off: "Connected but idle. Nothing is read or written.",
  import: "Read only. The tracker belongs to someone else; the only thing written back is a run summary.",
  sync: "Two way. The tracker owns the wording; workshop owns status and run history.",
};

export default function Trackers() {
  const [data, setData] = useState<Payload | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const [keepImported, setKeepImported] = useState(true);

  // Connect form
  const [provider, setProvider] = useState("github");
  const [target, setTarget] = useState("");
  const [scope, setScope] = useState("workshop");
  const [mode, setMode] = useState("import");
  const [scopes, setScopes] = useState<string[]>([]);
  const [credSource, setCredSource] = useState("gh");
  // Write only. Held just long enough to submit, then dropped: this value never
  // comes back from the server, and nothing here should keep a copy of it.
  const [token, setToken] = useState("");
  const [confirmForget, setConfirmForget] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [c, t, s] = await Promise.all([
        fetch("/api/trackers").then((r) => r.json()),
        fetch("/api/trackers/triage").then((r) => r.json()),
        fetch("/api/tasks/scopes").then((r) => (r.ok ? r.json() : { scopes: [] })),
      ]);
      setData(c);
      setCandidates(t.candidates ?? []);
      setScopes(s.scopes ?? []);
      if (c?.connection?.mode) setMode(c.connection.mode);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const act = useCallback(async (key: string, url: string, body?: unknown,
                                 method: "POST" | "PUT" | "DELETE" = "POST") => {
    setBusy(key);
    setNote(null);
    try {
      const r = await fetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      await load();
      return j;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setBusy(null);
    }
  }, [load]);

  const conn = data?.connection;
  const connected = !!conn?.provider;

  // WHY CONNECT CANNOT BE PRESSED, in words, or null when it can. One value
  // drives both the `disabled` flag and the sentence under the button, so the
  // control and its explanation cannot disagree: a greyed button with no reason
  // beside it is a dead end, and the reason drifting from the condition is how
  // it becomes a wrong one.
  const connectBlocker =
    !target.trim()
      ? "A repo is required. Type owner/repo above."
      : credSource === "pat" && !token.trim()
        ? "A token is required for this credential source."
        : null;

  return (
    <div>
      <PageHeader title="Trackers" />

      {err && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <StateBlock variant="error" title="Tracker error" hint={err}
                      action={<Button size="sm" onClick={() => { setErr(null); void load(); }}>Retry</Button>} />
        </div>
      )}
      {note && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{note}</p>
      )}

      {!data ? (
        <Card><Stack gap="sm"><Skeleton width="50%" /><Skeleton width="30%" /></Stack></Card>
      ) : (
        <Stack>
          {/* The connection ------------------------------------------------ */}
          <section>
            <SectionLabel icon={<Link2 aria-hidden />}>Connection</SectionLabel>
            <Card>
              {!connected ? (
                <Stack gap="sm">
                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                    One external ticket service at a time. Scopes point at their own
                    targets inside it.
                  </span>
                  {/* A FIXED TWO-COLUMN GRID, not a wrapping row.
                      This was one `Row wrap` with `alignItems: flex-end`
                      holding six inline fields. Only Repo carries a help line,
                      so Repo is taller than its siblings, and in a wrapping
                      flex-end row a taller item both misaligns its neighbours
                      and forces the break at whatever point it happens to
                      reach: on a desktop Provider and Scope sat on line one,
                      Repo was flung to the right on a half line ABOVE them, and
                      Mode and Credential landed on line two. A grid gives every
                      field the same cell whatever it contains.

                      Each explanation now sits on the field it explains. The
                      mode sentence and the credential sentence used to float
                      under the whole row, describing controls three positions
                      away. */}
                  <div className="ws-trackers__form">
                    <Field label="Provider">
                      <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                        {data.providers_built.map((p) => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Scope">
                      <Select value={scope} onChange={(e) => setScope(e.target.value)}>
                        {(scopes.length ? scopes : ["workshop"]).map((s) => (
                          <option key={s} value={s}>{scopeLabel(s)}</option>
                        ))}
                      </Select>
                    </Field>
                    <Field label="Repo" required help="owner/repo. Chosen, never guessed.">
                      <Input value={target} placeholder="owner/workshop-tickets"
                             onChange={(e) => setTarget(e.target.value)} />
                    </Field>
                    <Field label="Mode" help={MODE_HELP[mode]}>
                      <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                        {data.modes.filter((m) => m !== "off").map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </Select>
                    </Field>
                    <Field
                      label="Credential"
                      help={data.credential_sources.find((s) => s.source === credSource)?.detail}
                    >
                      <Select value={credSource} onChange={(e) => setCredSource(e.target.value)}>
                        {data.credential_sources.map((s) => (
                          <option key={s.source} value={s.source}>
                            {SOURCE_LABEL[s.source] ?? s.source}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {credSource === "pat" && (
                      <Field label="Token" required help="Stored write only. It is never shown again.">
                        <Input type="password" value={token} placeholder="ghp_..."
                               autoComplete="off"
                               onChange={(e) => setToken(e.target.value)} />
                      </Field>
                    )}
                    {/* THE REQUIREMENT IS ON THE BUTTON'S OWN FIELD. Connect
                        was greyed with nothing anywhere saying why, so the only
                        way to learn that a repo is required was to type one. A
                        disabled control has to carry the sentence that
                        un-disables it. */}
                    <Field className="ws-trackers__form-actions" help={connectBlocker}>
                    <Button size="sm" variant="primary" loading={busy === "connect"}
                            disabled={!!connectBlocker || busy === "connect"}
                            onClick={async () => {
                              // The token has to be stored BEFORE connect, because
                              // connect proves the credential works by using it.
                              if (credSource === "pat") {
                                const saved = await act("connect", "/api/trackers/credentials",
                                  { provider, token: token.trim() }, "PUT");
                                if (!saved) return;
                                setToken("");
                              }
                              const j = await act("connect", "/api/trackers/connect",
                                { provider, target: target.trim(), scope, mode,
                                  credential_source: credSource });
                              if (j) setNote(`Connected as ${j.account?.login}. Issues arrive in triage; nothing runs without your approval.`);
                            }}>
                      Connect
                    </Button>
                    </Field>
                  </div>
                </Stack>
              ) : (
                <Stack gap="sm">
                  <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
                    <Tag hue={catFor(conn.provider!)}>{conn.provider}</Tag>
                    <StatusPill status={conn.mode === "off" ? "stopped" : "ok"}>{conn.mode}</StatusPill>
                    {data.credential && (
                      <StatusDot status={data.credential.ready ? "ok" : "warn"}>
                        <span className="ws-muted">{data.credential.detail}</span>
                      </StatusDot>
                    )}
                  </Row>

                  <Stack gap="sm">
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Targets</span>
                    {Object.entries(conn.targets).map(([sk, tgt]) => (
                      <Row key={sk} style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <Tag hue={catFor(sk)}>{scopeLabel(sk)}</Tag>
                        <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>{tgt}</span>
                        <Button size="sm" variant="ghost" loading={busy === `poll:${sk}`}
                                onClick={() => act(`poll:${sk}`, `/api/trackers/poll?scope=${encodeURIComponent(sk)}`)}>
                          <RefreshCw aria-hidden /> Poll
                        </Button>
                      </Row>
                    ))}
                  </Stack>

                  {/* Credential. Changing where the token comes from used to
                      require a disconnect, which unlinks every ticket in the
                      scope, to change something that is not about tickets. */}
                  <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
                    <Field label="Credential" inline>
                      <Select value={conn.credential_source}
                              onChange={(e) => act("credsource", "/api/trackers/credential-source",
                                { source: e.target.value })}>
                        {data.credential_sources.map((s) => (
                          <option key={s.source} value={s.source}>
                            {SOURCE_LABEL[s.source] ?? s.source}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {conn.credential_source === "pat" && (
                      <>
                        <Field label={data.credential?.ready ? "Replace token" : "Token"} inline
                               help="Stored write only. It is never shown again.">
                          <Input type="password" value={token} placeholder="ghp_..."
                                 autoComplete="off"
                                 onChange={(e) => setToken(e.target.value)} />
                        </Field>
                        <Button size="sm" loading={busy === "token"}
                                disabled={!token.trim() || busy === "token"}
                                onClick={async () => {
                                  const j = await act("token", "/api/trackers/credentials",
                                    { provider: conn.provider, token: token.trim() }, "PUT");
                                  if (j) { setToken(""); setNote(j.detail); }
                                }}>
                          Save token
                        </Button>
                        {data.credential?.ready && (
                          <Button size="sm" variant="danger-ghost"
                                  onClick={() => setConfirmForget(true)}>
                            Forget token
                          </Button>
                        )}
                      </>
                    )}
                  </Row>

                  <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
                    <Field label="Mode" inline>
                      <Select value={conn.mode}
                              onChange={(e) => act("mode", "/api/trackers/mode", { mode: e.target.value })}>
                        {data.modes.map((m) => <option key={m} value={m}>{m}</option>)}
                      </Select>
                    </Field>
                    <Field label="Selection label" inline
                           help="which issues count as workshop work">
                      <Input defaultValue={conn.filter.label}
                             onBlur={(e) => act("filter", "/api/trackers/filter",
                               { kind: "label", label: e.target.value })} />
                    </Field>
                    <Button size="sm" variant="danger-ghost" onClick={() => setConfirmOff(true)}>
                      <Link2Off aria-hidden /> Disconnect
                    </Button>
                  </Row>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {MODE_HELP[conn.mode]}
                  </span>
                  {conn.last_error && (
                    <StatusDot status="danger">
                      <span className="ws-muted">last poll failed: {conn.last_error}</span>
                    </StatusDot>
                  )}
                </Stack>
              )}
            </Card>
          </section>

          {/* Triage --------------------------------------------------------- */}
          {connected && (
            <section>
              <SectionLabel icon={<Inbox aria-hidden />}>Triage</SectionLabel>
              <Card>
                {candidates === null ? (
                  <Skeleton width="40%" />
                ) : candidates.length === 0 ? (
                  <StateBlock
                    title="Nothing waiting"
                    hint={
                      <span>
                        Imported issues land here first. Poll a target above; nothing
                        from a tracker ever starts work on its own.
                      </span>
                    }
                  />
                ) : (
                  <Stack gap="sm">
                    {candidates.map((c) => (
                      <Row key={c.id} wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                        <a href={c.url} target="_blank" rel="noreferrer"
                           className="ws-mono" style={{ fontSize: "var(--fs-xs)" }}>{c.key}</a>
                        <span className="ws-wrap" style={{ flex: 1, fontSize: "var(--fs-sm)" }}>{c.title}</span>
                        <Tag hue={catFor(c.scope)}>{scopeLabel(c.scope)}</Tag>
                        <Button size="sm" variant="primary" loading={busy === `a:${c.id}`}
                                onClick={async () => {
                                  const j = await act(`a:${c.id}`, `/api/trackers/triage/${encodeURIComponent(c.id)}/accept`);
                                  if (j) setNote("Imported as a draft. It needs your approval before anything runs it.");
                                }}>
                          <Check aria-hidden /> Accept
                        </Button>
                        <Button size="sm" variant="ghost" loading={busy === `s:${c.id}`}
                                onClick={() => act(`s:${c.id}`, `/api/trackers/triage/${encodeURIComponent(c.id)}/snooze`, { days: 7 })}>
                          <Clock aria-hidden /> Snooze
                        </Button>
                        <Button size="sm" variant="ghost" loading={busy === `i:${c.id}`}
                                onClick={() => act(`i:${c.id}`, `/api/trackers/triage/${encodeURIComponent(c.id)}/ignore`)}>
                          <X aria-hidden /> Ignore
                        </Button>
                      </Row>
                    ))}
                  </Stack>
                )}
              </Card>
            </section>
          )}
        </Stack>
      )}

      <ConfirmDialog
        open={confirmForget}
        title="Forget the stored token?"
        body={
          <span>
            The token is deleted from this machine and cannot be shown again. The
            connection stays, but it stops working until you paste a new token or
            switch to a different credential.
          </span>
        }
        confirmLabel="Forget token"
        onConfirm={async () => {
          setConfirmForget(false);
          const j = await act("forget",
            `/api/trackers/credentials?provider=${encodeURIComponent(conn?.provider ?? "")}`,
            undefined, "DELETE");
          if (j) setNote("Token deleted.");
        }}
        onCancel={() => setConfirmForget(false)}
      />

      <ConfirmDialog
        open={confirmOff}
        title="Disconnect this tracker?"
        body={
          <Stack gap="sm">
            <span>
              Remote issues are never touched. Every linked ticket keeps a frozen
              reference to what it used to be.
            </span>
            <Field label="Tickets that came from the tracker" inline>
              <Select value={keepImported ? "keep" : "cancel"}
                      onChange={(e) => setKeepImported(e.target.value === "keep")}>
                <option value="keep">Keep them as local tickets</option>
                <option value="cancel">Cancel them (kept on record, not deleted)</option>
              </Select>
            </Field>
          </Stack>
        }
        confirmLabel="Disconnect"
        onConfirm={async () => {
          setConfirmOff(false);
          const j = await act("disconnect", "/api/trackers/disconnect", { keep_imported: keepImported });
          if (j) setNote(`Disconnected. ${j.unlinked} ticket${j.unlinked === 1 ? "" : "s"} unlinked${j.cancelled ? `, ${j.cancelled} cancelled` : ""}.`);
        }}
        onCancel={() => setConfirmOff(false)}
      />
    </div>
  );
}
