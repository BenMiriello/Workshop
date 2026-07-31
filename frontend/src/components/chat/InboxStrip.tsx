import { useCallback, useEffect, useState } from "react";
import { Bell, Check, X, MessageCircleQuestion, ClipboardCheck, Info } from "lucide-react";
import { Button, Input, Tag, catFor } from "../../ui";

// Items an agent raised that need the operator, shown above the transcript.
//
// NOT typed by entity. A finished run, a blocked agent and a proposal awaiting
// approval are one kind of thing (something wants you) with three verbs, which
// is what makes a single stack coherent instead of a pile of mixed nouns:
//
//   question  the agent is stuck and is asking rather than guessing  -> answer
//   review    the agent proposes something                           -> approve / reject
//   notify    FYI, safe to ignore                                    -> dismiss
//
// The store is global; this is the per-chat view of it. What a chat does not
// show inline is still counted here ("and N more"), because a filtered view
// that cannot tell you what it filtered out is how things get missed.
export type InboxItem = {
  id: string;
  kind: "notify" | "question" | "review";
  agent: string;
  scope: string;
  title: string;
  body: string;
  options: string[];
  chat_id: string | null;
};

type Counts = { notify: number; question: number; review: number };

const KIND_ICON = {
  question: MessageCircleQuestion,
  review: ClipboardCheck,
  notify: Info,
} as const;

export default function InboxStrip({
  chatId,
  onOpenGlobal,
}: {
  chatId: string | null;
  onOpenGlobal: () => void;
}) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [globalCounts, setGlobalCounts] = useState<Counts | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : "";
    try {
      const r = await fetch(`/api/inbox${q}`);
      if (!r.ok) { setItems([]); return; }
      const j = await r.json();
      setItems(j.items || []);
      setGlobalCounts(j.global_counts || null);
    } catch {
      // A failed poll leaves the last good state on screen rather than blanking
      // the strip: an inbox that empties itself on a network blip is worse than
      // one that is briefly stale.
      setItems((v) => v ?? []);
    }
  }, [chatId]);

  useEffect(() => {
    void load();
    // Agents act on their own schedule, so this has to poll. 20s is under the
    // threshold where the operator would notice a lag and well above anything
    // that would cost meaningfully; it pauses while the tab is hidden.
    const t = window.setInterval(() => { if (!document.hidden) void load(); }, 20000);
    const onVis = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [load]);

  const resolve = useCallback(async (id: string, answer: string | null, dismissed: boolean) => {
    setBusy(id);
    // Optimistic: the operator acted, so the item goes now. It is re-fetched
    // straight after, which corrects the rare case where the write failed.
    setItems((v) => (v || []).filter((i) => i.id !== id));
    try {
      await fetch(`/api/inbox/${encodeURIComponent(id)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer, dismissed }),
      });
    } finally {
      setBusy(null);
      void load();
    }
  }, [load]);

  if (!items || items.length === 0) {
    // No empty state. This is an interrupt surface: when nothing needs the
    // operator it must take zero space and say nothing at all.
    return null;
  }

  const elsewhere = globalCounts
    ? (globalCounts.notify + globalCounts.question + globalCounts.review) - items.length
    : 0;

  return (
    <div className="chat-inbox" role="region" aria-label="Agents needing you">
      {items.map((it) => {
        const Icon = KIND_ICON[it.kind] ?? Info;
        return (
          <div key={it.id} className={`chat-inbox__item chat-inbox__item--${it.kind}`}>
            <div className="chat-inbox__head">
              <Icon size={14} strokeWidth={1.8} aria-hidden />
              <Tag hue={catFor(`agent:${it.agent}`)}>{it.agent}</Tag>
              <span className="chat-inbox__title">{it.title}</span>
              <button
                type="button"
                className="chat-inbox__dismiss"
                onClick={() => void resolve(it.id, null, true)}
                title="Dismiss"
                aria-label={`Dismiss: ${it.title}`}
              >
                <X size={14} aria-hidden />
              </button>
            </div>
            {it.body && <p className="chat-inbox__body">{it.body}</p>}

            {it.kind === "question" && (
              <div className="chat-inbox__actions">
                {it.options.length > 0 ? (
                  it.options.map((o) => (
                    <Button key={o} size="sm" variant="ghost"
                            onClick={() => void resolve(it.id, o, false)}>
                      {o}
                    </Button>
                  ))
                ) : (
                  <>
                    <Input
                      value={answers[it.id] || ""}
                      onChange={(e) => setAnswers((a) => ({ ...a, [it.id]: e.target.value }))}
                      placeholder="Answer…"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && (answers[it.id] || "").trim()) {
                          void resolve(it.id, answers[it.id].trim(), false);
                        }
                      }}
                    />
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={!(answers[it.id] || "").trim() || busy === it.id}
                      onClick={() => void resolve(it.id, (answers[it.id] || "").trim(), false)}
                    >
                      Send
                    </Button>
                  </>
                )}
              </div>
            )}

            {it.kind === "review" && (
              <div className="chat-inbox__actions">
                <Button size="sm" variant="primary" onClick={() => void resolve(it.id, "approved", false)}>
                  <Check size={14} aria-hidden /> Approve
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void resolve(it.id, "rejected", false)}>
                  Reject
                </Button>
              </div>
            )}
          </div>
        );
      })}

      {elsewhere > 0 && (
        <button type="button" className="chat-inbox__more" onClick={onOpenGlobal}>
          <Bell size={13} strokeWidth={1.8} aria-hidden />
          {elsewhere} more in other scopes
        </button>
      )}
    </div>
  );
}
