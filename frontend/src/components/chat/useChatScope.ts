import { useCallback, useEffect, useMemo, useState } from "react";
import { chatApi, type Provider } from "../../lib/chat";
import { DEFAULT_SCOPE, readProvider, resolveScope, writeProvider } from "../../lib/chatSelection";
import type { ChatMode } from "./chatScope";
import type { ChatProblem } from "./chatProblem";
import { MODEL_LABELS } from "./models";
import { nearestEffort } from "./effort";

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
  /** Effort levels the SELECTED model honours, backend-declared. [] hides the control. */
  effortLevels: string[];
  /** The operator's explicit choice, or "" for "never chosen — use the model default". */
  selectedEffort: string;
  setSelectedEffort: (level: string) => void;
  /** What the meter displays: the choice, else the model's default level. */
  effectiveEffort: string;
  /** Permission modes this backend offers. Runtime-served, not assumed. */
  modeOptions: string[];
  /** The explicit choice, or "" for "never chosen — use the platform default". */
  selectedPermissionMode: string;
  setSelectedPermissionMode: (mode: string) => void;
  /** What the picker displays: the choice, else the platform default. */
  effectivePermissionMode: string;
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
  onError: (problem: ChatProblem | null) => void;
}): ChatScope {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providersReady, setProvidersReady] = useState(false);
  const [activeProvider, setActiveProvider] = useState<string>(contextKey);
  const [selectedModel, setSelectedModel] = useState<string>("");
  // "" means the operator has not chosen: the turn is sent with no effort at all
  // and runs at the model's own default. Kept distinct from "the default level"
  // so an untouched chat spawns exactly the argv it spawns today.
  const [selectedEffort, setSelectedEffort] = useState<string>("");
  // "" means the chat has never chosen and runs at the platform default, which
  // is what every chat did before this control existed.
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<string>("");
  // Bumped by the Retry on the provider-list error block. The resolver below is
  // idempotent — it re-reads the list and re-resolves the scope from it — so
  // re-running it is exactly what "try that request again" means here.
  const [providerNonce, setProviderNonce] = useState(0);

  useEffect(() => {
    // Capture the one-shot URL intents synchronously before any await. Both this
    // effect and useChatThread's loader effect run in the same React flush (this
    // one first, per call order). useChatThread reads and nulls these refs
    // synchronously in its own synchronous prefix, so by the time this effect's
    // first await resolves the refs are already null. Capturing here closes the
    // window: the values live in local variables before the async portion begins.
    const linked = linkedThreadRef.current;
    const startingNew = startNewRef.current;
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
        if (!cancelled) {
          onError({
            text: `Couldn't load the model/assistant list: ${e}. The picker may be showing stale options.`,
            endpoint: scoped ? `/api/chat/providers?scope=${contextKey}` : "/api/chat/providers",
            retry: () => setProviderNonce((n) => n + 1),
          });
        }
      }
      if (cancelled) return;
      if (effectiveMode === "data" && list.length) {
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
          startNew: startingNew,
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
  }, [contextKey, effectiveMode, scoped, initialThreadId, startNew, providerNonce]);

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

  // The effort ladder belongs to the MODEL, so it changes in the same paint the
  // model does. No caps (an older backend, or a model it does not know) means no
  // control, which is the right failure: better absent than lying.
  const caps = activeProviderObj?.model_caps?.[selectedModel];
  const effortLevels = useMemo(() => caps?.efforts.levels ?? [], [caps]);
  const defaultEffort = caps?.efforts.default ?? "";
  // What the meter shows: the choice if there is one, else the model's default.
  // The default is displayed because it is what actually runs, not because it
  // was picked — `selectedEffort` stays empty and no flag is sent.
  const effectiveEffort = selectedEffort || defaultEffort;

  // A model switch can strip a level out from under a live choice (xhigh exists
  // on Opus 5 and not on Sonnet 4.6). Step to the nearest supported level rather
  // than silently running at a level the picker still shows.
  useEffect(() => {
    if (!selectedEffort) return;
    if (effortLevels.includes(selectedEffort)) return;
    setSelectedEffort(nearestEffort(selectedEffort, effortLevels));
  }, [effortLevels, selectedEffort]);

  // Which modes exist is a RUNTIME fact: `auto` is account-gated and can be
  // switched off by managed settings, so the list is served, never assumed.
  const modeOptions = useMemo(
    () => activeProviderObj?.modes?.options ?? [],
    [activeProviderObj],
  );
  const effectivePermissionMode =
    selectedPermissionMode || activeProviderObj?.modes?.default || "";

  return {
    providers, providersReady, activeProvider, chooseProvider, scope,
    activeProviderObj, modelOptions, selectedModel, setSelectedModel,
    effortLevels, selectedEffort, setSelectedEffort, effectiveEffort,
    modeOptions, selectedPermissionMode, setSelectedPermissionMode,
    effectivePermissionMode,
  };
}
