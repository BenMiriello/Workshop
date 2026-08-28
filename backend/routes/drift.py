"""Is what you are looking at what is on disk: `/drift`.

The product-visible half of the apply contract (`docs/applying-changes.md`).
`scripts/apply.sh --check` has always been able to answer this and has only ever
answered it into one agent's terminal, which is why an installation could serve
a stale bundle for hours while every participant believed they had shipped: the
refusal that caused it ("not building: the bundle is stale because of
UNCOMMITTED UI work") was printed somewhere nobody was looking.

WHY THIS IS A READ AND NOT AN APPLY BUTTON. There is no POST here and there
should not be. The two states this reports have two different fixes and neither
is safe to fire from a browser: a `stale` surface is fixed by the apply step,
which restarts the backend serving this very request, and a `blocked` surface is
fixed by COMMITTING the files -- a decision belonging to whoever wrote them, not
to whoever noticed. Reporting the condition and naming the files is the whole
job; a button here would either restart the operator's session out from under
him or commit somebody else's half-written work.

`running_backend_id` COMES FROM THE PROCESS, NOT FROM A SECOND HASH. `main.py`
computes `_SOURCE_ID` once at startup and serves it on `/healthz`; that is what
"the code answering this request" means, and recomputing it here would answer
"the code on disk" twice and call it agreement.

Gated with the rest of the platform control surface in `main.py`
(`dependencies=_OPERATOR`).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import drift as drift_svc

router = APIRouter(prefix="/drift", tags=["drift"])


class Surface(BaseModel):
    """One thing that can serve something other than what is on disk.

    `state` is a closed set of four and each has a different fix:

      `current`      running matches source. Nothing is owed.
      `stale`        they differ and the tree is clean. The apply step fixes it.
      `blocked`      they differ AND uncommitted work under this surface means
                     the apply step will REFUSE. Rerunning apply does nothing;
                     the files have to be committed. This is the state that is
                     invisible today and the reason this router exists.
      `undecidable`  a source id could not be computed. Reported as its own
                     state rather than folded into `current`, because apply.sh
                     is explicit that "undecidable is NOT the same as in-sync
                     and must never be reported as it".

    `blocking_files` is populated only for `blocked`, and it is the answer to
    the only question the operator has at that moment: whose work is it.
    """
    name: str
    state: str
    running: str | None = None
    source: str | None = None
    blocking_files: list[str] = Field(default_factory=list)
    detail: str = ""


class DriftReport(BaseModel):
    """`canonical` IS LOAD-BEARING AND IS NOT A DETAIL.

    A linked git worktree builds into its own `frontend/dist`, while the edge
    serves the canonical checkout's dist by absolute path. So a UI change
    applied from a worktree is correct, built, and reaches nobody. apply.sh
    warns about it; a client that did not carry the flag would show a green
    "current" for a bundle the operator cannot see.
    """
    surfaces: list[Surface]
    blocked: bool
    stale: bool
    canonical: bool
    apply_command: str
    detail: str


@router.get("", response_model=DriftReport)
def get_drift() -> DriftReport:
    """What is running versus what is on disk, and what is holding the gap open."""
    from main import _SOURCE_ID

    return DriftReport(**drift_svc.report(running_backend_id=_SOURCE_ID))
