"""Storage classification, measurement and settings, on throwaway trees only."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from services.storage import kinds, measure, policy, report


def _repo(root, ignore, files):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("\n".join(ignore) + "\n")
    for rel, size in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    return root


def test_conventional_dirs_classify_and_unknown_ignored_files_are_work(tmp_path):
    root = _repo(tmp_path, ["node_modules/", "tmp/", "data/", ".mypy_cache/"], {
        "src/a.py": 10, "node_modules/p/tmp/x": 10, "tmp/probe": 10,
        "data/db": 10, ".mypy_cache/3.8/c": 10})
    got = {i.rel: i.kind for i in kinds.classify(root)}
    assert got == {"node_modules": "rebuildable", "tmp": "throwaway",
                   "data": "work", ".mypy_cache": "rebuildable"}


def test_manifest_declarations_win_and_claim_their_subtree(tmp_path):
    root = _repo(tmp_path, ["data/", "out/"], {"data/db": 1, "data/thumbs/t": 1,
                                                "out/tmp/x": 1})
    (root / "workshop.app.json").write_text(json.dumps({"storage": {
        "rebuildable": ["data/thumbs", "/etc", "../up"], "throwaway": ["out"]}}))
    got = sorted(kinds.classify(root))
    assert got == [("data", "work"), ("data/thumbs", "rebuildable"), ("out", "throwaway")]


def test_walk_counts_a_hardlink_once_and_skips_nested_units(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "inner").mkdir()
    (tmp_path / "a" / "f").write_bytes(b"x" * 65536)
    os.link(tmp_path / "a" / "f", tmp_path / "a" / "g")
    (tmp_path / "a" / "inner" / "h").write_bytes(b"x" * 65536)
    seen: set = set()
    size, _ = measure.walk(tmp_path / "a", {str(tmp_path / "a" / "inner")}, seen)
    assert 65536 <= size < 2 * 65536
    assert measure.walk(tmp_path / "a", set(), seen)[0] >= 65536


def test_sandbox_spelling_rebases_onto_the_host_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    (tmp_path / "projects" / ".wt").mkdir(parents=True)
    assert measure._host(measure.MOUNTPOINT + "/projects/.wt") == tmp_path / "projects" / ".wt"
    assert measure._host("/nowhere/at/all") == measure.Path("/nowhere/at/all")


def test_a_path_that_cannot_be_statted_is_kept_as_spelled(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o000)
    try:
        assert measure._host(str(locked / "Letta.log")) == locked / "Letta.log"
    finally:
        locked.chmod(0o755)


def test_policy_validates_and_the_plan_quota_caps_the_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path))
    monkeypatch.delenv("WORKSHOP_STORAGE_QUOTA_GB", raising=False)
    assert policy.read() == policy.DEFAULTS
    assert policy.limit_bytes(policy.read()) is None
    doc = policy.update({"limit_gb": 500, "apps": {"a": {"protect": True}}})
    assert doc["apps"] == {"a": {"protect": True, "cap_gb": None}}
    monkeypatch.setenv("WORKSHOP_STORAGE_QUOTA_GB", "100")
    assert policy.limit_bytes(policy.read()) == 100 * policy.GB
    assert policy.update({"apps": {"a": None}})["apps"] == {}
    for bad in ({"retention_days": 0}, {"limit_gb": -1}, {"nope": 1}, {"trash_days": True}):
        with pytest.raises(ValueError):
            policy.update(bad)


def test_keep_free_defaults_to_a_share_of_the_disk_between_a_floor_and_a_cap():
    doc = dict(policy.DEFAULTS)
    assert policy.keep_free_bytes(doc, 100 * policy.GB) == 20 * policy.GB
    assert policy.keep_free_bytes(doc, 300 * policy.GB) == 30 * policy.GB
    assert policy.keep_free_bytes(doc, 1800 * policy.GB) == 50 * policy.GB
    assert policy.keep_free_bytes({**doc, "keep_free_gb": 5}, 1000 * policy.GB) == 5 * policy.GB


def test_a_scan_is_saved_and_summarised(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / "rt"))
    app = _repo(tmp_path / "app", ["tmp/"], {"tmp/x": 4096, "main.py": 4096})
    registry = tmp_path / "rt" / "projects.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"projects": {"app": {"dir": str(app)}}}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(registry))
    monkeypatch.setattr(measure, "TOOL_CACHES", ())
    assert not report.scanning()
    report.run()
    usage = report.summary()["usage"]
    assert usage["apps"]["app"]["kinds"]["throwaway"] >= 4096
    assert usage["apps"]["app"]["kinds"]["work"] >= 4096
    assert usage["disks"]
