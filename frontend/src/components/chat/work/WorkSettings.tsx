import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check, ChevronLeft } from "lucide-react";
import { Prop, Props, SectionLabel } from "@workshop/ui";
import { scopeLabel } from "../chatScope";
import { CHAT_MODES, type ChatModeId } from "../chatModes";

// What this chat does with work, and nothing else.
//
// THREE THINGS ARE DELIBERATELY ABSENT. The design handoff drew "Interrupt me",
// "Show ticket cards in chat" and "Run in parallel"; none of the three exists as
// a setting anywhere in the system, and a settings row with nothing behind it is
// placeholder UI that teaches the operator the panel lies.
//
// AND `Files into` IS READ-ONLY. The toolbar's scope picker already owns which
// place this conversation is in, and it owns it properly: rescoping moves the
// thread and drops the live session. A second picker here would be a second
// control over one fact, free to disagree with the first.
//
// The mode commits ON CHANGE. It is a view/behaviour preference on one
// conversation, cheap and reversible, and the persona restates it every turn so
// a change lands on the next message. Config and money commit on Save; this is
// neither.

type Autonomy = { cron_autonomy?: { disabled?: boolean } } | null;

// The per-SCOPE execution setting (services/scope_execution). Filing is a per-chat
// choice above; this is the scope choice: does the scope's filed work auto-run in
// the background, and how far. Shown only for a chat that has a scope, since a chat
// that files nowhere has no queue to arm. The route has no response_model yet, so
// there is no generated type; this local shape is the minimum it reads.
type ScopeExec = { mode?: string; armed?: boolean } | null;

const EXEC_MODES: { id: string; name: string; gloss: string }[] = [
  { id: "reactive", name: "Off", gloss: "Filed work waits for you. Nothing runs on its own." },
  { id: "supervised", name: "Run to review", gloss: "Filed work runs in the background and stops at review for your approval." },
  { id: "autonomous", name: "Run to done", gloss: "Filed work runs in the background all the way to done." },
];

export default function WorkSettings({
  scope, chatMode, onChooseChatMode, onBack,
}: {
  /** The chat's task scope, or null for a chat that files nowhere. */
  scope: string | null;
  chatMode: ChatModeId;
  onChooseChatMode: (mode: ChatModeId) => void;
  onBack: () => void;
}) {
  // Read once, the same way the roster page reads it. It is a ceiling above the
  // per-chat policy rather than part of it, and this view is open for seconds,
  // so it does not earn a poll of its own.
  const [autonomy, setAutonomy] = useState<Autonomy>(null);
  const [autonomyRead, setAutonomyRead] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/agents/autonomy/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (!cancelled) { setAutonomy(j); setAutonomyRead(true); } })
      .catch(() => { if (!cancelled) { setAutonomy(null); setAutonomyRead(true); } });
    return () => { cancelled = true; };
  }, []);

  const off = !!autonomy?.cron_autonomy?.disabled;

  // The scope's execution mode. Read on mount (and when the scope changes), and
  // committed ON CHANGE like the filing mode above: it is reversible and the
  // sweep reads it fresh each pass, so there is nothing to Save.
  const [exec, setExec] = useState<ScopeExec>(null);
  useEffect(() => {
    if (!scope) return;
    let cancelled = false;
    fetch(`/api/agents/autonomy/scope-execution?scope=${encodeURIComponent(scope)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (!cancelled) setExec(j); })
      .catch(() => { if (!cancelled) setExec(null); });
    return () => { cancelled = true; };
  }, [scope]);

  const execMode = exec?.mode ?? "reactive";
  function chooseExec(mode: string) {
    if (!scope || mode === execMode) return;
    setExec({ mode, armed: mode !== "reactive" }); // optimistic; server confirms
    fetch("/api/agents/autonomy/scope-execution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, mode }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) setExec(j); })
      .catch(() => {});
  }

  return (
    <>
      <div className="chat-workpanel__head">
        <button type="button" className="chat-workpanel__back" onClick={onBack}>
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden />
          Tickets
        </button>
        <span className="chat-workpanel__heading">Ticket settings</span>
      </div>

      <SectionLabel>When work comes up</SectionLabel>
      {/* The gloss is printed here rather than left on hover, which is the
          opposite of what the menu row this replaces did. A menu row is one
          line and a description in it wins the width fight with the name; a
          settings row is two lines and the second one is where the difference
          between keeping work here and sending it out is actually explained. */}
      {/* A RADIO GROUP, because this is one exclusive choice among the modes
          `chatModes` offers. It was a set of buttons carrying `aria-pressed`,
          which a screen reader announces as independent toggles that happen to
          look exclusive: nothing said they were alternatives, nothing said how
          many there were, and arrow keys did not move between them. Settings.tsx states the same choice
          with the kit's `Segmented`; that primitive is one compact line per
          option and each option here needs a second line of gloss, so the
          semantics are matched rather than the component. */}
      <div className="chat-workpanel__modes" role="radiogroup" aria-label="When work comes up">
        {CHAT_MODES.map((m) => {
          const on = m.id === chatMode;
          return (
            <button
              key={m.id}
              type="button"
              className="chat-workpanel__mode"
              role="radio"
              aria-checked={on}
              onClick={() => onChooseChatMode(m.id)}
            >
              <span className="chat-workpanel__mode-mark">
                {on && <Check size={14} strokeWidth={2} aria-hidden />}
              </span>
              <span className="chat-workpanel__mode-body">
                <span className="chat-workpanel__mode-name">{m.action}</span>
                <span className="chat-workpanel__mode-gloss">{m.title}</span>
              </span>
            </button>
          );
        })}
      </div>

      <Props className="chat-workpanel__props">
        <Prop label="Files into">
          {scope ? scopeLabel(scope) : "Nowhere. This chat has no ticket queue."}
        </Prop>
      </Props>

      {/* The scope choice, distinct from the chat choice above: filing is what
          THIS conversation does, execution is what the whole scope does with
          filed work. Shown only when the chat has a scope to arm. */}
      {scope && (
        <>
          <SectionLabel>Background execution</SectionLabel>
          <div className="chat-workpanel__modes" role="radiogroup" aria-label="Background execution">
            {EXEC_MODES.map((m) => {
              const on = m.id === execMode;
              return (
                <button
                  key={m.id}
                  type="button"
                  className="chat-workpanel__mode"
                  role="radio"
                  aria-checked={on}
                  onClick={() => chooseExec(m.id)}
                >
                  <span className="chat-workpanel__mode-mark">
                    {on && <Check size={14} strokeWidth={2} aria-hidden />}
                  </span>
                  <span className="chat-workpanel__mode-body">
                    <span className="chat-workpanel__mode-name">{m.name}</span>
                    <span className="chat-workpanel__mode-gloss">{m.gloss}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <Props className="chat-workpanel__props">
            <Prop label="Applies to">{scopeLabel(scope)}, not just this chat.</Prop>
          </Props>
        </>
      )}

      {/* The ceiling above everything above. A chat set to file and run on its
          own still runs nothing while the workshop is paused, and the operator
          should learn that here rather than by watching a ticket sit still. */}
      {autonomyRead && (
        <div className="chat-workpanel__foot">
          <span className="chat-workpanel__note">
            {autonomy
              ? off
                ? "Workshop autonomy is off, so nothing here runs on its own."
                : "Workshop autonomy is on."
              : "Couldn't read whether workshop autonomy is on."}
          </span>
          <Link className="chat-workpanel__link-out" to="/settings">Settings</Link>
        </div>
      )}
    </>
  );
}
