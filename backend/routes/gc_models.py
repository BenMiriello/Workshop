"""Response models for the reaper (`GET /base-apps/gc/preview`, `POST
/base-apps/gc/run`), ADR 0023 clause 6.

WHY A SIBLING MODULE. These were inline in `base_apps.py`, which is 771 lines
and would cross `check_module_layout.py`'s 800-line big-file threshold once
`candidates`, `reaped`, `failed`, `cleared` and `checkpoints` were given real
types. That check calls itself "the mechanical trigger for that review, not a
line-count opinion", so this is the review, and the seam is obvious: garbage
collection is a distinct concern from serving base apps, it has its own service
module (`services/gc.py`), and nothing else in `base_apps.py` refers to it.

EVERY SHAPE HERE WAS READ OFF `services/gc.py`, not guessed. The reaper has no
persistent store to sample -- its report is computed per call -- so the
producers ARE the evidence:

  list_merged_worktrees   builds `{project_slug, branch_slug, branch, dir,
                          merged}`; `reap_merged_worktrees` puts that list under
                          `candidates` when reporting and under `reaped` when
                          applying, and `failed` is the same entry plus `error`.
  list_stopped_runtimes   builds `{slug, pid, port}`, which lands under
                          `candidates` or `cleared` by the same report/apply
                          rule.
  checkpoint_report       returns `describe_checkpoint_thinning`'s per-repo
                          description, whose own `candidates` are `{sha,
                          subject}` pairs. Its two early returns (not a repo,
                          git failed) emit the same keys with zeroes, so the
                          field set does not vary.

All of them allow extras. The reaper's entries are assembled inline rather than
by a normalizer, so a floor that cannot drop is the right shape: adding a key to
one of those dict literals should reach the client, not vanish at the boundary.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MergedWorktree(BaseModel):
    """A task worktree whose branch is already merged into the project's
    canonical branch, so removing it loses nothing. `merged` is always true --
    it is what qualified the entry -- and is carried so a row reads on its own."""

    model_config = ConfigDict(extra="allow")

    project_slug: str | None = None
    branch_slug: str | None = None
    branch: str | None = None
    dir: str | None = None
    merged: bool | None = None
    # Set only on `failed`: the teardown raised, and this is `Type: message`.
    error: str | None = None


class StoppedRuntime(BaseModel):
    """A dev-server entry whose process is gone. `pid` and `port` are what it
    HELD, which is the useful part of the report: a port still registered to a
    dead process is why the next start fails."""

    model_config = ConfigDict(extra="allow")

    slug: str | None = None
    pid: int | None = None
    port: int | None = None


class SquashableCheckpoint(BaseModel):
    """One checkpoint commit old enough to squash and not pointed at by a tag.
    `sha` is already shortened to 12 by the producer."""

    model_config = ConfigDict(extra="allow")

    sha: str | None = None
    subject: str | None = None


class CheckpointReport(BaseModel):
    """One repo's checkpoint-thinning description. REPORT ONLY: nothing here is
    ever rewritten, because rewriting history is exactly what the git-guard hook
    blocks for agents, and the producer says so in its own `note`."""

    model_config = ConfigDict(extra="allow")

    repo: str | None = None
    is_repo: bool | None = None
    total_checkpoints: int | None = None
    kept: int | None = None
    squashable: int | None = None
    candidates: list[SquashableCheckpoint] | None = None
    note: str | None = None


class GcWorktrees(BaseModel):
    """`candidates` on a preview, `reaped`/`failed` after an apply. `count` means
    candidates before and reaped after."""

    model_config = ConfigDict(extra="allow")

    applied: bool
    count: int
    candidates: list[MergedWorktree] | None = None
    reaped: list[MergedWorktree] | None = None
    failed: list[MergedWorktree] | None = None


class GcRuntimes(BaseModel):
    """`available: false` means the dev-server module is not loadable, which is a
    different answer from 'nothing to reap'."""

    model_config = ConfigDict(extra="allow")

    available: bool
    applied: bool
    count: int
    candidates: list[StoppedRuntime] | None = None
    cleared: list[StoppedRuntime] | None = None


class GcReport(BaseModel):
    """Checkpoint history is REPORTED, never rewritten here: rewriting history is
    exactly what the git-guard hook blocks for agents."""

    model_config = ConfigDict(extra="allow")

    merged_worktrees: GcWorktrees
    checkpoints: list[CheckpointReport]
    stopped_runtimes: GcRuntimes
