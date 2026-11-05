// Where this chat is working and how you see it, in one control: the page in
// an app it last worked on, as the app reports it (`chat.place`,
// routes/chat_place.py), and the view of its work in the apps that show it
// (`chat.follow`): follow, show or hide (routes/page_link.py `view`).
//
// Pressing it opens a menu: open that page beside this chat (full screen it
// goes there with the chat; beside that app's page it brings it forward), and
// the three views. Stored on the chat and told to the apps at once, so a view
// counts wherever the app is open, mid-turn included, whether or not anything
// is open here.

import { useState } from "react";
import { AppWindow, Check, Eye, EyeOff, PanelsTopLeft } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Button, Popover, SectionLabel } from "@workshop/ui";
import type { components } from "../../api/schema.gen";
import { chatApi, type Thread, type ThreadSummary } from "../../lib/chat";
import { getJson } from "../../lib/http";
import { queryKeys } from "../../lib/queryKeys";
import { beside } from "../shell/beside/besideStore";
import { isView, type ChatView } from "../shell/beside/protocol";
import { leaveFrame, useChatEmbed } from "./chatEmbed";
import "../shell/beside/beside.css";

type Place = components["schemas"]["ChatPlace"];
type ChatApp = components["schemas"]["ChatApp"];
type PageState = components["schemas"]["PageState"];

// Within this long of the chat's last call there, it is working there now.
const LIVE_S = 20;

export const VIEW_ROWS: { view: ChatView; label: string; hint: string }[] = [
  { view: "follow", label: "Follow", hint: "What it opens or acts on comes to the front" },
  { view: "show", label: "Show its tabs", hint: "Its work waits in the app; nothing comes forward" },
  { view: "hide", label: "Hide", hint: "It works unseen, kept out of the app's lists" },
];

const ICONS = { follow: Eye, show: PanelsTopLeft, hide: EyeOff } as const;

async function putView(threadId: string, view: ChatView): Promise<PageState> {
  const r = await fetch(`/api/page-link/${encodeURIComponent(threadId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ view }),
  });
  if (!r.ok) throw new Error(`PUT /api/page-link: ${r.status}`);
  return r.json() as Promise<PageState>;
}

export default function ChatPlace({ threadId, contextKey, live, onAdopt }: {
  threadId: string | null;
  contextKey: string;
  live: boolean;
  onAdopt: (t: ThreadSummary | Thread) => void;
}) {
  const embed = useChatEmbed();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const ownSlug = contextKey.startsWith("app:") ? contextKey.slice(4) : null;
  const catalog = useQuery({
    queryKey: queryKeys.chatApps.catalog(),
    queryFn: () => getJson<ChatApp[]>("/api/chat-apps"),
    staleTime: 60_000,
  });
  const grants = useQuery({
    queryKey: queryKeys.chatApps.grants(threadId ?? ""),
    queryFn: () => getJson<{ access: Record<string, string[]> }>(`/api/chat-apps/${encodeURIComponent(threadId!)}`)
      .then((g) => g.access),
    enabled: !!threadId,
  });
  const places = useQuery({
    queryKey: queryKeys.chatPlace(threadId ?? ""),
    queryFn: () => getJson<Place[]>(`/api/chat-place/${encodeURIComponent(threadId!)}`),
    enabled: !!threadId,
    refetchInterval: live ? 4000 : 30_000,
  });
  const state = useQuery({
    queryKey: queryKeys.pageLink(threadId ?? ""),
    queryFn: () => getJson<PageState>(`/api/page-link/${encodeURIComponent(threadId!)}`),
    enabled: !!threadId,
  });

  const shows = (catalog.data ?? []).find((a) => a.follow && (a.slug === ownSlug || a.slug in (grants.data ?? {})));
  const place = places.data?.[0];
  if (!shows && !place) return null;

  const view: ChatView = isView(state.data?.view) ? state.data.view : state.data?.follow === false ? "show" : "follow";
  const working = live && place?.at != null && Date.now() / 1000 - place.at < LIVE_S;
  const appLabel = place?.app_label ?? shows?.label ?? "";
  const Icon = ICONS[view];
  const row = VIEW_ROWS.find((r) => r.view === view)!;

  const go = () => {
    if (!place || !threadId) return;
    setOpen(false);
    if (embed?.host) embed.host.leave(place.path);
    else if (embed) leaveFrame(place.path);
    else {
      beside.carry(threadId, contextKey);
      navigate(place.path);
    }
  };

  const choose = async (next: ChatView) => {
    setFailed(null);
    let id = threadId;
    if (!id) {
      const t = await chatApi.createThread(contextKey);
      onAdopt(t);
      id = t.id;
    }
    const key = queryKeys.pageLink(id);
    const before = qc.getQueryData<PageState>(key);
    const mark = (v: ChatView) => { if (beside.get().thread === id) beside.setView(v); };
    if (before) qc.setQueryData<PageState>(key, { ...before, follow: next === "follow", view: next });
    mark(next);
    try {
      qc.setQueryData(key, await putView(id, next));
    } catch (e) {
      qc.setQueryData(key, before);
      mark(view);
      setFailed(`Couldn't change this: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const what = place ? `${appLabel}: ${place.title || place.label}` : appLabel;
  const label = `${working ? "Working in" : place ? "Last worked in" : "Works in"} ${what}. ${row.label}.`;
  return (
    <Popover
      label="Where this chat works"
      open={open}
      onOpenChange={setOpen}
      trigger={(
        <button type="button" className="chat-tb__place" title={label} aria-label={label}>
          <Icon size={14} aria-hidden />
          <span className="chat-tb__place-label">{place?.label ?? appLabel}</span>
          {working && <span className="chat-tb__place-live" aria-hidden />}
        </button>
      )}
    >
      <div className="ws-access">
        {failed && <p className="ws-access__note">{failed}</p>}
        <div className="ws-access__group">
          <SectionLabel>{appLabel}</SectionLabel>
          {place ? (
            <>
              <p className="ws-place__where">{place.title || place.label}</p>
              <Button size="sm" onClick={go}>
                <AppWindow size={14} aria-hidden /> Open beside this chat
              </Button>
            </>
          ) : <p className="ws-access__note">Nothing opened yet.</p>}
        </div>
        <div className="ws-access__group">
          <SectionLabel>While it works</SectionLabel>
          <div role="radiogroup" aria-label="How you see this chat at work">
            {VIEW_ROWS.map((r) => (
              <button
                key={r.view}
                type="button"
                role="radio"
                aria-checked={view === r.view}
                className={`ws-access__level ws-place__view${view === r.view ? " is-on" : ""}`}
                onClick={() => { void choose(r.view); }}
              >
                <Check size={14} aria-hidden className="ws-access__tick" />
                <span>
                  {r.label}
                  <span className="ws-place__hint">{r.hint}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Popover>
  );
}
