// The chat LIST with no shell around it, for a page that frames it: an app
// showing its own chats inside its own screen.
//
//   /embed/chats/<scope>[?chats=<host base>]
//
// `<scope>` is a chat context key, typically `app:<slug>`, and the list holds
// that scope's conversations and no other: an app's own chat, never its build
// chats (see EmbedChat for why the two differ). It is the Chats page's own
// list (`ChatsView`), grid and all; only where a pick goes differs.
//
// A host that names its own addresses (`?chats=`, chatEmbed HOST_CHATS_PARAM)
// is handed every pick and New chat, so the conversation opens on the host's
// own screen and its address bar and Back work. Without one, the frame opens
// the conversation itself, at its own embed address.
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { OverlayHost } from "@workshop/ui";
import {
  HOST_CHATS_PARAM, embedChatPath, hostChatPath, hostChatsBase, openFromFrame,
} from "../components/chat/chatEmbed";
import { ChatsView } from "./Chats";

export default function EmbedChats() {
  const { scope: raw = "" } = useParams();
  const scope = decodeURIComponent(raw) || "general";
  const appSlug = scope.startsWith("app:") ? scope.slice("app:".length) : null;
  const [search] = useSearchParams();
  const nav = useNavigate();
  const base = hostChatsBase(search.get(HOST_CHATS_PARAM), appSlug);
  // One conversation by id, or a new one without.
  const open = (threadId?: string) => {
    if (base) openFromFrame(hostChatPath(base, threadId), appSlug);
    else nav(threadId ? embedChatPath(scope, threadId) : `${embedChatPath(scope)}?new=1`);
  };
  return (
    <div className="ws-root ws-app">
      <OverlayHost>
        <main>
          <ChatsView
            scope={scope}
            title="Chats"
            onOpenThread={(t) => open(t.id)}
            onNewChat={() => open()}
          />
        </main>
      </OverlayHost>
    </div>
  );
}
