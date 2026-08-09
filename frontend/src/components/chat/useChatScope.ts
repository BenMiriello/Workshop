import { useCallback, useEffect, useMemo, useState } from "react";
import { chatApi, type Provider } from "../../lib/chat";
import { DEFAULT_SCOPE, readProvider, resolveScope, writeProvider } from "../../lib/chatSelection";
import type { ChatMode } from "./chatScope";
import { MODEL_LABELS } from "./models";

// WHICH SCOPE THIS MOUNT BELONGS IN, and which model it will use — resolved
// ONCE, before any thread loads.
//
// `scope` is the thread list you can see, and it is the SAME axis as a thread's
// server-side context_key: list_threads filters on an exact match. So a thread
// already knows where it lives, and opening one can never be a question about
// which scope the picker happens to be left on.
//
// It used to be exactly that question, and that is the bug behind every variant
// of "my chats open blank / open the wrong one":
//   * activeProvider started at contextKey and then flipped asynchronously once
//     listProviders resolved, so the thread loader ran TWICE — the first pass
//     burned the one-shot URL intent under the wrong scope, and the second ran
//     with no intent left and fell back to whatever this scope was last on.
//     `?new=1` reopened an old chat; `?thread=<id>` opened a different one, or
//     nothing. Which way it broke depended on which of two async calls landed
//     first, which is why it looked intermittent.
//   * the remembered provider is keyed by contextKey, and nearly every chat
//     lives at /chat/general — so it was in practice ONE global "last scope I
//     picked" that every chat inherited.
//
// `providersReady` is the gate the thread loader waits on: never load a thread
// under a scope that is still being resolved.

export type ChatScope = {
  providers: Provider[];
  providersReady: boolean;
  activeProvider: string;
  chooseProvider: (key: string) => void;
  /** The thread list this mount can see. */
  scope: string;
  activeProviderObj: Provider | undefined;
  modelOptions: string[];
  selectedModel: string;
  setSelectedModel: (id: string) => void;
};

export function useChatScope({
  contextKey, effectiveMode, scoped, initialThreadId, startNew,
  startNewRef, linkedThreadRef, onError,
}: {
  contextKey: string;
  effectiveMode: ChatMode;
  scoped?: boolean;
  initialThreadId?: string;
  startNew?: boolean;
  // The one-shot URL intents. READ here (the intent decides the scope) but never
  // consumed — the thread loader does that, once, after the scope is settled.
  startNewRef: React.MutableRefObject<boolean>;
  linkedThreadRef: React.MutableRefObject<string | null>;
  onError: (msg: string) => void;
}): ChatScope {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providersReady, setProvidersReady] = useState(false);
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);
  const [selectedModel, setSelectedModel] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let list: Provider[] = [];
      try {
        list = await chatApi.listProviders(scoped ? contextKey : undefined);
        if (cancelled) return;
        setProviders(list);
      } catch (e) {
        // NOT non-essential, which is what this used to claim. This list is the
        // live source for the model picker; when it fails the picker silently
        // falls back to the frontend's hardcoded MODEL_LABELS, which can offer
        // models the backend does not serve.
        if (!cancelled) onError(`Couldn't load the model/assistant list: ${e}. The picker may be showing stale options.`);
      }
      if (cancelled) return;
      if (effectiveMode === "data" && list.length) {
        const linked = linkedThreadRef.current;
        let linkedThreadHome: string | null = null;
        if (linked) {
          // Read-only peek: the loader still fetches and adopts this thread.
          const t = await chatApi.getThread(linked).catch(() => null);
          if (cancelled) return;
          if (t) linkedThreadHome = String(t.context_key || DEFAULT_SCOPE);
        }
        const next = resolveScope({
          contextKey,
          knownScopes: list.map((p) => p.key),
          linkedThreadHome,
          startNew: startNewRef.current,
          remembered: readProvider(contextKey),
        });
        setActiveProvider(next);
        writeProvider(contextKey, next);
      }
      if (!cancelled) setProvidersReady(true);
    })();
    return () => { cancelled = true; };
    // The URL intents are refs on purpose: they are read, not depended on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey, effectiveMode, scoped, initialThreadId, startNew]);

  const chooseProvider = useCallback((key: string) => {
    setActiveProvider(key);
    writeProvider(contextKey, key);
  }, [contextKey]);

  const scope = effectiveMode === "build" ? contextKey : activeProvider;
  const activeProviderObj = useMemo(
    () => providers.find((p) => p.key === scope),
    [providers, scope],
  );
  const modelOptions = useMemo(
    () => activeProviderObj?.models ?? Object.keys(MODEL_LABELS),
    [activeProviderObj],
  );

  useEffect(() => {
    if (selectedModel && modelOptions.includes(selectedModel)) return;
    const def = activeProviderObj?.default_model;
    if (def && modelOptions.includes(def)) setSelectedModel(def);
    else if (modelOptions.length) setSelectedModel(modelOptions[0]);
  }, [activeProviderObj, modelOptions, selectedModel]);

  return {
    providers, providersReady, activeProvider, chooseProvider, scope,
    activeProviderObj, modelOptions, selectedModel, setSelectedModel,
  };
}
