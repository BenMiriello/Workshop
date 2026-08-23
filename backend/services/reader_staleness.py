"""Step B8, second half: telling a reader that the tree moved under it.

`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` §11.

    A session reading a tree another session is writing will confidently
    describe a file that changed two minutes ago. Contention is loud; staleness
    is silent.

WHAT IS DELIBERATELY NOT HERE, because §11 is explicit that it is already
handled or cannot be:

  * NO INVALIDATION PROTOCOL. Half of this is already solved: for EDITS the
    harness enforces read-before-write and errors when a file changed since it
    was read. The unhandled residual is the model REASONING from a stale read it
    will never re-take, and no invalidation fixes that, because the read is
    already sitting in the context window. Cache-invalidating a context window
    is not a thing; the read cannot be recalled.

  * NO NEW CHANNEL. §11 names the only real remedy -- "a message into the
    session (`the tree moved: N files changed since your last read`)" -- and
    says what it is: "a presence-driven steer, not a new protocol". So delivery
    is `services/control.steer`, the channel that landed with B6/B7, and this
    module contributes exactly two things it does not have: the count, and the
    sentence.

  * NO POLLER. Nothing here runs on a timer, and no other module calls
    `notify()` yet. Who decides a reader should be told is a presence-driven
    decision on top of B4/B5, and wiring one into the live turn loop is a change
    to how every session behaves -- which is the same attended decision as the
    B1 flag itself. The mechanism ships tested; arming it is deliberate.

WHY A SHA AND NOT A TIMESTAMP. "Since its last read" has to mean something
checkable. A wall-clock cut would count commits made before the session read
anything and miss a write made in the same second. The sha the reader was
looking at is exact, is what git can diff from, and is what `tree_access.decide`
can hand a caller at mount time.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

__all__ = ["files_changed_since", "message", "notify", "notify_readers"]


def files_changed_since(repo_path: str, since_sha: str | None,
                        *, ref: str = "HEAD") -> int | None:
    """How many files changed between the reader's sha and the tree now.

    None means "cannot say" -- an unreadable repo, a sha this tree has never
    heard of, git not answering -- and every caller below treats None as
    silence. A staleness message whose number is a guess is worse than no
    message: it teaches the model to discount the next one.
    """
    if not repo_path or not since_sha:
        return None
    try:
        from services import git_service
        # `^{commit}` and not a bare sha: `git rev-parse --verify <40 hex>`
        # succeeds on any well-formed sha whether or not this repository has
        # ever had the object, so a bare verify would pass and the diff would
        # then come back empty -- reporting "nothing changed" about a tree we
        # cannot measure at all.
        if git_service.rev_parse(repo_path, f"{since_sha}^{{commit}}",
                                 verify=True) is None:
            return None
        changed = git_service.diff_name_status(repo_path, str(since_sha), ref)
    except Exception as exc:
        log.info("staleness for %s could not be measured: %s", repo_path, exc)
        return None
    return len({row.get("path") for row in changed if row.get("path")})


def message(n_files: int, *, branch: str | None = None) -> str:
    """§11's sentence. One fact, no instruction.

    It says what happened and stops. Telling the model what to do about it
    ("re-read before you answer") would be this module inventing policy for a
    turn it knows nothing about, and the harness's read-before-write check
    already enforces the part that can be enforced.
    """
    where = f" of {branch}" if branch else ""
    files = "file" if n_files == 1 else "files"
    return (f"The tree{where} moved: {n_files} {files} changed since your last "
            f"read. Anything you read before this may describe an older version.")


def notify(session_id: str, *, repo_path: str, since_sha: str | None,
           branch: str | None = None, actor: str | None = None) -> dict | None:
    """Steer one reader with the staleness message, or do nothing.

    Returns None -- not an error, not an empty dict -- when there is nothing
    true to say: no measurable diff, or a diff of zero files. Silence is the
    correct output for a reader that is not stale, and it is the common case.
    """
    n = files_changed_since(repo_path, since_sha)
    if not n:
        return None
    from services import control
    return control.steer(str(session_id), message(n, branch=branch),
                         actor=actor or "staleness")


def notify_readers(readers: list[dict], *, repo_path: str,
                   branch: str | None = None,
                   actor: str | None = None) -> list[dict]:
    """`notify` over the sessions reading this tree.

    `readers` is `[{"session_id": ..., "since_sha": ...}]` -- supplied by the
    caller rather than looked up here, because "who is reading this tree" is
    `presence.directory`'s question and this module is not going to answer it a
    second way.

    One reader that cannot be steered does not stop the rest: a session that
    went away between the directory read and this call is the ordinary case, not
    a failure of the sweep.
    """
    out = []
    for reader in readers or []:
        session_id = reader.get("session_id")
        if not session_id:
            continue
        try:
            result = notify(session_id, repo_path=repo_path,
                            since_sha=reader.get("since_sha"), branch=branch,
                            actor=actor)
        except Exception as exc:
            log.info("could not steer %s about staleness: %s", session_id, exc)
            continue
        if result is not None:
            out.append(result)
    return out
