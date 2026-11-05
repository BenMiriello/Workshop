// What this chat may reach, in one place: how much of the page beside it (when
// it is in the drawer beside one) and which other apps' tools, with each app's
// own options. Commits on change, like the model picker: it is a per-chat
// setting that applies from the next turn.
//
// The levels and the grants are the server's (services/page_link,
// services/chat_apps); nothing here names an app, every label comes from the
// app's own declaration.

import { useState } from "react";
import { Check, KeyRound } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Checkbox, Popover, SectionLabel } from "@workshop/ui";
import { chatApi, type Thread, type ThreadSummary } from "../../lib/chat";
import { queryKeys } from "../../lib/queryKeys";
import { useBeside } from "../shell/beside/besideStore";
import { useChatEmbed } from "./chatEmbed";
import { setLevel } from "../shell/beside/usePageLink";
import "../shell/beside/beside.css";
import type { Level } from "../shell/beside/protocol";

type AccessOption = { id: string; label: string; default: boolean };
type ChatApp = { slug: string; label: string; about: string; access: AccessOption[]; follow: boolean };
type Grants = Record<string, string[]>;

// Three steps of autonomy on the page beside the chat. "use" is an older
// level kept by chats that had it; it counts as full autonomy.
const LEVEL_ROWS: { level: Level; label: string; hint: string }[] = [
  { level: "send", label: "Only what I send", hint: "It sees nothing on the page unless you send it" },
  { level: "see", label: "Sees the page", hint: "It reads the page but does not act on it" },
  { level: "drive", label: "Full autonomy", hint: "It reads and acts on its own; you can step in anytime" },
];

const levelShown = (level: Level): Level => (level === "use" ? "drive" : level);

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json() as Promise<T>;
}

async function putGrants(threadId: string, access: Grants): Promise<Grants> {
  const r = await fetch(`/api/chat-apps/${encodeURIComponent(threadId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ access }),
  });
  if (!r.ok) throw new Error(`PUT /api/chat-apps: ${r.status}`);
  return ((await r.json()) as { access: Grants }).access;
}

export default function AccessMenu({ threadId, contextKey, onAdopt }: {
  threadId: string | null;
  contextKey: string;
  /** Take a thread this menu had to create (a grant on a chat not yet sent in). */
  onAdopt: (t: ThreadSummary | Thread) => void;
}) {
  const qc = useQueryClient();
  const b = useBeside();
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  // This menu is the drawer's own when its chat is hosted by the drawer; it
  // has the page section then, even before the chat has a conversation.
  const inDrawer = !!useChatEmbed()?.host;
  const besidePage = b.open && inDrawer;
  const ownSlug = contextKey.startsWith("app:") ? contextKey.slice(4) : null;
  const catalog = useQuery({
    queryKey: queryKeys.chatApps.catalog(),
    queryFn: () => getJson<ChatApp[]>("/api/chat-apps"),
    staleTime: 60_000,
  });
  const grants = useQuery({
    queryKey: queryKeys.chatApps.grants(threadId ?? ""),
    queryFn: () => getJson<{ access: Grants }>(`/api/chat-apps/${encodeURIComponent(threadId!)}`).then((g) => g.access),
    enabled: !!threadId,
  });
  const access = grants.data ?? {};
  const granted = Object.keys(access).filter((s) => s !== ownSlug).length;
  const on = (besidePage && b.level !== "send") || granted > 0;

  const save = async (next: Grants) => {
    let id = threadId;
    if (!id) {
      const t = await chatApi.createThread(contextKey);
      onAdopt(t);
      id = t.id;
    }
    try {
      const stored = await putGrants(id, next);
      qc.setQueryData(queryKeys.chatApps.grants(id), stored);
      setFailed(null);
    } catch (e) {
      setFailed(`Couldn't change this: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const toggleApp = (app: ChatApp, checked: boolean) => {
    const next = { ...access };
    if (checked) next[app.slug] = app.access.filter((o) => o.default).map((o) => o.id);
    else delete next[app.slug];
    void save(next);
  };

  const toggleOption = (app: ChatApp, option: string, checked: boolean) => {
    const own = app.slug === ownSlug;
    const current = access[app.slug] ?? (own ? app.access.filter((o) => o.default).map((o) => o.id) : []);
    const opts = checked ? [...new Set([...current, option])] : current.filter((o) => o !== option);
    void save({ ...access, [app.slug]: opts });
  };

  const apps = catalog.data ?? [];
  return (
    <>
    <Popover
      label="What this chat can reach"
      open={open}
      onOpenChange={setOpen}
      trigger={(
        <button
          className={`chat-tb__btn${on ? " is-active" : ""}`}
          title="What this chat can reach"
          aria-label="What this chat can reach"
        >
          <KeyRound size={16} aria-hidden />
        </button>
      )}
    >
      <div className="ws-access">
      {failed && <p className="ws-access__note">{failed}</p>}
      {besidePage && (
        <div className="ws-access__group">
          <SectionLabel>{`${b.pageLabel || "Page"} autonomy`}</SectionLabel>
          <div role="radiogroup" aria-label={`${b.pageLabel || "Page"} autonomy`}>
            {LEVEL_ROWS.map(({ level, label, hint }) => (
              <button
                key={level}
                type="button"
                role="radio"
                aria-checked={levelShown(b.level) === level}
                className={`ws-access__level ws-place__view${levelShown(b.level) === level ? " is-on" : ""}`}
                onClick={() => {
                  setFailed(null);
                  void setLevel(threadId, level).then((ok) => {
                    if (!ok) setFailed("Couldn't change this: PUT /api/page-link failed.");
                  });
                }}
              >
                <Check size={14} aria-hidden className="ws-access__tick" />
                <span>
                  {label}
                  <span className="ws-place__hint">{hint}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="ws-access__group">
        <SectionLabel>Apps</SectionLabel>
        {catalog.isError && <p className="ws-access__note">Couldn't load the apps.</p>}
        {catalog.isSuccess && !apps.length && <p className="ws-access__note">No app offers tools to chats.</p>}
        {apps.map((app) => {
          const own = app.slug === ownSlug;
          const isOn = own || app.slug in access;
          const chosen = access[app.slug] ?? (own ? app.access.filter((o) => o.default).map((o) => o.id) : []);
          return (
            <div key={app.slug} className="ws-access__app">
              <Checkbox
                checked={isOn}
                disabled={own}
                onChange={(v) => toggleApp(app, v)}
                label={<span className="ws-access__name">{app.label}</span>}
              />
              {isOn && app.access.map((o) => (
                <Checkbox
                  key={o.id}
                  className="ws-access__option"
                  checked={chosen.includes(o.id)}
                  onChange={(v) => toggleOption(app, o.id, v)}
                  label={o.label}
                />
              ))}
            </div>
          );
        })}
      </div>
      </div>
    </Popover>
    </>
  );
}
