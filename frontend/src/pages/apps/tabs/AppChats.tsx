// The Chats tab: every conversation bound to this app, real for every app
// (services/apps.py chats_for_app -- Postgres, chat_thread.app_id, no
// project link required). GET /api/apps/{id}/chats.
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Plus } from "lucide-react";
import { Button, Card, CardGrid, Row, SectionLabel } from "@workshop/ui";
import { newChatHref } from "../../../lib/chatSelection";
import type { components } from "../../../api/schema.gen";
import type { AppRecord } from "../../../lib/apps";
import { AppTabPage, CollectionBody } from "./AppTabShell";
import { queryKeys } from "../../../lib/queryKeys";

type AppChat = components["schemas"]["AppChat"];

function ChatsBody({ app }: { app: AppRecord }) {
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
          <CardGrid>
            {rows.map((c) => (
              <li key={c.id}>
                <Link
                  to={`/chat/${encodeURIComponent(c.context_key || app.slug)}?thread=${encodeURIComponent(c.id)}`}
                  style={{ textDecoration: "none", color: "inherit" }}
                >
                  <Card interactive>
                    <Row between style={{ gap: "var(--space-2)" }}>
                      <h3
                        className="ws-card__title"
                        style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      >
                        <MessagesSquare size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.4rem" }} />
                        {c.title || "Untitled chat"}
                      </h3>
                    </Row>
                  </Card>
                </Link>
              </li>
            ))}
          </CardGrid>
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
      sub="Every conversation bound to this app."
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
