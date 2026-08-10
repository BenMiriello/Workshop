"""GitHub Issues, through the `gh` CLI.

WHY `gh` AND NOT A GITHUB APP, FOR NOW. The design (§12 of
ticket-model-and-external-trackers-2026-08-07) is clear that `gh` does not survive
to a multi-user product: it holds one person's long-lived token in one home
directory. A GitHub App with the device authorization grant is the production
path, and it needs the operator to register an App — an action on github.com that
cannot be done from here.

So this adapter takes its token from a CREDENTIAL SOURCE rather than reaching for
`gh` itself (services/tracker_credentials). `gh` is one implementation of that
seam and works today with zero setup; `device_flow` is the other and slots in
without touching this file. That is the whole reason the seam exists, and it is
why building on `gh` now is not a dead end.

Every repo string passes `services.github.reject_blocked`, so an account
this installation has declared off limits cannot be reached through a tracker
before any call, per the operator's standing separation rule.
"""
from __future__ import annotations

import json
import logging
import subprocess

from services import github as gh_repo
from services.trackers import (Account, Filter, RemoteIssue, find_idem,
                               idem_marker, remote_state, strip_idem)

log = logging.getLogger(__name__)

_TIMEOUT = 60


class GitHubTracker:
    provider = "github"

    def __init__(self, binding: dict):
        self.repo = str(binding.get("target") or "").strip()
        if not self.repo or "/" not in self.repo:
            raise ValueError(f"github target must be owner/repo, got {self.repo!r}")
        gh_repo.reject_blocked(self.repo)
        self._env = None
        source = binding.get("credential_source") or "gh"
        if source != "gh":
            from services import tracker_credentials
            self._env = tracker_credentials.env_for(binding)

    # ---- plumbing -----------------------------------------------------------

    def _api(self, path: str, *, method: str = "GET",
             fields: dict | None = None, paginate: bool = False) -> object:
        args = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
        if method != "GET":
            args += ["--method", method]
        if paginate:
            args += ["--paginate"]
        args.append(path)
        stdin = None
        if fields is not None:
            # --input - keeps a body with newlines and quotes intact, which
            # -f key=value does not.
            args += ["--input", "-"]
            stdin = json.dumps(fields)
        try:
            res = subprocess.run(args, capture_output=True, text=True,
                                 timeout=_TIMEOUT, input=stdin, env=self._env)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"github api timed out: {path}") from e
        if res.returncode != 0:
            raise RuntimeError(
                f"github api failed ({res.returncode}) {method} {path}: "
                f"{(res.stderr or res.stdout or '').strip()[:400]}")
        out = (res.stdout or "").strip()
        if not out:
            return None
        if paginate:
            # --paginate concatenates JSON arrays; join them into one list.
            merged: list = []
            for chunk in _json_chunks(out):
                if isinstance(chunk, list):
                    merged.extend(chunk)
            return merged
        return json.loads(out)

    def _issue(self, raw: dict) -> RemoteIssue:
        body = raw.get("body") or ""
        return RemoteIssue(
            remote_id=str(raw.get("number")),
            key=f"#{raw.get('number')}",
            url=str(raw.get("html_url") or ""),
            title=str(raw.get("title") or ""),
            body=strip_idem(body),
            body_raw=body,
            state=str(raw.get("state") or "open"),
            closed_reason=raw.get("state_reason"),
            labels=[str(l.get("name")) for l in (raw.get("labels") or [])
                    if isinstance(l, dict) and l.get("name")],
            assignee=((raw.get("assignee") or {}) or {}).get("login"),
            updated=str(raw.get("updated_at") or ""),
            workshop_id=find_idem(body),
        )

    # ---- the eight ----------------------------------------------------------

    def whoami(self) -> Account:
        raw = self._api("user") or {}
        return Account(login=str(raw.get("login") or ""),
                       name=str(raw.get("name") or ""),
                       url=str(raw.get("html_url") or ""))

    def list_since(self, cursor: str | None,
                   filt: Filter) -> tuple[list[RemoteIssue], str]:
        """Issues matching the selection rule, changed since `cursor`.

        `since` is GitHub's own server-side filter, so a poll that finds nothing
        new costs one request. Pull requests are excluded: GitHub returns them
        from the issues endpoint and they are not tickets.
        """
        path = f"repos/{self.repo}/issues?state=all&per_page=100&sort=updated"
        if cursor:
            path += f"&since={cursor}"
        if filt.kind == "label" and filt.label:
            path += f"&labels={filt.label}"
        raw = self._api(path, paginate=True) or []
        issues = [self._issue(r) for r in raw
                  if isinstance(r, dict) and not r.get("pull_request")]
        # The next cursor is the newest updated_at we saw, so nothing is skipped
        # and at most one already-seen issue is re-read.
        newest = max((i.updated for i in issues), default=cursor or "")
        return issues, newest

    def get(self, remote_id: str) -> RemoteIssue:
        return self._issue(self._api(f"repos/{self.repo}/issues/{remote_id}"))

    def create(self, *, title: str, body: str, labels: list[str],
               idem: str) -> RemoteIssue:
        """Create, idempotently. The marker is searched for first, so a crashed
        create can never double-file — the single most damaging sync bug."""
        existing = self.find_by_workshop_id(idem)
        if existing is not None:
            return existing
        marked = f"{body}\n\n{idem_marker(idem)}".strip()
        raw = self._api(f"repos/{self.repo}/issues", method="POST",
                        fields={"title": title, "body": marked,
                                "labels": labels or []})
        return self._issue(raw)

    def find_by_workshop_id(self, task_id: str) -> RemoteIssue | None:
        """Search the repo for our marker. Used by `create` for idempotency and by
        a reconnect to re-link deterministically without an LLM."""
        q = f'repo:{self.repo} in:body "{idem_marker(task_id)}"'
        try:
            raw = self._api(f"search/issues?q={_quote(q)}") or {}
        except RuntimeError as e:
            log.debug("github marker search failed: %s", e)
            return None
        for item in (raw.get("items") or []):
            if isinstance(item, dict) and find_idem(item.get("body") or "") == task_id:
                return self._issue(item)
        return None

    def update(self, remote_id: str, patch: dict) -> RemoteIssue:
        fields: dict = {}
        if "title" in patch:
            fields["title"] = patch["title"]
        if "body" in patch:
            # Preserve our marker: it is how a reconnect re-links, and rewriting
            # the body without it would orphan the ticket.
            body = str(patch["body"] or "")
            if find_idem(body) is None and patch.get("task_id"):
                body = f"{body}\n\n{idem_marker(patch['task_id'])}".strip()
            fields["body"] = body
        if "labels" in patch:
            fields["labels"] = list(patch["labels"] or [])
        if not fields:
            return self.get(remote_id)
        return self._issue(self._api(f"repos/{self.repo}/issues/{remote_id}",
                                     method="PATCH", fields=fields))

    def transition(self, remote_id: str, status: str) -> RemoteIssue:
        state, reason = remote_state(status)
        fields: dict = {"state": state}
        if reason:
            fields["state_reason"] = reason
        return self._issue(self._api(f"repos/{self.repo}/issues/{remote_id}",
                                     method="PATCH", fields=fields))

    def comment(self, remote_id: str, markdown: str) -> None:
        self._api(f"repos/{self.repo}/issues/{remote_id}/comments",
                  method="POST", fields={"body": markdown})

    def labels(self) -> list[str]:
        raw = self._api(f"repos/{self.repo}/labels?per_page=100", paginate=True) or []
        return [str(l.get("name")) for l in raw
                if isinstance(l, dict) and l.get("name")]


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def _json_chunks(text: str):
    """Yield each top-level JSON value in a `gh --paginate` stream."""
    dec = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            return
        obj, end = dec.raw_decode(text, i)
        yield obj
        i = end
