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
    { name: "new", desc: "Start a new chat", available: true, kind: "local", run: actions.newChat },
    { name: "clear", desc: "Start a fresh chat (alias of new)", available: true, kind: "local", run: actions.newChat },
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

    { name: "context", desc: "Show what's currently in the context window", available: isBuild, kind: "passthrough" },
    { name: "cost", desc: "Show token usage and plan limits", available: isBuild, kind: "passthrough" },
    { name: "model", desc: "Show or switch the model", available: isBuild, kind: "passthrough" },
    { name: "memory", desc: "Show the agent's memory (CLAUDE.md)", available: isBuild, kind: "passthrough" },
    { name: "init", desc: "Generate a CLAUDE.md for this project", available: isBuild, kind: "passthrough" },
    { name: "review", desc: "Review a pull request", available: isBuild, kind: "passthrough" },
    { name: "status", desc: "Show session + account status", available: isBuild, kind: "passthrough" },
    { name: "agents", desc: "List configured subagents", available: isBuild, kind: "passthrough" },
    { name: "mcp", desc: "Show connected MCP servers", available: isBuild, kind: "passthrough" },
  ];
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
