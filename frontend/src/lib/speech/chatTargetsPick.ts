// "Chat model ...": pick a model by spoken name and set it on the chat, the
// way its model picker would, and set it back on a cancel.

import { modelLabel, orderModels } from "../../components/chat/models";
import { chatApi, type Provider } from "../chat";
import { readDraftNewest, writeDraftOutside } from "../draftWire";
import { findOpen, type ChatTarget } from "./chatTargetsCore";
import { matchModel } from "./chatTargetsModel";
import { findIn } from "./spans";

/** The model set, and the one it replaced (null when none was known). */
export type ModelPick = { id: string; label: string; previous: string | null };

async function providerFor(scope: string): Promise<Provider | null> {
  const list = await chatApi.listProviders(scope);
  return list.find((p) => p.key === scope) ?? list[0] ?? null;
}

async function currentModel(t: ChatTarget, provider: Provider | null): Promise<string | null> {
  const own = t.threadId
    ? (await chatApi.getThread(t.threadId).catch(() => null))?.model
    : (await readDraftNewest(t.draftKey)).settings.model;
  return own || provider?.default_model || null;
}

async function applyModel(t: ChatTarget, id: string | null): Promise<void> {
  const open = findOpen(t);
  if (open && id) { open.composer.setModel(id); return; }
  if (t.threadId) {
    if (id) await chatApi.setThreadModel(t.threadId, { model: id });
    return;
  }
  // A new chat keeps its picks with its draft until the chat exists.
  const d = await readDraftNewest(t.draftKey);
  const settings = { ...d.settings };
  if (id) settings.model = id;
  else delete settings.model;
  writeDraftOutside(t.draftKey, { text: d.text, attachments: d.attachments, settings });
}

/** Match `query` against the models this chat offers, by label and id, and
 *  set the best one. Null when nothing matches; nothing is changed then. */
/** The models the chat can use, in the order its picker lists them. */
export async function chatModels(t: ChatTarget): Promise<{ id: string; label: string }[]> {
  const open = findOpen(t);
  const ids = open ? open.composer.modelOptions() : (await providerFor(t.scope))?.models ?? [];
  return orderModels(ids).map((id) => ({ id, label: modelLabel(id) }));
}

export async function setChatModel(t: ChatTarget, query: string): Promise<ModelPick | null> {
  const open = findOpen(t);
  const provider = open ? null : await providerFor(t.scope);
  const list = await chatModels(t);
  const pick = matchModel(query, list) ?? findIn(query, (q) => matchModel(q, list));
  if (!pick) return null;
  const previous = open ? open.composer.model() || null : await currentModel(t, provider);
  await applyModel(t, pick.id);
  return { id: pick.id, label: pick.label, previous };
}

/** The model the chat uses now. */
export async function modelNow(t: ChatTarget): Promise<string | null> {
  const open = findOpen(t);
  if (open) return open.composer.model() || null;
  return currentModel(t, await providerFor(t.scope));
}

/** Set the chat's model to `previous` (undoing a pick) or back to a pick's id
 *  (redoing it). */
export async function restoreModel(t: ChatTarget, previous: string | null): Promise<void> {
  await applyModel(t, previous);
}
