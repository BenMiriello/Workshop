// The slash-command catalogue, and what "/" in the composer means.
//
// TWO KINDS, and the distinction is the whole design:
//
//   local        a Workshop chat action, handled by this app (new chat, tasks,
//                inbox, compact, deploy).
//   passthrough  a Claude Code command (/context, /cost, /model, …) forwarded
//                RAW to the agent's claude session, where the CLI executes it
//                natively. Build agents only — data and assistant chats are not
//                backed by a claude session, so there is nothing to forward to.
//
// Any unlisted "/x" still passes through when sent, so the full Claude command
// set works even though only the useful ones are surfaced in the autocomplete.
// This list is a menu, not a whitelist.
//
// /compact is deliberately LOCAL in every mode even though Claude Code has a
// command by that name: a Workshop chat's context lives in two places, and the
// stored transcript half is folded server-side. Treating it as a passthrough
// would compact the live session and leave the transcript untouched.
//
// /clear is LOCAL for the same reason, and is its OWN action — not an alias of
// /new. It used to be: both pointed at the same "abandon this thread, open a
// blank one" function, which is not what /clear means anywhere else — it
// means "drop the context, keep looking at it." A native CLI /clear would
// only reach the live session anyway: it empties the window but leaves the
// stored transcript's fold boundary alone, so the next fresh session would
// re-resume from disk and quietly undo the clear.

export type SlashCommand = {
  name: string;
  desc: string;
  available: boolean;
  kind: "local" | "passthrough";
  run?: () => void;
};

export type SlashActions = {
  newChat: () => void;
  deleteChat: () => void;
  toggleTasks: () => void;
  toggleInbox: () => void;
  popOut: () => void;
  showHelp: () => void;
  openDeploys: () => void;
  compact: () => void;
  clear: () => void;
};

export function buildSlashCommands({
  isBuild, hasThread, hasTaskScope, canExpand, compacting, actions,
}: {
  isBuild: boolean;
  hasThread: boolean;
  hasTaskScope: boolean;
  canExpand: boolean;
  compacting: boolean;
  actions: SlashActions;
}): SlashCommand[] {
  return [
    { name: "new", desc: "Start a new chat, leaving this one", available: true, kind: "local", run: actions.newChat },
    { name: "clear", desc: "Drop this chat's context, keeping the transcript", available: hasThread, kind: "local", run: actions.clear },
    { name: "delete", desc: "Delete the current chat", available: hasThread, kind: "local", run: actions.deleteChat },
    { name: "tasks", desc: "Toggle the task list", available: hasTaskScope, kind: "local", run: actions.toggleTasks },
    // The inbox is the task list's sibling drawer, so it gets the sibling
    // command. Leaving it out made the two panels look like different kinds of
    // thing in a place where they are deliberately the same kind.
    { name: "inbox", desc: "Toggle what agents are waiting on you for", available: hasThread, kind: "local", run: actions.toggleInbox },
    { name: "expand", desc: "Open this chat full screen", available: canExpand, kind: "local", run: actions.popOut },
    { name: "help", desc: "Show available commands", available: true, kind: "local", run: actions.showHelp },
    // A shortcut to where deploys live, not a deploy trigger: the actual action
    // stays on the page that owns it.
    { name: "deploy", desc: "Open deploys for this project (or the index)", available: true, kind: "local", run: actions.openDeploys },
    { name: "compact", desc: "Summarize + compact the conversation to free up context", available: !compacting, kind: "local", run: actions.compact },

    // WHAT SURVIVED THE MEASUREMENT. Every passthrough was run against the real
    // CLI on 2026-08-10 in the mode this chat actually uses
    // (`claude -p --output-format stream-json`). Most of the catalogue did not
    // work at all — see UNSUPPORTED_PASSTHROUGH below. These are the ones that
    // returned something real.
    //
    // `/context` is the prize: it returns a full markdown breakdown (tokens by
    // category, MCP tools per server, memory files, skills), which is why it is
    // worth a UI of its own rather than a wall of table in the transcript.
    { name: "context", desc: "Show what's currently in the context window", available: hasThread, kind: "passthrough" },
    // These two are not really commands, they are TASKS the CLI runs as a turn,
    // which is why they work when the TUI-only ones do not. Build only, because
    // both act on a project's working tree.
    { name: "init", desc: "Generate a CLAUDE.md for this project", available: isBuild, kind: "passthrough" },
    { name: "review", desc: "Review a pull request", available: isBuild, kind: "passthrough" },
  ];
}

