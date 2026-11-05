// The chat with no shell around it, for a page that frames it: an app showing
// its own chat inside its own screen.
//
//   /embed/chat/<scope>[?thread=<id>][&new=1][&chats=<host base>]
//
// `<scope>` is a chat context key, typically `app:<slug>`: the chat an app
// declares in its manifest. The frame shows THAT SCOPE AND NO OTHER. It lists
// only its conversations, files a new one under it, and will not open a
// conversation filed anywhere else.
//
// AN APP'S CHAT IS NOT ITS BUILD CHAT. `project:<slug>` holds the conversations
// in which an agent edits the app's source, with the full toolset over its
// tree; `app:<slug>` holds the app's own assistant, with the app's persona, its
// tools and its default model. Both are keyed to the same app row, which is why
// "the app's conversations" is the wrong question to ask here. The frame used
// to fall back to the project scope when the app scope was not listed, and the
// operator found builder chats inside the app, unusable there.
//
// THE CHAT IS NOT REIMPLEMENTED. `OpenConversation` is the focused chat's own
// screen; this route differs only in having no dock or page column, and in the
// addresses it hands the chat through `ChatEmbedContext`.
import { useMemo, type MouseEvent, type ReactNode } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button, OverlayHost, Skeleton, Stack, StateBlock } from "@workshop/ui";
import { chatApi } from "../lib/chat";
import { queryKeys } from "../lib/queryKeys";
import {
  ChatEmbedContext, HOST_CHATS_PARAM, embedChatPath, embedListPath, hostChatPath, hostChatsBase, leaveFrame,
  openFromFrame, pathLeavingFrame, type ChatEmbed,
} from "../components/chat/chatEmbed";
import { OpenConversation } from "./FocusedChat";
import { useFrameEdge } from "../lib/frameEdge";

const STATE_PAD = { padding: "var(--space-4)" } as const;

export default function EmbedChat() {
  const { scope: raw = "" } = useParams();
  const scope = decodeURIComponent(raw);
  const appSlug = scope.startsWith("app:") ? scope.slice("app:".length) : null;
  return (
    <EmbedFrame appSlug={appSlug}>
      {appSlug
        ? <AppEmbed scope={scope} appSlug={appSlug} />
        : <EmbeddedConversation scope={scope} contextKey={scope || "general"} appSlug={null} />}
    </EmbedFrame>
  );
}

function AppEmbed({ scope, appSlug }: { scope: string; appSlug: string }) {
  const scopes = useQuery({
    queryKey: queryKeys.chat.providerKeys(),
    queryFn: () => chatApi.listProviders().then((list) => list.map((p) => p.key)),
    retry: false,
  });
  if (scopes.isError) {
    return (
      <div style={STATE_PAD}>
        <StateBlock
          variant="error"
          title="Couldn't load the chat"
          hint={<code>GET /api/chat/providers: {String(scopes.error)}</code>}
          action={<Button size="sm" onClick={() => void scopes.refetch()}>Retry</Button>}
        />
      </div>
    );
  }
  if (!scopes.data) return <Loading />;
  if (!scopes.data.includes(scope)) {
    return (
      <div style={STATE_PAD}>
        <StateBlock
          variant="empty"
          title="Chat isn't available right now"
          action={<Button size="sm" onClick={() => void scopes.refetch()}>Try again</Button>}
        />
      </div>
    );
  }
  return <EmbeddedConversation scope={scope} contextKey={scope} appSlug={appSlug} />;
}

function Loading() {
  return (
    <div style={STATE_PAD}>
      <Stack gap="sm">
        <Skeleton width="40%" />
        <Skeleton style={{ height: 120 }} />
      </Stack>
    </div>
  );
}

function EmbeddedConversation({ scope, contextKey, appSlug }: {
  /** The route's own `:scope`, so the frame's address stays what its host wrote. */
  scope: string;
  contextKey: string;
  appSlug: string | null;
}) {
  const [search] = useSearchParams();
  const { key: navKey } = useLocation();
  const threadId = search.get("thread") || "";
  // A host that shows its chats at its own addresses keeps "see all" and a
  // picked conversation on its own screens (chatEmbed, HOST_CHATS_PARAM).
  const hostBase = hostChatsBase(search.get(HOST_CHATS_PARAM), appSlug);
  const embed = useMemo<ChatEmbed>(() => ({
    threadPath: (id) => embedChatPath(scope, id),
    listPath: hostBase ?? embedListPath(contextKey),
    pickerScope: contextKey,
    leave: (path) => openFromFrame(path, appSlug),
    openThread: hostBase ? (id) => openFromFrame(hostChatPath(hostBase, id), appSlug) : undefined,
  }), [scope, contextKey, appSlug, hostBase]);
  // WHERE A LINKED CONVERSATION IS FILED, asked before it is shown, because the
  // chat's own loader opens a linked thread from any scope by design. A thread
  // the server cannot find falls through to that loader, which handles it.
  const linked = useQuery({
    queryKey: queryKeys.chat.thread(threadId),
    queryFn: () => chatApi.getThread(threadId),
    enabled: threadId !== "",
    retry: false,
  });
  if (threadId && linked.isPending) return <Loading />;
  const owner = linked.data?.context_key;
  if (threadId && owner && owner !== contextKey) {
    return (
      <div style={STATE_PAD}>
        <StateBlock
          variant="empty"
          title="This chat isn't part of this app"
          action={(
            <Button size="sm" onClick={() => leaveFrame(
              `/chat/${encodeURIComponent(owner)}?thread=${encodeURIComponent(threadId)}`,
            )}
            >
              Open in Workshop
            </Button>
          )}
        />
      </div>
    );
  }
  return (
    <ChatEmbedContext.Provider value={embed}>
      <OpenConversation
        contextKey={contextKey}
        threadId={threadId || undefined}
        navKey={navKey}
        startNew={search.get("new") === "1"}
      />
    </ChatEmbedContext.Provider>
  );
}

/** The `--chatfull` layout without the shell: one viewport-tall column that
 *  fills the frame. A link to any other Workshop page (a ticket, a run) opens
 *  in the window hosting the frame, not inside it. A link to one of the host
 *  app's own screens is offered to the host first (`workshop/v1/open`), so the
 *  app shows it in place with its chat still open. */
function EmbedFrame({ appSlug, children }: { appSlug: string | null; children: ReactNode }) {
  // A host decides where the frame ends; the composer pads for the home bar
  // only when that is the bottom of the screen (lib/frameEdge).
  useFrameEdge();
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest("a[href]");
    if (!(a instanceof HTMLAnchorElement)) return;
    const path = pathLeavingFrame(a.getAttribute("href") || "", window.location.href, a.getAttribute("target"));
    if (!path) return;
    e.preventDefault();
    openFromFrame(path, appSlug);
  };
  return (
    <div className="ws-root ws-app ws-app--chatfull" onClickCapture={onClickCapture}>
      <OverlayHost>
        <main>{children}</main>
      </OverlayHost>
    </div>
  );
}
