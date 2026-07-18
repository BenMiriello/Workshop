Second addendum to the orchestration-surface requirements, from the operator, verbatim intent. Feeds design tickets T0, B1, B5, B6, B7, B8.

## Shared scope context + cross-chat editing (B7, extends B6)
- This chat OR any other chat in the same scope must be able to edit any task or other entity within that scope.
- Before overwriting or changing plans, the editing chat must look for and think about conflicts and context.
- It must be able to check the context it came from in the other chat, and surface the question/conflict/clarification to the operator.
- The point: there is shared context within a scope, plus ways of handling situations and catching/resolving conflicts. Plan the full path and conditionality of this, don't leave it implicit.

## Resilience (B8)
- Plan ahead for compaction, changing models, or any other complication.
- Wants at least SOME handling, lighter is acceptable, for voice chat mode too.

## Card linking (B1)
- The cards that appear about 'adding tasks' and 'capturing a preference' are good, but should link to where that is pointing to AND back to the originating chat.
- If an action filed multiple tickets (e.g. the 14-at-once case), tapping the card should open a picker to select which one.

## Standing rule reaffirmed
- Scoping/decomposition/clarifying discussion happens in the MAIN THREAD before the chat decides to spawn a run, not inside the run.
- Migrate all systems to work with this; do not leave old, out-of-date UI or systems unsynchronized.