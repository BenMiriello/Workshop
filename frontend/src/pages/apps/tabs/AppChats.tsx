// The Chats tab: every conversation bound to this app, real for every app
// (services/apps.py chats_for_app -- Postgres, chat_thread.app_id, no
// project link required). GET /api/apps/{id}/chats.
//
// IT RENDERS THE SAME ROW /chats RENDERS, and that is the whole of this file's
// job. It used to render a three-column grid of cards holding a title and
// nothing else: no preview, no turn count, no time, no live state, because the
// endpoint did not return any of those. So the same conversation looked like a
// different kind of object depending on which page you opened it from, and this
// one could not answer "when" or "how much" at all. Reported 2026-08-29: "why
// does it still take me to the wrong chats page? Some old chats page why does
// it still exist even?" The endpoint now returns the global summary and the row
// lives in components/chat/ChatRow.
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Plus } from "lucide-react";
import { Button, SectionLabel, Stack } from "@workshop/ui";
import ChatRow from "../../../components/chat/ChatRow";
import type { ThreadSummary } from "../../../lib/chat";
import { newChatHref } from "../../../lib/chatSelection";
import type { components } from "../../../api/schema.gen";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { queryKeys } from "../../../lib/queryKeys";

type AppChat = components["schemas"]["AppChat"];

function ChatsBody({ app }: { app: AppRecord }) {
  const nav = useNavigate();
  const endpoint = `/api/apps/${app.id}/chats`;
  const q = useQuery({
    queryKey: queryKeys.apps.chats(app.id),
    queryFn: async (): Promise<AppChat[]> => {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
      const d = await r.json();
      return d.chats ?? [];
    },
  });

  return (
    <section>
      <SectionLabel icon={<MessagesSquare aria-hidden />}>Chats</SectionLabel>
      <CollectionBody
        items={q.data ?? null}
        err={q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null}
        reload={() => void q.refetch()}
        endpoint={endpoint}
        emptyTitle="No chats yet"
        emptyHint={`Open a chat scoped to ${app.name} to see it here.`}
        overflowHint="Archive old chats from the chat toolbar to bring this list down."
        render={(rows) => (
          <Stack gap="sm">
            {rows.map((c) => (
              <ChatRow
                key={c.id}
                thread={c as unknown as ThreadSummary}
                onOpen={() =>
                  nav(`/chat/${encodeURIComponent(c.context_key || app.slug)}?thread=${encodeURIComponent(c.id)}`)
                }
              />
            ))}
          </Stack>
        )}
      />
    </section>
  );
}

export default function AppChats() {
  const { appSlug = "" } = useParams();
  const nav = useNavigate();
  return (
    <AppTabPage
      slug={appSlug}
      title="Chats"
      actions={(app) => (
        <Button variant="ghost" size="sm" onClick={() => nav(newChatHref(app.slug))}>
          <Plus aria-hidden /> New chat
        </Button>
      )}
    >
      {(app) => <ChatsBody app={app} />}
    </AppTabPage>
  );
}
