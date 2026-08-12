// ONE auxiliary drawer at a time, selected from the toolbar.
//
// There is exactly one now: the work drawer (WorkDrawer), reached from the
// toolbar's Tickets button. It got here in two steps.
//
// First the slot became exclusive. The inbox and the task list used to be
// independent booleans, so both could be open at once, stacking 40vh of inbox
// on 30vh of tasks and leaving a sliver of conversation on a phone. They are
// alternatives, not layers: each answers "what else is going on around this
// chat", and wanting one is wanting to look away from the other.
//
// Then the two collapsed into one panel with two zones, because they were never
// two surfaces to choose between: "what needs me" and "what exists" are two
// questions about the same thing and both fit above a transcript at once. What
// remains a rule is that the top of the chat cannot grow without limit, which
// is why this stays a single selection rather than becoming a set.
//
// The drawer stays MOUNTED while unselected, so it keeps polling the inbox and
// reporting its count and the toolbar badge stays live without the operator
// opening anything to find out whether it should.
export type ChatDrawer = "work" | null;