// Commands the CLI does NOT run outside its interactive TUI. Measured, not
// assumed: each of these was sent to the real CLI in this chat's mode and
// answered, verbatim, "/<name> isn't available in this environment."
//
// They were in the catalogue for months, offered in the autocomplete, and every
// one cost a whole turn — a spawned process and a full prompt — to print that
// sentence. `/cost` and `/usage` are a near miss in the same family: they run,
// but answer "You are currently using your subscription to power your Claude
// Code usage" with no numbers in it at all, which is worse than nothing because
// it looks like an answer. The Usage page has the real figures.
//
// Anything here is answered LOCALLY and instantly instead of being sent.
export const UNSUPPORTED_PASSTHROUGH: Record<string, string> = {
  model: "Use the model picker in the toolbar.",
  mcp: "Not available here.",
  agents: "Not available here.",
  memory: "Not available here.",
  status: "Not available here.",
  doctor: "Not available here.",
  export: "Not available here.",
  resume: "Open the conversation from the chat picker.",
  cost: "See the Usage page for real numbers. The CLI's own answer has none.",
  usage: "See the Usage page for real numbers. The CLI's own answer has none.",
};

/** The plain answer for a command this environment cannot run, or null if it can
 *  run (or is not a bare command at all). Keeps a wasted turn from being spent
 *  to print an error the client already knows. */
export function unsupportedCommand(input: string): string | null {
  const text = input.trim();
  if (!text.startsWith("/") || text.includes("\n")) return null;
  const name = text.slice(1).split(/\s/)[0].toLowerCase();
  const why = UNSUPPORTED_PASSTHROUGH[name];
  return why ? `/${name} doesn't work in Workshop chat. ${why}` : null;
}

// What the composer's current text means for the two type-ahead pickers.
export function matchSlash(input: string, commands: SlashCommand[], buttonOpen: boolean) {
  // A command being typed is a single "/"-prefixed token, so a message that
  // merely CONTAINS a slash never opens the picker.
  const typed = input.startsWith("/") && !input.includes("\n");
  const query = typed ? input.slice(1).toLowerCase().trimStart().split(" ")[0] : "";
  // Typed: filter by the prefix. Button-opened: the whole list, because nothing
  // was typed to filter by.
  const matches = typed
    ? commands.filter((c) => c.available && c.name.startsWith(query))
    : buttonOpen
      ? commands.filter((c) => c.available)
      : [];
  return { typed, matches, show: (typed || buttonOpen) && matches.length > 0 };
}

// The @-mention being typed: the last "@word" run at the very END of the
// composer. Anchored to the end so editing earlier in a message never re-opens
// the picker, and suppressed once a complete mention exists because only the
// FIRST tag in a message is ever parsed server-side — addressing two agents in
// one breath has no coherent meaning.
const MENTION_TAIL = /(?:^|\s)@([a-z0-9-]*)$/;
const MENTION_COMPLETE = /(?:^|\s)@[a-z][a-z0-9-]*\s/;

export function matchMention<T extends { slug: string; name: string }>(
  input: string,
  candidates: T[],
): { query: string | null; matches: T[]; show: boolean } {
  const tail = MENTION_TAIL.exec(input);
  const query = tail && !MENTION_COMPLETE.test(input) ? tail[1] : null;
  const matches = query === null
    ? []
    : candidates.filter((a) => a.slug.startsWith(query) || a.name.toLowerCase().startsWith(query));
  return { query, matches, show: query !== null && matches.length > 0 };
}

// Replace the partial token in place: everything typed around it is preserved,
// and the trailing space means the next keystroke is the message rather than
// more of the name.
export function applyMention(input: string, slug: string): string {
  return input.replace(/@[a-z0-9-]*$/, `@${slug} `);
}
