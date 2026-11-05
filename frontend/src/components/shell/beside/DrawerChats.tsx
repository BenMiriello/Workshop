import { useEffect } from "react";
import { List } from "lucide-react";
import { ChatsView } from "../../../pages/Chats";
import { beside } from "./besideStore";
import type { ThreadSummary } from "../../../lib/chat";

// Every chat, inside the drawer. It covers the conversation without
// unmounting it, so a turn that is streaming, the draft and the scroll position
// are all where they were when the list closes. Its bar has the same icon in the
// same place as the chat's, so one control opens it and the same one closes it.

export function DrawerChats({ scope, onClose }: { scope: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const open = (t: ThreadSummary) => beside.open({ scope: t.context_key, threadId: t.id });
  return (
    <div className="ws-beside__chats" role="region" aria-label="All chats">
      <div className="chat-toolbar">
        <button
          className="chat-tb__btn is-active"
          onClick={onClose}
          title="Back to the chat"
          aria-label="Back to the chat"
          aria-pressed="true"
        >
          <List size={16} aria-hidden />
        </button>
        <span className="chat-tb__label">All chats</span>
      </div>
      <div className="ws-beside__chats-body">
        <ChatsView
          header={false}
          onOpenThread={open}
          onNewChat={() => beside.open({ scope, startNew: true })}
        />
      </div>
    </div>
  );
}
