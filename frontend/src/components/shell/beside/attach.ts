// Taking something into the chat beside a page: an app's `workshop/v1/attach`
// (its "Discuss" action) or Add to chat on selected text.
//
// Text, links, pages and records become chips (contextItems.ts). Images are
// fetched and handed to the composer as files, so they upload as ordinary image
// attachments and the model sees them. A request that grants apps creates its
// conversation first and puts the grant on it, because the first turn already
// has to have those apps.

import { chatApi } from "../../../lib/chat";
import { beside, itemId } from "./besideStore";
import type { ContextItem } from "./contextItems";
import type { AttachItem, AttachRequest } from "./protocol";

type ChatApp = { slug: string; access: { id: string; default: boolean }[] };

async function fileFrom(item: AttachItem, n: number): Promise<File | null> {
  if (!item.src) return null;
  try {
    const url = new URL(item.src, window.location.href);
    if (url.protocol !== "data:" && url.origin !== window.location.origin) return null;
    const r = await fetch(url.href);
    if (!r.ok) return null;
    const blob = await r.blob();
    if (!blob.type.startsWith("image/")) return null;
    const ext = blob.type.split("/")[1]?.split("+")[0] || "png";
    const base = (item.title || `image-${n + 1}`).replace(/[^\w.-]+/g, "-").slice(0, 60);
    return new File([blob], `${base}.${ext}`, { type: blob.type });
  } catch {
    return null;
  }
}

function toItem(item: AttachItem, source: string): ContextItem {
  return {
    id: itemId(), kind: item.kind === "image" ? "link" : item.kind, source,
    title: item.title, text: item.text, url: item.url || (item.kind === "image" ? item.src : undefined),
    app: item.app, ref: item.id,
  };
}

async function grantFor(threadId: string, slugs: string[], merge: boolean): Promise<void> {
  const catalog = await fetch("/api/chat-apps").then((r) => (r.ok ? r.json() as Promise<ChatApp[]> : []));
  let access: Record<string, string[]> = {};
  if (merge) {
    const r = await fetch(`/api/chat-apps/${encodeURIComponent(threadId)}`);
    if (r.ok) access = ((await r.json()) as { access: Record<string, string[]> }).access;
  }
  for (const slug of slugs) {
    const app = catalog.find((a) => a.slug === slug);
    if (app && !(slug in access)) access[slug] = app.access.filter((o) => o.default).map((o) => o.id);
  }
  await fetch(`/api/chat-apps/${encodeURIComponent(threadId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ access }),
  });
}

export async function receiveAttach(req: AttachRequest, source: string, pageScope: string): Promise<void> {
  const scope = req.scope || pageScope;
  let threadId: string | null = null;
  try {
    if (req.newChat && req.apps.length) {
      const t = await chatApi.createThread(scope);
      threadId = t.id;
      await grantFor(t.id, req.apps, false);
    } else if (!req.newChat && req.apps.length && beside.get().thread) {
      await grantFor(beside.get().thread!, req.apps, true);
    }
  } catch {
    // The chat still opens with what was attached; only the grant is missing,
    // and the Access menu shows it is.
  }
  if (req.newChat) beside.open({ scope, threadId, startNew: !threadId });
  else beside.open(beside.get().scope ? {} : { scope });
  const images = req.items.filter((i) => i.kind === "image");
  const files = await Promise.all(images.map(fileFrom));
  const chips = [
    ...req.items.filter((i) => i.kind !== "image").map((i) => toItem(i, source)),
    // An image that could not be fetched still goes, as its address.
    ...images.filter((_, n) => !files[n]).map((i) => toItem(i, source)),
  ];
  const ready = files.filter((f): f is File => !!f);
  // Sent on its own: the draft and anything already attached stay put.
  if (req.sendOnly) {
    beside.sendOnly(ready, chips);
    return;
  }
  beside.addItems(chips);
  beside.toComposer(ready, req.prompt || null, req.send);
}

/** Add to chat on selected text: open the drawer beside this page and attach
 *  it. `doc` is the document the selection is in, which names where it came from. */
export function addSelection(text: string, doc: Document | null) {
  const { scope, pageScope, pageLabel } = beside.get();
  beside.open(scope ? {} : { scope: pageScope });
  let title = "";
  try { title = (doc?.title || "").trim(); } catch { /* gone */ }
  beside.addItems([{
    id: itemId(), kind: "text", source: pageLabel || title || "this page",
    title: title && title !== pageLabel ? title : undefined, text,
  }]);
  beside.toComposer([], null, false);
}
