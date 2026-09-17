// One open conversation, INSIDE the app it belongs to.
//
// WHY THIS ROUTE EXISTS. A chat row on an app's Chats tab used to navigate to
// `/chat/<context-key>`, the global focused chat. That path belongs to the All
// chats place (`places.ts`, `prefixes: ["/chats", "/chat"]`), so opening a chat
// from inside an app left the app: the dock lit All chats, the page column lost
// the app's tabs, and Back went to the global list rather than to the tab the
// reader had been on. The operator, 2026-09-17: "if I go to a project and I go
// to its chats page and then I open a particular chat thread, it goes to all
// chats, which is not correct. That is not how it should work."
//
// The fix is the ADDRESS, not a second chat. `appChatPath` (shell/places.ts)
// puts the conversation under the app's own prefix, and every one of the
// shell's existing rules then does the right thing with no special case:
// `placeFor` resolves the app (longest matching prefix), `pageFor` still
// resolves its Chats row (a page matches as a prefix too, so the tab stays
// highlighted), and `isOpenChat` gives the path the viewport-tall chat layout.
//
// THE CHAT ITSELF IS NOT DUPLICATED. `OpenConversation` (pages/FocusedChat) is
// the screen, mounted by both routes. This file is only the two things that
// differ: which scope the conversation is in, and what the breadcrumb says.
// Five design generations have been lost in this repo to a second copy of a
// surface, and the app's Chats tab was one of them (see the header of
// pages/Chats.tsx).
import { useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button, StateBlock, Skeleton, Stack, useCrumbTrail, type Crumb } from "@workshop/ui";
import { chatApi } from "../../../lib/chat";
import { queryKeys } from "../../../lib/queryKeys";
import { useApp } from "../../../lib/apps";
import { OpenConversation } from "../../FocusedChat";

/** Room to say something in a shell that has handed its padding to the chat.
 *  `--chatfull` sets `padding: 0; overflow: hidden` on the content region, so a
 *  state block rendered bare would sit flush against the top edge. */
const STATE_PAD = { padding: "var(--space-4)" } as const;

export default function AppChatThread() {
  const { appSlug = "", threadId = "" } = useParams();
  // Picking a conversation from the chat's own toolbar is a same-path push, so
  // the ROUTER KEY is what makes it an event rather than an unchanged value.
  // Same reason FocusedChat passes it. See chat/threadIntent.
  const { key: navKey } = useLocation();
  const { app, err: appErr, reload: reloadApp } = useApp(appSlug);

  // WHICH SCOPE THIS CONVERSATION IS IN, asked of the thread rather than
  // assumed from the app. `/api/apps/{id}/chats` selects by `chat_thread.app_id`,
  // which is broader than any scope key: one app's chats can sit under several
  // context keys, so the app's own scope is a fallback and not the answer. One
  // field of one small settings read (`getThread` is not the transcript), and it
  // is what makes the URL a real deep link instead of something that only works
  // when you arrived from the tab.
  const thread = useQuery({
    queryKey: queryKeys.chat.thread(threadId),
    queryFn: () => chatApi.getThread(threadId),
    enabled: threadId !== "",
    retry: false,
  });

  // THE TRAIL NAMES THE APP AND THE TAB, and the tab crumb is the way back.
  //
  // ShellTrail draws a trail only where one goes deeper than the page column
  // already says, which is exactly this case: the column says "Chats" and the
  // conversation is a record under it. Built from whatever has resolved so far,
  // so the trail never flashes a placeholder for a name that is about to
  // arrive.
  const trail = useMemo<Crumb[]>(() => {
    const crumbs: Crumb[] = [];
    if (app) crumbs.push({ label: app.name, to: `/apps/${encodeURIComponent(appSlug)}` });
    crumbs.push({ label: "Chats", to: `/apps/${encodeURIComponent(appSlug)}/chats` });
    crumbs.push({ label: thread.data?.title || "Chat" });
    return crumbs;
  }, [app, appSlug, thread.data?.title]);
  useCrumbTrail(trail);

  // THE APP RECORD FIRST, exactly as AppTabPage does it, and for the same
  // reason: without it there is no reliable scope to open the conversation in
  // (a project-backed app owns a scope that is not its slug), so guessing here
  // would file the chat under the wrong one. An app that resolves keeps going
  // even if the read later fails; only "never resolved" is an error.
  if (appErr && !app) {
    return (
      <div style={STATE_PAD}>
        <StateBlock
          variant="error"
          title="Couldn't load this app"
          hint={<code>GET /api/apps ({appErr})</code>}
          action={<Button size="sm" onClick={reloadApp}>Retry</Button>}
        />
      </div>
    );
  }

  if (thread.isError) {
    return (
      <div style={STATE_PAD}>
        <StateBlock
          variant="error"
          title="Couldn't open this chat"
          hint={<code>GET /api/chat/threads/{threadId}: {String(thread.error)}</code>}
          action={<Button size="sm" onClick={() => void thread.refetch()}>Retry</Button>}
        />
      </div>
    );
  }
  if (!thread.data) {
    return (
      <div style={STATE_PAD}>
        <Stack gap="sm">
          <Skeleton width="40%" />
          <Skeleton style={{ height: 120 }} />
        </Stack>
      </div>
    );
  }

  // The app's slug stands in for a context key the thread does not carry, the
  // same fallback the row on the Chats tab already used: a row selected by
  // `app_id` can come back with a null `context_key`, and without this the chat
  // would open on the scope literally named "null". `app.scope` first, because
  // for a project-backed app the owned scope is not the slug -- the server
  // resolves that (services/apps.owned_scope) and ships it on the payload.
  const contextKey =
    (thread.data.context_key || app?.scope || appSlug || "general").trim() || "general";

  return (
    <OpenConversation contextKey={contextKey} threadId={threadId} navKey={navKey} />
  );
}
