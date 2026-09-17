import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Badge, Button, Card, CardGrid, Notice, PageHeader, Row, SectionLabel, Select,
  Skeleton, Stack, StateBlock, StatusDot, Tag, catFor, shortHttpError,
} from "@workshop/ui";
import { MessageSquare, RefreshCw, Shapes, ShieldCheck } from "lucide-react";

import DelegatesSection from "../components/DelegatesSection";
import { queryKeys } from "../lib/queryKeys";
import {
  ARMED_VALUES, armedGloss, armedName, armedStatus, armingBody, armingSummary,
  assignedThreads, isArmed, roleUsage, rolesFromCatalog, writesGloss,
  type Armed, type ArmableThread, type RoleDefinition,
} from "../lib/roles";
import type { components } from "../api/schema.gen";

// HOME > ROLES. The surface that replaced the Agents roster.
//
// WHAT THE ROSTER WAS AND WHY IT IS GONE
// (docs/platform-redesign/threads-branches-and-pages-2026-08-29.md sections 1,
// 2 and 6). `/agents` listed markdown files on disk as if they were live
// entities. They were not: four declared agents had run once between them in
// six weeks, while everything that actually did unattended work showed up in
// Jobs and never in Agents. The roster and the reality had no overlap at all.
//
// So there is no second entity here. A thread is the one entity, every thread
// is chattable, and "agent" is a description of a thread whose `armed` is not
// `off`. This page owns the two fields that description is made of, and it
// answers two questions the roster could not:
//
//   1. WHAT RUNS UNASKED. Threads that are armed, with the control that arms
//      them on the row.
//   2. WHAT A ROLE IS. The definitions a thread can point at, with what each
//      one may write and how many threads point at it.
//
// The second list is definitions and the first is threads, and they are
// deliberately not merged: a role nothing points at is still a role, and a
// thread with no role is still armed. Neither implies the other.
//
// The pure rules (which threads are listed, in what order, what a value means,
// what the write body is) are in lib/roles.ts and are executed by
// roles.test.ts. This file is arrangement and I/O.

// The catalog of shipped role definitions. Section 2: "the existing
// template/fork machinery is the closest thing already built", and a template
// is exactly `{ name, prompt, tools, writes }` on disk. What changes is what
// the operator does with one: ASSIGNMENT REPLACES FORKING. A thread points at
// a definition; it does not copy one, so editing the definition reaches every
// thread that names it, and there is nothing to keep in sync.
const CATALOG_URL = "/api/agents/templates";

// A thread's own page, the same address the chat list opens.
function threadHref(t: ArmableThread): string {
  const key = t.context_key || "general";
  return `/chat/${encodeURIComponent(key)}?thread=${encodeURIComponent(t.id)}`;
}

// OVERFLOW. The fourth list state. This is a working list, not a browse
// surface: past a couple of dozen armed threads the question stops being "which
// one" and becomes "why are there so many", and the flat recency list on Chats
// is the better tool for that. So the list caps and says what it capped.
const THREAD_LIMIT = 24;

type ArmingChange = { armed?: Armed; role?: string | null };

/** One row: what the thread is, whether it runs unasked, and the role it
 *  answers as.
 *
 *  BOTH CONTROLS COMMIT ON CHANGE, on the same terms as the chat's mode
 *  switch: one field, immediately reversible, and stated back by the row it
 *  sits on. An Edit mode over two selects would be ceremony over a setting the
 *  operator flips while reading the list. A rejected write is a Notice on the
 *  row, never a region: the row is still on screen.
 */
