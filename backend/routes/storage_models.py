"""Response and request shapes for `routes/storage.py`, read off
`services/storage`."""
from __future__ import annotations

from pydantic import BaseModel


class AppPolicy(BaseModel):
    protect: bool
    cap_gb: float | None = None


class StoragePolicy(BaseModel):
    limit_gb: float | None = None
    keep_free_gb: float | None = None
    retention_days: int
    trash_days: int
    apps: dict[str, AppPolicy]


class PolicyChanges(BaseModel):
    """Only the keys present change. An app set to null drops its overrides."""
    limit_gb: float | None = None
    keep_free_gb: float | None = None
    retention_days: int | None = None
    trash_days: int | None = None
    apps: dict[str, AppPolicy | None] | None = None


class UsageItem(BaseModel):
    path: str
    kind: str
    bytes: int
    newest: float


class AppUsage(BaseModel):
    root: str | None = None
    total: int
    kinds: dict[str, int]
    items: list[UsageItem]


class Disk(BaseModel):
    mount: str
    total: int
    used: int
    free: int


class Skipped(BaseModel):
    app: str
    reason: str


class Usage(BaseModel):
    scanned_at: float
    seconds: float
    apps: dict[str, AppUsage]
    disks: list[Disk]
    skipped: list[Skipped]


class Removal(BaseModel):
    """One thing the retention pass removed or would remove. `done`, `error`,
    `trash_id` and `saved_ref` are present once a pass has been applied."""
    app: str
    path: str
    why: str
    bytes: int | None = None
    action: str | None = None
    branch: str | None = None
    head: str | None = None
    done: bool | None = None
    error: str | None = None
    trash_id: str | None = None
    saved_ref: str | None = None


class TrashSweep(BaseModel):
    removed: list[str] | None = None
    bytes: int | None = None
    would_remove: list[str] | None = None


class BranchFailure(BaseModel):
    project_slug: str
    branch: str
    error: str


class BranchService(BaseModel):
    """What the branch service's own merged-branch reaper did."""
    reaped: int
    failed: list[BranchFailure]


class CleanupPlan(BaseModel):
    """A retention pass's result. Checkout rows carry `action` (remove, park or
    keep); throwaway rows are all removals."""
    started_at: float
    applied: bool
    seconds: float
    throwaway: list[Removal]
    checkouts: list[Removal]
    skipped: list[Skipped]
    trash: TrashSweep
    branch_service: BranchService | None = None


class CleanupStarted(BaseModel):
    started: bool


class TrashEntry(BaseModel):
    id: str
    origin: str
    app: str
    kind: str
    reason: str
    bytes: int
    trashed_at: float
    slot: str


class StorageSummary(BaseModel):
    """`limit_bytes` is null when only each disk's keep-free floor bounds
    storage; `keep_free_bytes` is that floor per mount of the last scan."""
    policy: StoragePolicy
    limit_bytes: int | None = None
    keep_free_bytes: dict[str, int]
    scanning: bool
    usage: Usage | None = None
    last_pass: CleanupPlan | None = None


class ScanStarted(BaseModel):
    started: bool
    scanning: bool
