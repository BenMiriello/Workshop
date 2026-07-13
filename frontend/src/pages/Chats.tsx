import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessagesSquare } from "lucide-react";
import {
  Button,
  Card,
  Field,
  Input,
  PageHeader,
  Skeleton,
  StateBlock,
  Tag,
  catFor,
} from "../ui";
import { chatApi, type ThreadSummary } from "../lib/chat";

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

function contextLabel(key: string): string {
  if (!key || key === "general") return "General";
  const [kind, rest] = key.split(":", 2);
  if (kind === "project") return `Project · ${rest}`;
  if (kind === "build") return `Builder · ${rest}`;
  if (kind === "agent") return `Agent · ${rest}`;
  if (kind === "app") return `App · ${rest}`;
  return key;
}

export default function Chats() {
  const nav = useNavigate();
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");

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

  const filtered = useMemo(() => {
    if (!threads) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return threads;
    return threads.filter(
      (t) =>
        (t.title || "").toLowerCase().includes(needle) ||
        (t.last_message || "").toLowerCase().includes(needle) ||
        contextLabel(t.context_key).toLowerCase().includes(needle),
    );
  }, [threads, q]);

  const open = (t: ThreadSummary) =>
    nav(`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`);

  return (
    <div>
      <PageHeader title="Chats" sub="Every conversation across the workspace" />

      <div style={{ maxWidth: 420, marginBottom: "var(--space-4)" }}>
        <Field label="Search">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by title, context, or last message"
            spellCheck={false}
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
        />
      ) : filtered.length === 0 ? (
        <StateBlock title="No matches" hint="No chat matches that search. Clear the filter to see all." />
      ) : (
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {filtered.map((t) => (
            <Card
              key={t.id}
              onClick={() => open(t)}
              style={{ padding: "var(--space-4)", cursor: "pointer" }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-3)", flexWrap: "wrap" }}>
                <MessagesSquare size={14} aria-hidden />
                <strong style={{ minWidth: 0 }}>{t.title || "New chat"}</strong>
                <Tag hue={catFor(t.context_key)}>{contextLabel(t.context_key)}</Tag>
                <span className="ws-card__meta" style={{ marginLeft: "auto" }}>
                  {t.message_count} msg
                </span>
              </div>
              {t.last_message && (
                <p className="ws-muted" style={{ margin: "var(--space-2) 0 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.last_message}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