function ThreadRow({
  thread, roles, onChange,
}: {
  thread: ArmableThread;
  roles: RoleDefinition[];
  onChange: (id: string, change: ArmingChange) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const armed = thread.armed;
  const role = (thread.role || "").trim();
  // A thread can name a role the catalog does not carry (a definition that was
  // renamed or removed). The option is kept so the select shows the truth
  // rather than silently reading as "no role" over a thread that has one.
  const orphanRole = role && !roles.some((r) => r.key === role) ? role : null;

  const apply = async (change: ArmingChange) => {
    setBusy(true);
    setErr(null);
    try {
      await onChange(thread.id, change);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <Stack gap="sm">
        <Row between wrap style={{ gap: "var(--space-3)" }}>
          <Link to={threadHref(thread)} style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
            <MessageSquare size={14} strokeWidth={1.8} aria-hidden />
            {thread.title || "New chat"}
          </Link>
          <StatusDot status={armedStatus(armed)}>{armedName(armed)}</StatusDot>
        </Row>
        <p className="ws-card__meta ws-muted">
          {thread.scope || thread.context_key || "workshop"}
          {" · "}
          {armedGloss(armed)}
        </p>
        <Row wrap style={{ gap: "var(--space-3)" }}>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
            {/* NOT "Armed". That is the COLUMN's name, and printing a
                database field at the operator is how it reached him: "armed is
                not like a normal way to say it. That's I didn't approve that
                phrasing... that's not a normal term" (2026-09-17). The control
                sets what advances a thread with nobody watching, so it says
                that, in the words this page already uses for it ("Threads that
                run unasked", "Nothing runs unasked"). The four option labels
                were already plain and are unchanged: Off, On message, On a
                schedule, On a ready ticket. */}
            <span className="ws-muted">Runs on its own</span>
            <Select
              aria-label={`What makes ${thread.title || thread.id} run on its own`}
              value={armed || "off"}
              disabled={busy}
              onChange={(e) => void apply({ armed: e.target.value as Armed })}
            >
              {ARMED_VALUES.map((v) => (
                <option key={v} value={v}>{armedName(v)}</option>
              ))}
            </Select>
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
            <span className="ws-muted">Role</span>
            <Select
              aria-label={`Role for ${thread.title || thread.id}`}
              value={role}
              disabled={busy}
              // ONLY the role changes here. `armingBody` omits what was not
              // asked about, so flipping the role cannot clear the arming and
              // flipping the arming cannot clear the role.
              onChange={(e) => void apply({ role: e.target.value || null })}
            >
              <option value="">No role</option>
              {orphanRole && <option value={orphanRole}>{orphanRole} (not in the catalog)</option>}
              {roles.map((r) => (
                <option key={r.key} value={r.key}>{r.name}</option>
              ))}
            </Select>
          </label>
        </Row>
        {err && (
          <Notice tone="error" detail={err}>
            That change was not saved.
          </Notice>
        )}
      </Stack>
    </Card>
  );
}

export default function Roles() {
  const qc = useQueryClient();

  // THE THREADS, on the shared key. `GET /api/chat/threads` has one owner
  // (lib/queryKeys), so this page reads the same cache entry the dashboard and
  // the Chats page do, and an arming write invalidates all three at once.
  const threadsQuery = useQuery({
    queryKey: queryKeys.chat.threads(null),
    queryFn: async (): Promise<ArmableThread[]> => {
      const r = await fetch("/api/chat/threads");
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return (await r.json()) as ArmableThread[];
    },
    retry: false,
  });

  // THE CATALOG. Read once on mount and after an explicit reload: a definition
  // is a file, it does not change while the page is open, and polling it would
  // be a request every fifteen seconds for an answer that is the same every
  // time.
  const [catalog, setCatalog] = useState<RoleDefinition[] | null>(null);
  const [catalogErr, setCatalogErr] = useState<string | null>(null);

  const loadCatalog = useCallback(async () => {
    setCatalogErr(null);
    try {
      const r = await fetch(CATALOG_URL);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setCatalog(rolesFromCatalog((await r.json()) as components["schemas"]["AgentTemplatesOut"]));
    } catch (e) {
      setCatalog(null);
      setCatalogErr(String(e));
    }
  }, []);

  useEffect(() => { void loadCatalog(); }, [loadCatalog]);

  const threads = threadsQuery.data ?? null;
  const roles = catalog ?? [];
  const listed = useMemo(() => assignedThreads(threads ?? []), [threads]);
  const summary = useMemo(() => armingSummary(threads ?? []), [threads]);

  const setArming = useCallback(async (id: string, change: ArmingChange) => {
    const r = await fetch(`/api/chat/threads/${encodeURIComponent(id)}/arming`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(armingBody(change)),
    });
    if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
    // One invalidation over the whole `["chat", "threads"]` prefix, which is
    // what reaches every copy of the list including the archive's.
    await qc.invalidateQueries({ queryKey: queryKeys.chat.threads(null) });
  }, [qc]);

  return (
    <div>
      <PageHeader
        title="Roles"
        sub={
          threads
            ? `${summary.armed} of ${summary.total} threads run unasked · ${summary.withRole} carry a role`
            : "Loading"
        }
      />

      <SectionLabel>
        <ShieldCheck size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
        Threads that run unasked
      </SectionLabel>
      <p className="ws-muted" style={{ marginBottom: "var(--space-4)" }}>
        One setting says what advances a thread without you. A role says which
        definition it answers as. They are independent: a thread can run on its
        own with no role, or carry a role and never run on its own.
      </p>

      {threadsQuery.isError ? (
        <StateBlock
          variant="error"
          title="Couldn't load threads"
          hint={<code>GET /api/chat/threads</code>}
          action={
            <Button size="sm" variant="ghost" onClick={() => void threadsQuery.refetch()}>
              <RefreshCw aria-hidden /> Retry
            </Button>
          }
        />
      ) : !threads ? (
        <Stack><Skeleton /><Skeleton /><Skeleton /></Stack>
      ) : listed.length === 0 ? (
        <StateBlock
          title="Nothing runs unasked"
          hint={
            <span>
              Every thread waits for you. Open a chat and set its arming, or arm
              one from <Link to="/chats">Chats</Link>.
            </span>
          }
        />
      ) : (
        <Stack>
          {listed.slice(0, THREAD_LIMIT).map((t) => (
            <ThreadRow key={t.id} thread={t} roles={roles} onChange={setArming} />
          ))}
          {listed.length > THREAD_LIMIT && (
            <p className="ws-muted">
              Showing {THREAD_LIMIT} of {listed.length}.{" "}
              <Link to="/chats">Every thread is on Chats</Link>.
            </p>
          )}
        </Stack>
      )}

      <SectionLabel>
        <Shapes size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
        Role definitions
      </SectionLabel>
      <p className="ws-muted" style={{ marginBottom: "var(--space-4)" }}>
        A role is a definition, edited in one place. A thread points at one, it
        does not copy one, so an edit here reaches every thread that names it.
      </p>

      {catalogErr ? (
        <StateBlock
          variant="error"
          title="Couldn't load role definitions"
          hint={<code>GET {CATALOG_URL}</code>}
          action={
            <Button size="sm" variant="ghost" onClick={() => void loadCatalog()}>
              <RefreshCw aria-hidden /> Retry
            </Button>
          }
        />
      ) : !catalog ? (
        <Stack><Skeleton /><Skeleton /></Stack>
      ) : roles.length === 0 ? (
        <StateBlock
          title="No role definitions"
          hint={
            <span>
              Add a definition to <code>agents/_templates/</code> and it appears
              here. A thread with no role is not broken: it continues with its
              own context.
            </span>
          }
        />
      ) : (
        <CardGrid>
          {roles.map((r) => {
            const usage = roleUsage(threads ?? [], r.key);
            const gloss = writesGloss(r.writes);
            return (
              <li key={r.key}>
                <Card>
                  <Row between>
                    <h2 className="ws-card__title">{r.name}</h2>
                    {/* Write authority is the closed enum and a permission, so
                        it is categorical, not a status. A definition that
                        declares none renders NOTHING: an absence must not read
                        as a permission nobody granted. */}
                    {r.writes && <Tag hue={catFor(`role:${r.writes}`)}>{r.writes}</Tag>}
                  </Row>
                  {r.purpose && <p className="ws-card__meta">{r.purpose}</p>}
                  {gloss && <p className="ws-card__meta ws-muted">{gloss}</p>}
                  <Row wrap style={{ gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
                    <Badge>
                      {usage.threads} {usage.threads === 1 ? "thread" : "threads"}
                    </Badge>
                    {/* Only when there is something to say. Zero on a role
                        nothing points at is not news; zero on a role five
                        threads carry is. */}
                    {usage.threads > 0 && (
                      <Badge>{usage.armed} run unasked</Badge>
                    )}
                  </Row>
                </Card>
              </li>
            );
          })}
        </CardGrid>
      )}

      {/* WHO CAN BE CALLED, as opposed to who runs. This was a section of the
          Agents roster and it is the only surface that lists the native
          subagents reached through delegation, which have no trigger and no
          autonomy of their own and so are not threads. Deleting the roster
          orphaned it (caught by scripts/check_dead_surface.py), and a
          capability does not stop existing because the page it was on did. It
          belongs here on the same grounds section 2 gives: a role is a
          definition, edited in one place, and a delegate is a definition. */}
      <DelegatesSection />
    </div>
  );
}

// Exported for the shell's place list: the count that belongs beside "Roles" is
// how much runs unasked, which is a property of threads and not of definitions.
export function armedThreadCount(threads: readonly ArmableThread[]): number {
  return threads.filter((t) => isArmed(t.armed)).length;
}
