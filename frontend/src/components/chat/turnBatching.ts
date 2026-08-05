// How a queue of typed-ahead messages becomes turns.
//
// The queue used to send everything waiting as ONE turn, joined by blank lines.
// That is right for prose and wrong for commands, and the difference is not
// cosmetic: the backend only treats a turn as a command when it is a single
// line (`_is_raw_command` in routes/chat.py), because a multi-line blob handed
// to the CLI is read as one enormous command NAME and fails. So a queue holding
// ["/compact", "Continue"] went out as "/compact\n\nContinue" — plain text. The
// compaction never happened, and the model answered the "Continue" underneath a
// line of prose that looked like a command. From the operator's side: "instead
// of compacting it just processed my next message".
//
// The rule: a command is its own turn. Plain messages either side of it still
// batch, so the reason the join exists (N queued messages must not become N
// sequential turns, each waiting out the last) survives for the case it was
// written for.
//
// Only the FIRST segment is sent. The rest stay queued and the queue's own
// self-drain sends them when this turn ends — which is also the only ordering
// that can be honest, since a command has to run BETWEEN turns to run at all.

// A leading "/" alone does not make a command: "/usr/bin/foo is missing" is a
// message. Matches the composer's test in Chat.submitText and the backend's.
const COMMAND = /^\/[a-zA-Z][\w:-]*(\s|$)/;

export function isCommand(text: string): boolean {
  const s = text.trim();
  return COMMAND.test(s) && !s.includes("\n");
}

/** Split a queue into (what goes out as the next turn, what keeps waiting). */
export function splitFirstTurn(queue: string[]): [string[], string[]] {
  if (!queue.length) return [[], []];
  if (isCommand(queue[0])) return [[queue[0]], queue.slice(1)];
  const next = queue.findIndex(isCommand);
  if (next < 0) return [queue, []];
  return [queue.slice(0, next), queue.slice(next)];
}
