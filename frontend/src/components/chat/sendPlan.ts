
// What happens to a message the operator just hit enter on. ONE decision, made
// in one place, against the store's state.
//
// This replaces a four-branch guard ladder in the send path (compacting /
// logBusy && isSlashCommand / streaming && threadId / logBusy) plus a 250ms
// timer in the queue's drain. Every branch was added for a real incident and
// each was correct on its own; together they asked two different questions
// ("is a turn streaming" and "does a turn own the tail of the log") whose
// answers diverged for one network round trip on every single turn, and a
// message sent in that window took the wrong branch.
//
// The state below has ONE question in it, `runActive`, plus `starting`, which is
// the window the timer used to paper over: a turn has been asked for and the
// POST has not come back yet. Setting it synchronously at send time is what
// makes the timer unnecessary — there is no interval in which the chat is
// honestly idle but a turn is on its way.

export type SendPlan =
  // Address the chat: the server delivers it to the run in flight, or takes it
  // onto the thread's queue if that run cannot be spoken to. Either way the
  // server has it before this resolves.
  | "inject"
  // Nothing is running here: this message becomes a turn.
  | "newTurn"
  // The one case the client must hold, because the server cannot: this chat
  // does not exist yet and a request that CREATES it is already in flight. A
  // second one would make a second thread. It goes the instant that POST
  // returns with a thread id, and it is durable in the meantime (useSendOutbox).
  | "awaitThread";

export type SendState = {
  // A /compact or /clear round trip is in flight. It is a server-side operation
  // on this thread and it moves the context boundary; a message sent before it
  // returns could land on the wrong side of that cut.
  compacting: boolean;
  // A turn has been requested and has not been confirmed yet.
  starting: boolean;
  // The server says a run is in flight on this thread.
  runActive: boolean;
  // Does this chat exist yet? A blank chat has no thread until its first send
  // creates one.
  hasThread: boolean;
};

/** What to do with a message the operator just hit enter on.
 *
 *  THERE IS NO "QUEUE" ANSWER HERE ANY MORE, and its absence is the fix.
 *
 *  This used to decide, in the browser, that a message would not be sent yet —
 *  because the run in flight takes no input (a slash command, a voice turn), or
 *  because the message was itself a command, which the CLI only executes
 *  between turns. Both facts are still true. What was wrong was WHO ACTED ON
 *  THEM: the message then sat in localStorage, and getting it out again needed
 *  a tab open on that thread when the turn ended. On 2026-08-12 the operator
 *  sent two long messages during a `/compact`, switched chats, and came back on
 *  another device. They were gone, and the server had never heard of them.
 *
 *  So the client POSTs unconditionally and the SERVER decides. It answers
 *  `queued: true` for exactly the cases this function used to withhold, and it
 *  sends them itself when the run ends. See backend/services/chat_queue.py.
 */
export function planSend(_text: string, s: SendState): SendPlan {
  if (!s.hasThread) return s.starting ? "awaitThread" : "newTurn";
  // A turn already exists to address, so let the server route it. `compacting`
  // is included deliberately: a fold is a server-side operation on this thread,
  // and the endpoint that receives the message is the one that knows where the
  // boundary ended up.
  if (s.starting || s.runActive || s.compacting) return "inject";
  return "newTurn";
}
