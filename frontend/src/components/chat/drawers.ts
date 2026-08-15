// ONE auxiliary panel at a time, selected from the toolbar.
//
// There is exactly one now: the Work panel (chat/work/WorkPanel), reached from
// the toolbar's Tickets button. It got here in three steps.
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
// Then the zones became four filter chips over ONE list, and the panel took the
// chat's ticket policy with it (2026-08-15). This type is what is left of the
// arrangement: a single nullable selection, held by Chat, read by the toolbar
// for the button's tone and by the panel for whether it renders.
//
// IT NO LONGER STAYS MOUNTED WHILE CLOSED. It used to, because it owned the
// inbox poll that kept the toolbar badge live. That poll is `useChatWork` in
// Chat now, read by the badge, the panel and every ticket card from one copy, so
// the panel is free to be nothing at all when it is shut.
export type ChatDrawer = "work" | null;
