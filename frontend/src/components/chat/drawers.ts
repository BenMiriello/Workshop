// ONE auxiliary drawer at a time, selected from the toolbar.
//
// The inbox and the task list are both bounded panels that open between the
// toolbar and the log, and they used to be independent booleans — so both could
// be open at once, stacking 40vh of inbox on 30vh of tasks and leaving a sliver
// of conversation on a phone. They are alternatives, not layers: each answers
// "what else is going on around this chat", and wanting one is wanting to look
// away from the other. A single selection is what stops the top of the chat from
// growing without limit as more panels get added.
//
// Both panels stay MOUNTED when unselected, so they keep polling and reporting
// their counts and the toolbar badges stay live without the operator opening
// anything to find out whether they should.
export type ChatDrawer = "inbox" | "tasks" | null;
