<!-- author: self; chat: 4181312fd46e4cdd913d7d3dd18f880a -->
Measured 2026-09-16, after the operator released the autonomy brake and nothing happened.

**Two switches, and one of them was a no-op.** The brake (`autonomy_override`) and per-scope arming (`scope_execution`) were both correct. `work_driver` derived a resident runner from the armed scope, exactly as designed. The runner swept every 72 seconds and selected nothing, forever, while reporting itself healthy to the API.

**The cause was one missing flag.** `tasks.next_ready(unattended=True)` selects only tickets assigned to the worker the sweep NAMES:

    is_worker_assignee(assignee) and t.assignee == assignee

An unattended sweep that names nobody selects nothing. That rule is deliberate and correct: assigning a ticket is the grant, unassigning is the revocation, and it is what stops arming a scope from arming the whole backlog. `project_ralph.sh` has taken `--assignee agent:<slug>` since it was written. `queue_runner.sh`, which is what `work_driver` actually starts, had no such flag, so every sweep passed `assignee=None`. 80 ready tickets refused per sweep, logged to `.runtime/queue-runner.log`, which nobody reads.

**The general shape, which is the part worth keeping.** Every link in this chain was built, tested and wired. The defect lived exactly where two halves meet, in the argument one never passed to the other. That is the same shape as `launcher_cards()` swallowing `AppsUnavailable` and as the four-registry merge: not a missing feature, a missing connection, invisible because each half is individually correct.

**A second instance found in the same hour.** `ticket_readiness` requires a Boundaries section; `ticket_format.LABEL_RE` required a trailing delimiter after the label. A ticket writing `Boundaries` as a bare heading with text beneath failed the check while reading as complete to a person: 20 of 28 boundaries failures across the open queue. Producer and checker drifting apart is what that module's own docstring was written about, and it happened again in the same field, one shape over.

**Diagnostic that works.** Read `.runtime/queue-runner.log` rather than trusting residency. `unattended selection refused: ...` names the exact reason and the count of tickets that passed every other gate.