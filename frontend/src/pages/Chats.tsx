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

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

function contextLabel(key: string): string {
  if (!key || key === "general") return "General";
  if (!key.includes(":")) return `App · ${key}`; // app chats bind a bare app id
  const [kind, rest] = key.split(":", 2);
  if (kind === "project") return `Project · ${rest}`;
  if (kind === "build") return `Builder · ${rest}`;
  if (kind === "agent") return `Agent · ${rest}`;
  if (kind === "app") return `App · ${rest}`;
  return key;
}

// Classify a context key into the coarse buckets the Segmented filter exposes.
// App chats bind a BARE app id (finance, budget, ...), not `app:<id>`; a bare
// non-general key is therefore an app. agent: threads are assistants.
type ContextKind = "general" | "project" | "build" | "agent" | "app" | "other";
function contextKind(key: string): ContextKind {
  if (!key || key === "general") return "general";
  if (!key.includes(":")) return "app"; // bare app id
  const kind = key.split(":", 1)[0];
  if (kind === "project" || kind === "build" || kind === "agent" || kind === "app") return kind;
  return "other";
}

// The context-kind view filter. Commits on change (a view preference).
type FilterKind = "all" | "general" | "project" | "build" | "agent" | "app";
const KIND_OPTIONS: SegmentedOption<FilterKind>[] = [
  { value: "all", label: "All" },
  { value: "general", label: "General" },
  { value: "project", label: "Projects" },
  { value: "build", label: "Builders" },
  { value: "agent", label: "Assistants" },
  { value: "app", label: "Apps" },
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

  const newChat = () => nav("/chat/general");

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
      if (kind !== "all" && contextKind(t.context_key) !== kind) return false;
      if (!needle) return true;
      return (
        (t.title || "").toLowerCase().includes(needle) ||
        (t.last_message || "").toLowerCase().includes(needle) ||
        contextLabel(t.context_key).toLowerCase().includes(needle)
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
