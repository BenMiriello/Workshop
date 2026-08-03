import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessagesSquare, Plus } from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  PageHeader,
  Segmented,
  Skeleton,
  StateBlock,
  Tag,
  catFor,
  type SegmentedOption,
} from "../ui";
import { chatApi, type ThreadSummary } from "../lib/chat";
import { newChatHref } from "../lib/chatSelection";

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

function contextLabel(key: string): string {
  if (!key || key === "general") return "Workshop";
  if (!key.includes(":")) return `App · ${key}`; // app chats bind a bare app id
  const [kind, rest] = key.split(":", 2);
  if (kind === "project") return `Project · ${rest}`;
  // Both build: and agent: are Agent scopes; the runtime distinction is
  // plumbing per entity-model 2026-07-18 §6. Label them the same in the
  // chat list; the AgentDetail is where runtime shows.
  if (kind === "build" || kind === "agent") return `Agent · ${rest}`;
  if (kind === "app") return `App · ${rest}`;
  return key;
}

// Classify a context key into scope kinds (per entity-model §2 scope
// addressing: workshop / project:<slug> / app:<name> / agent:<slug>). App chats
// bind a BARE app id (finance, budget, ...), not `app:<id>`; a bare non-general
// key is an app. build: and agent: both collapse to "agent" — they are two
// runtimes of the same scope kind.
type ContextKind = "workshop" | "project" | "agent" | "app" | "other";
function contextKind(key: string): ContextKind {
  if (!key || key === "general") return "workshop";
  if (!key.includes(":")) return "app"; // bare app id
  const kind = key.split(":", 1)[0];
  if (kind === "project" || kind === "app") return kind;
  if (kind === "build" || kind === "agent") return "agent";
  return "other";
}

// Scope-kind view filter (C17). Commits on change (a view preference).
type FilterKind = "all" | "workshop" | "project" | "app" | "agent";
const KIND_OPTIONS: SegmentedOption<FilterKind>[] = [
  { value: "all", label: "All" },
  { value: "workshop", label: "Workshop" },
  { value: "project", label: "Projects" },
  { value: "app", label: "Apps" },
  { value: "agent", label: "Agents" },
];

// Cap the rendered list so a large history never mounts unbounded (design rule
// 9, overflow state). The filter narrows the list toward the cap.
const RENDER_CAP = 100;

export default function Chats() {
  const nav = useNavigate();
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<FilterKind>("all");

  const load = useCallback(() => {
    setThreads(null);
    setErr(null);
    chatApi
      .listThreads()
      .then(setThreads)
      .catch((e) => {
        setErr(String(e));
        setThreads([]);
      });
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const newChat = () => nav(newChatHref("general"));

  // Most-recent first, then apply the context-kind and text filters.
  const sorted = useMemo(
    () =>
      threads
        ? [...threads].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
        : [],
    [threads],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sorted.filter((t) => {
      if (kind !== "all") {
        const k = contextKind(t.context_key);
        // P3: the Agent filter matches BOTH context_key-derived agent chats
        // (legacy shape) AND any chat carrying an agent as a participant
        // under the new model. That lets a workshop-scope chat with agent:X
        // as a participant still surface here.
        const hasAgentParticipant = (t.participants ?? []).length > 0;
        if (kind === "agent") {
          if (k !== "agent" && !hasAgentParticipant) return false;
        } else if (k !== kind) {
          return false;
        }
      }
      if (!needle) return true;
      const participantMatch = (t.participants ?? []).some(
        (p) => p.toLowerCase().includes(needle),
      );
      return (
        (t.title || "").toLowerCase().includes(needle) ||
        (t.last_message || "").toLowerCase().includes(needle) ||
        contextLabel(t.context_key).toLowerCase().includes(needle) ||
        participantMatch
      );
    });
  }, [sorted, q, kind]);

  const visible = filtered.slice(0, RENDER_CAP);

  const open = (t: ThreadSummary) =>
    nav(`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`);

  return (
    <div>
      <PageHeader
        title="Chats"
        actions={
          <Button variant="primary" onClick={newChat}>
            <Plus aria-hidden /> New chat
          </Button>
        }
      />

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-4)",
          alignItems: "flex-end",
          marginBottom: "var(--space-4)",
        }}
      >
        <div style={{ flex: "1 1 260px", maxWidth: 420 }}>
          <Field label="Search">
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by title, context, or last message"
              spellCheck={false}
            />
          </Field>
        </div>
        <Field label="Context">
          <Segmented
            ariaLabel="Filter by context"
            options={KIND_OPTIONS}
            value={kind}
            onChange={setKind}
          />
        </Field>
      </div>

      {threads === null ? (
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 64 }} />
          ))}
        </div>
      ) : err ? (
        <StateBlock
          variant="error"
          title="Couldn't load chats"
          hint={<code>GET /api/chat/threads: {err}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : threads.length === 0 ? (
        <StateBlock
          title="No chats yet"
          hint="Start a conversation with the general assistant, or open chat inside any app or project."
          action={
            <Button variant="primary" size="sm" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <StateBlock title="No matches" hint="No chat matches that filter. Clear the search or pick All to see everything." />
      ) : (
        <>
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            {visible.map((t) => (
              <Card
                key={t.id}
                interactive
                compact
                className="ws-chat-card"
                onClick={() => open(t)}
                // min-width:0 is what every other card grid sets on its items
                // (see .ws-*-grid > li in components.css). This grid is a raw
                // inline div, so the card must carry it itself, else a long
                // context tag or snippet sets the card's min-content width and
                // pushes it past the viewport on mobile (horizontal scroll).
                style={{ cursor: "pointer", minWidth: 0 }}
              >
                {/* One line: icon + title ellipsize, tag + count never shrink.
                    overflow:hidden + min-width:0 keep a long title/context from
                    wrapping the row tall or overflowing the viewport on mobile. */}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0, overflow: "hidden" }}>
                  <MessagesSquare size={14} aria-hidden style={{ flex: "none" }} />
                  <strong style={{ minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {t.title || "New chat"}
                  </strong>
                  <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flex: "none", marginLeft: "auto" }}>
                    <Tag hue={catFor(t.context_key)}>{contextLabel(t.context_key)}</Tag>
                    {/* P3: participants column — every agent in this chat as
                        a small tag. An agent is a PARTICIPANT of a chat, not
                        the chat's home; this row makes that visible instead
                        of collapsing "agent chats" into a distinct scope. */}
                    {(t.participants ?? []).slice(0, 3).map((p) => {
                      const slug = p.startsWith("agent:") ? p.slice(6) : p;
                      return (
                        <Tag key={p} hue={catFor(`part:${slug}`)}>
                          {slug}
                        </Tag>
                      );
                    })}
                    {(t.participants ?? []).length > 3 && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                        +{(t.participants ?? []).length - 3}
                      </span>
                    )}
                    <span className="ws-card__meta">{t.message_count}</span>
                  </span>
                </div>
                {/* Always rendered (even when empty) so every card reserves the
                    same 2-line height; the clamp trims longer snippets with an
                    ellipsis instead of growing the card. */}
                <p className="ws-muted ws-chat-card__snippet">{t.last_message || " "}</p>
              </Card>
            ))}
          </div>
          {filtered.length > RENDER_CAP && (
            <p className="ws-muted" style={{ margin: "var(--space-3) 0 0", textAlign: "center" }}>
              Showing {RENDER_CAP} of {filtered.length}. Refine the search or context filter to narrow the list.
            </p>
          )}
        </>
      )}
    </div>
  );
}
