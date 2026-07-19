"""Shared result types for deploy providers.

Every provider module implements:

    KIND: str
    LABEL: str
    TARGET_FIELDS: list[dict]                       # for the 'Add target' modal
    SOURCE_KINDS: list[str]                         # supported source.kind values

    def env_requirements() -> list[dict]:
        # [{key, required: bool, description}, ...]

    def preflight(deploy: dict, env: dict) -> Preflight:
        # credential + reachability check; must NOT deploy anything

    def build(deploy: dict, workdir: Path, log_write) -> BuildResult:
        # local build step; produces an artifact directory

    def upload(deploy: dict, build: BuildResult, env: dict, log_write) -> UploadResult:
        # ship the artifact to the target; returns an upload ref + tentative url

    def status(deploy: dict, upload_ref: str, env: dict) -> Status:
        # poll the upload_ref until terminal; short call, safe to loop

    def rollback(deploy: dict, to_run_id: str, env: dict, log_write) -> RollbackResult:
        # promote a prior successful deploy back to production

The dict shapes for `deploy` follow services.deploys.Deploy.to_json(). Provider
code must not mutate them.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Preflight:
    ok: bool
    missing_keys: list[str] = field(default_factory=list)
    detail: str | None = None


@dataclass
class BuildResult:
    ok: bool
    output_dir: str | None = None
    commit_sha: str | None = None
    detail: str | None = None


@dataclass
class UploadResult:
    ok: bool
    upload_ref: str | None = None
    url: str | None = None
    detail: str | None = None


@dataclass
class Status:
    # kind: pending | building | running | success | failure
    kind: str
    detail: str | None = None
    url: str | None = None


@dataclass
class RollbackResult:
    ok: bool
    url: str | None = None
    detail: str | None = None
