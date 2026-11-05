// Spoken actions: what a page or app can be told to do by voice. An app
// declares its own ("render", "add this to a group"), the Workshop shell hears
// them while the app is on screen and lists them in Commands under the app's
// name. The shape follows WebMCP's `registerTool` (name, title, description,
// an input, annotations, a function to run), with the phrases it is said by;
// the host is kept on the
// shell's window, as read aloud's is, so an app in a same-origin frame reaches
// it. Outside a Workshop shell there is no host and registering does nothing.

import { useEffect, useRef } from "react";

/** What a spoken action takes: a free text, or one of `choices` (better: the
 *  shell can list them, and a choice can be said anywhere in a phrase). For
 *  "add to {group}", `{ name: "group", choices: ["Sunsets", "Portraits"] }`. */
export type SpokenActionInput = {
  name: string;
  description?: string;
  choices?: readonly string[];
};

/** WebMCP's tool annotations. `consequentialHint`: it sends, deletes or
 *  spends, and cannot be taken back, so the shell always asks first.
 *  `readOnlyHint`: it only shows something. Either way an action runs at once
 *  unless it is consequential. */
export type SpokenActionAnnotations = { readOnlyHint?: boolean; consequentialHint?: boolean };

export type SpokenAction = {
  /** Unique among the actions this page declares (WebMCP's `name`). */
  id: string;
  /** What Commands lists it as. */
  title: string;
  /** One sentence, used to understand it when it is worded freely. */
  description: string;
  /** The ways to say it; the first is the one Commands shows. Absent: the
   *  title. With an input, a phrase holds at most one slot standing for it,
   *  `"add to {group}"` or `"put {group} first"`; one without a slot is
   *  followed by it. A free text input can only be the last words said; a
   *  slot anywhere else needs `choices`. */
  phrases?: readonly string[];
  /** Other wordings, learned from but never shown; a slot in one is filled
   *  from the choices. */
  examples?: readonly string[];
  input?: SpokenActionInput;
  annotations?: SpokenActionAnnotations;
  /** `arg`: the words said for the input, or the choice they name. */
  run: (arg?: string) => void | Promise<void>;
};

export interface SpokenActionsHost {
  /** Hear `actions` while `doc` is on screen. Returns the unregister. */
  register(actions: readonly SpokenAction[], doc: Document): () => void;
}

const KEY = "__workshopSpokenActions";
type HostWindow = Window & { [KEY]?: SpokenActionsHost };

function hostOn(win: Window): SpokenActionsHost | null {
  try {
    return (win as HostWindow)[KEY] ?? null;
  } catch {
    return null; // a cross-origin parent
  }
}

/** The shell's spoken actions, from this window or the nearest same-origin
 *  ancestor. */
export function spokenActionsHost(): SpokenActionsHost | null {
  if (typeof window === "undefined") return null;
  let win: Window = window;
  for (let i = 0; i < 4; i++) {
    const found = hostOn(win);
    if (found) return found;
    if (win.parent === win) break;
    win = win.parent;
  }
  return null;
}

/** The shell installs its registry here. Returns the uninstall. */
export function provideSpokenActions(host: SpokenActionsHost): () => void {
  (window as HostWindow)[KEY] = host;
  return () => {
    if ((window as HostWindow)[KEY] === host) delete (window as HostWindow)[KEY];
  };
}

/** Declare `actions` until the returned function is called. */
export function registerSpokenActions(actions: readonly SpokenAction[]): () => void {
  const host = spokenActionsHost();
  return host && actions.length ? host.register(actions, document) : () => {};
}

/** What an action is, without its function: when this changes, it is
 *  declared again. */
function shapeOf(actions: readonly SpokenAction[]): string {
  return JSON.stringify(actions.map(({ run: _run, ...rest }) => rest));
}

/** Declare `actions` while the component is mounted. They may be written
 *  inline: each runs the `run` of the latest render, and they are declared
 *  again only when what they are (ids, titles, phrases, choices) changes. */
export function useSpokenActions(actions: readonly SpokenAction[]): void {
  const latest = useRef(actions);
  latest.current = actions;
  const shape = shapeOf(actions);
  useEffect(() => {
    const declared = latest.current.map((a) => ({
      ...a,
      run: (arg?: string) => latest.current.find((x) => x.id === a.id)?.run(arg),
    }));
    return registerSpokenActions(declared);
  }, [shape]);
}

/** One action, declared while mounted; `null` declares nothing. */
export function useSpokenAction(action: SpokenAction | null): void {
  useSpokenActions(action ? [action] : []);
}
