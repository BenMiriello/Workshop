// The three commands you can still type, and the answer for the ones you cannot.
//
// THE CATALOGUE AND ITS PICKER ARE GONE. There was a "/" button, a drop-up menu,
// keyboard navigation for it, and a list of sixteen commands. Two things killed
// it:
//
//   1. Most of the list did not work. Measured against the real CLI on
//      2026-08-10 in the mode this chat uses, /model /mcp /agents /memory
//      /status /doctor /export /resume all answered "isn't available in this
//      environment", each after spawning a process and spending a full prompt to
//      say so. /cost ran but returned a sentence with no numbers in it.
//   2. What remained was duplicated by controls that are already on screen. A
//      menu whose entries are "the button two inches to your left" is a second
//      way to do everything and a first way to do nothing.
//
// WHAT SURVIVES IS TYPED, NEVER SUGGESTED. No picker opens, nothing
// autocompletes, and there is no button. If you know the word you can type it;
// if you do not, the control is visible anyway. These three earn it by being
// VERBS that act on this conversation right now, which is the only thing a
// keyboard shortcut is better at than a button.
//
// Everything else beginning with "/" is just a message. That is the honest
// default: this chat is not a terminal, and text the operator typed goes to the
// model rather than being silently swallowed by a command layer that does not
// exist any more.

export type TypedCommand = "compact" | "clear" | "new";

const ACCEPTED: Record<string, TypedCommand> = {
  compact: "compact",
  clear: "clear",
  new: "new",
};

/** Which of the three this text is, or null for anything else.
 *
 *  Exact and bare: "/compact" is the command, "/compact the logs please" is a
 *  message about compacting. A command that swallowed its own arguments would
 *  quietly drop the sentence the operator wrote. */
export function typedCommand(input: string): TypedCommand | null {
  const text = input.trim();
  if (!text.startsWith("/") || text.includes("\n")) return null;
  return ACCEPTED[text.slice(1).toLowerCase()] ?? null;
}

// Commands that used to exist here, and where their job went. Answering is
// cheap and stops a wasted turn: typing one of these used to spawn a process and
// spend a prompt so the CLI could reply that it does not work.
//
// This is the slash surface's remaining job, and it is a better one than being a
// menu: it is a MAP to the controls. Someone typing a command they remember from
// the terminal gets told where the thing lives.
const RETIRED: Record<string, string> = {
  model: "Use the model picker in the toolbar.",
  context: "Click the context percentage next to the composer.",
  mcp: "Click the context percentage next to the composer; MCP servers are listed there.",
  cost: "See the Usage page. The CLI's own answer has no numbers in it.",
  usage: "See the Usage page. The CLI's own answer has no numbers in it.",
  agents: "Not available here.",
  memory: "Not available here.",
  status: "Not available here.",
  doctor: "Not available here.",
  export: "Not available here.",
  resume: "Open the conversation from the chat picker.",
  delete: "Use the delete control in the toolbar.",
  tasks: "Use the tasks button in the toolbar.",
  inbox: "Use the inbox button in the toolbar.",
  deploy: "Deploys live on the project page.",
  expand: "Use the expand button in the toolbar.",
  help: "There are three commands: /compact, /clear and /new.",
};

/** The one-line answer for a command that no longer exists here, or null.
 *
 *  Null for the three that DO work and for ordinary text, so the caller can send
 *  anything else to the model unchanged. */
export function retiredCommand(input: string): string | null {
  const text = input.trim();
  if (!text.startsWith("/") || text.includes("\n")) return null;
  const name = text.slice(1).split(/\s/)[0].toLowerCase();
  const where = RETIRED[name];
  return where ? `/${name} isn't a command here. ${where}` : null;
}
