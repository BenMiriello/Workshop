"""A chat turn runs everything but deletes, which ask (services/tool_policy)."""
from __future__ import annotations

import json
import subprocess

import pytest

from services import build_streaming, local_turn, tool_policy as tp


@pytest.mark.parametrize("command", [
    "rm draft.txt", "rm -rf build", "cd x && rm -f a", "ls | xargs rm",
    "find . -name '*.tmp' -delete", "git clean -fd", "git reset --hard HEAD~1",
    "git branch -D old", "git push --force origin main", "git checkout -- src/",
    "sqlite3 db.sqlite 'DROP TABLE runs'", "psql -c 'delete from users where 1'",
    "curl -X DELETE http://127.0.0.1:9999/api/items/3", "bash -c 'rm -r out'",
    "sudo rm -r /usr/bin", "FOO=1 rm a", "truncate -s 0 log.txt", "dd if=/dev/zero of=disk.img",
])
def test_a_command_that_deletes_asks(command):
    assert tp.command_deletes(command), command
    assert tp.decision(tp.OPEN, "Bash", {"command": command})[0] == "ask"


@pytest.mark.parametrize("command", [
    "ls -la", "git status && git diff", "echo 'rm -rf' > note.txt", "grep -rn 'rm ' src",
    "python3 -m pytest", "mkdir -p out", "git push origin main", "mv a b",
    "gio trash old.txt", "git checkout main",
])
def test_a_command_that_does_not_delete_runs(command):
    assert tp.command_deletes(command) is None, command
    assert tp.decision(tp.OPEN, "Bash", {"command": command}) == ("allow", "does not delete")


def test_an_app_tool_asks_only_when_its_name_says_it_deletes():
    for name in ("mcp__pages__history_search", "mcp__pages__close_tab",
                 "mcp__library__clear_selection", "mcp__workshop__chat_access"):
        assert tp.decision(tp.OPEN, name, {})[0] == "allow", name
    for name in ("mcp__docs__delete_note", "mcp__pages__bookmarkRemove", "mcp__x__purge_cache"):
        assert tp.decision(tp.OPEN, name, {})[0] == "ask", name


def test_guard_leaves_everything_but_a_delete_to_the_mode():
    assert tp.decision(tp.GUARD, "Edit", {"file_path": "a.py"}) == (None, "")
    assert tp.decision(tp.GUARD, "mcp__pages__open_tab", {}) == (None, "")
    assert tp.decision(tp.GUARD, "Bash", {"command": "rm a"})[0] == "ask"


def test_a_rule_the_operator_gave_stops_the_asking():
    rules = ["Bash(rm build/*)", "mcp__docs__delete_note"]
    assert tp.decision(tp.GUARD, "Bash", {"command": "rm build/a.o"}, rules)[0] == "allow"
    assert tp.decision(tp.GUARD, "Bash", {"command": "rm src/a.c"}, rules)[0] == "ask"
    assert tp.decision(tp.OPEN, "mcp__docs__delete_note", {}, rules)[0] == "allow"
    assert tp.rule_covers("mcp__docs", "mcp__docs__delete_note", {})
    assert tp.rule_covers("Bash(git push:*)", "Bash", {"command": "git push -f"})


def test_auto_is_open_and_a_chosen_mode_guards():
    assert tp.posture(None) == tp.OPEN
    assert tp.posture("auto") == tp.OPEN
    assert tp.posture("acceptEdits") == tp.GUARD
    assert tp.posture("default") == tp.GUARD
    assert tp.posture("bypassPermissions") is None


def test_the_hook_is_the_module_itself_and_runs_anywhere(tmp_path):
    cmd = tp.hook_entry(tp.OPEN)["hooks"][0]["command"]

    def run(event, env=None):
        out = subprocess.run(cmd, shell=True, input=json.dumps(event), capture_output=True,
                             text=True, cwd=tmp_path, env=env, timeout=20)
        return json.loads(out.stdout)["hookSpecificOutput"] if out.stdout.strip() else None

    assert run({"tool_name": "Read", "tool_input": {}})["permissionDecision"] == "allow"
    assert run({"tool_name": "Bash", "tool_input": {"command": "rm x"}})["permissionDecision"] == "ask"
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "WORKSHOP_ALLOW_RULES": json.dumps(["Bash(rm x)"])}
    assert run({"tool_name": "Bash", "tool_input": {"command": "rm x"}}, env)["permissionDecision"] == "allow"


def test_a_local_turn_carries_the_policy_hook():
    settings = json.loads(local_turn.cli_flags(tools_narrowed=False, posture=tp.OPEN)[3])
    commands = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("tool_policy" in c or "def decision" in c for c in commands)
    bare = json.loads(local_turn.cli_flags(tools_narrowed=False)[3])
    assert len(bare["hooks"]["PreToolUse"]) == 1


def test_stop_asking_keeps_the_rule_for_this_session_only():
    assert build_streaming.session_rules(["Bash(rm a (1))", "mcp__x__delete"]) == [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm a (1)"}],
         "behavior": "allow", "destination": "session"},
        {"type": "addRules", "rules": [{"toolName": "mcp__x__delete"}],
         "behavior": "allow", "destination": "session"},
    ]


@pytest.mark.parametrize("command", [
    "nice -n 10 rm -rf build",
    "systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 nice -n 10 rm x",
    "timeout 30 rm x",
    "docker rm -f web",
    "kubectl delete pod web",
    "gh repo delete me/x --yes",
    "python3 -c 'import shutil; shutil.rmtree(\"x\")'",
    "python3 -c \"import os; os.remove('x')\"",
    "git checkout .",
    "git restore src/a.py",
    "psql -c 'DELETE FROM notes'",
])
def test_more_deletes_ask(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command})[0] == "ask"


@pytest.mark.parametrize("command", [
    "git restore --staged src/a.py",
    "git rm --cached secrets.env",
    "grep -rn 'delete from' backend",
    "docker ps",
    "gh pr list",
    "python3 -c 'print(1)'",
])
def test_lookalikes_do_not_ask(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command})[0] == "allow"


def test_a_rule_for_a_harmless_prefix_never_carries_a_chained_delete():
    rules = ["Bash(git status:*)"]
    chained = {"command": "git status && git reset --hard"}
    assert tp.decision(tp.GUARD, "Bash", chained, rules)[0] == "ask"
    assert tp.decision(tp.GUARD, "Bash", {"command": "git status -s"}, rules)[0] == "allow"
    own = ["Bash(rm build/*)"]
    assert tp.decision(tp.GUARD, "Bash", {"command": "rm build/x"}, own)[0] == "allow"
    assert tp.decision(tp.GUARD, "Bash", {"command": "rm build/x && rm -rf ~"}, own)[0] == "ask"


@pytest.mark.parametrize("command", [
    "python3 - <<'EOF'\ncases = ['rm -rf x', 'git reset --hard']\nEOF",
    "cat >> notes.txt <<EOF\nrm -rf build\nEOF\necho done",
    "echo 'a; rm b'",
    "cd tmp/chatreview && rm -f a.txt b.json",
    "rm -f /tmp/probe.json",
])
def test_data_and_scratch_are_not_deletes(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command})[0] == "allow"


def test_a_delete_after_a_heredoc_still_asks():
    cmd = "cat > a.txt <<EOF\nhello\nEOF\nrm -rf src"
    assert tp.decision(tp.OPEN, "Bash", {"command": cmd})[0] == "ask"
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm -rf tmp/../src"})[0] == "ask"
    assert tp.decision(tp.OPEN, "Bash", {"command": "cd tmp && rm -rf /etc/hostname"})[0] == "ask"


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a\n")
    (tmp_path / "src" / "b.py").write_text("b\n")
    (tmp_path / ".gitignore").write_text("*.log\n")
    git("add", ".")
    git("commit", "-qm", "one")
    return tmp_path


def test_what_git_can_bring_back_runs(repo):
    cwd = str(repo)
    for command in ("git rm src/a.py", "git rm -r src", "rm src/a.py", "git checkout HEAD~0 -- src/a.py",
                    "git restore src/a.py", "git checkout .", f"cd {cwd}/src && rm a.py",
                    f"git -C {cwd} rm -f src/b.py"):
        assert tp.decision(tp.OPEN, "Bash", {"command": command}, cwd=cwd) == ("allow", "does not delete"), command


def test_what_git_cannot_bring_back_asks(repo):
    cwd = str(repo)
    (repo / "src" / "a.py").write_text("unsaved\n")
    (repo / "src" / "new.py").write_text("untracked\n")
    (repo / "src" / "run.log").write_text("ignored\n")
    for command in ("rm src/a.py", "git checkout HEAD -- src/a.py", "git restore src/a.py",
                    "git rm -f src/a.py", "rm -r src", "rm src/new.py", "rm src/run.log",
                    "git checkout .", "rm /etc/hosts"):
        assert tp.decision(tp.OPEN, "Bash", {"command": command}, cwd=cwd)[0] == "ask", command
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm src/b.py"})[0] == "ask"


def test_the_hook_reads_the_turn_cwd_and_saves_before_a_discard(repo):
    event = {"tool_name": "Bash", "tool_input": {"command": "git rm -f src/a.py"}, "cwd": str(repo)}
    out = subprocess.run(["python3", "-c", tp.hook_source(), tp.OPEN], input=json.dumps(event),
                         capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    (repo / "src" / "a.py").write_text("unsaved\n")
    out = subprocess.run(["python3", "-c", tp.hook_source(), tp.OPEN], input=json.dumps(event),
                         capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    saved = subprocess.run(["git", "-C", str(repo), "for-each-ref", "refs/wip/discarded",
                            "--format=%(refname)"], capture_output=True, text=True).stdout.strip()
    show = subprocess.run(["git", "-C", str(repo), "show", f"{saved}:src/a.py"],
                          capture_output=True, text=True)
    assert show.stdout == "unsaved\n"


def test_removing_what_is_not_there_runs(tmp_path):
    cwd = str(tmp_path)
    assert tp.decision(tp.OPEN, "Bash", {"command": f"rm -rf {tmp_path}/nothing-here"}, cwd=cwd)[0] == "allow"
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm -rf not-made-yet"}, cwd=cwd)[0] == "allow"
    (tmp_path / "real").mkdir()
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm -rf real"}, cwd=cwd)[0] == "ask"
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm -rf real*"}, cwd=cwd)[0] == "ask"
    assert tp.decision(tp.OPEN, "Bash", {"command": "rm -rf nothing"})[0] == "ask"


@pytest.mark.parametrize("command", [
    "export GIT_INDEX_FILE=/tmp/idx_abl && rm -f $GIT_INDEX_FILE && git read-tree HEAD && git add a.py && git commit -m x",
    "export GIT_INDEX_FILE=$(mktemp -u /tmp/k2e-index.XXXX) && git read-tree HEAD && git commit -qm x && rm -f $GIT_INDEX_FILE",
    "export GIT_INDEX_FILE=/tmp/app-idx-$$ && git commit -qm x; rm -f $GIT_INDEX_FILE; unset GIT_INDEX_FILE",
    "I=/tmp/claude-1001/conceal.idx; rm -f $I; GIT_INDEX_FILE=$I git read-tree HEAD",
    "W=/workspace/tmp; F=${W}/kb-index; rm -f $F",
    "X=$(mktemp); rm -f \"$X\"",
    "systemd-run --user --wait --pipe -q bash -c 'cd /srv/x && ls'",
])
def test_a_commit_through_a_private_index_runs(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command}, cwd="/")[0] == "allow", command


@pytest.mark.parametrize("command", [
    "rm -f $GIT_INDEX_FILE",
    "F=$(cat files.txt); rm -f $F",
    "X=/tmp/a; X=$(cat list); rm -rf $X",
    "X=keep; rm -rf $X",
    "FOO=/tmp/a rm -rf keep",
    "X=/tmp/a; rm -f $X && rm -rf keep",
])
def test_a_variable_the_command_does_not_set_to_scratch_still_asks(command, tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.txt").write_text("a\n")
    assert tp.decision(tp.OPEN, "Bash", {"command": command}, cwd=str(tmp_path))[0] == "ask", command


def test_a_rule_check_reads_the_command_as_the_policy_does():
    cmd = {"command": "systemd-run --user -q bash -c 'cd /etc && rm -f hostname'"}
    assert tp.decision(tp.GUARD, "Bash", cmd, ["Bash(ls:*)"], cwd="/")[0] == "ask"


@pytest.mark.parametrize("command", [
    "cd /workspace/tmp && rm -rf dist-sched build.log", "rmdir server/tests/tmp", "rm -rf tmp",
    "rmdir src", "sudo rmdir /srv/x",
])
def test_a_scratch_directory_named_without_its_slash_is_scratch(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command}, cwd="/")[0] == "allow", command


# Under the turn's own rm (`turn_rm`), which moves what it removes to the trash.

@pytest.mark.parametrize("command", [
    "rm -rf build", "cd x && rm -f a", "ls | xargs rm", "unlink a", "bash -c 'rm -r out'",
    "find . -name '*.log' -exec rm {} +", "nice -n 10 rm -rf build", "\\rm draft.txt",
])
def test_the_turns_own_rm_is_not_a_delete(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command}, trash=True) == ("allow", "does not delete")


@pytest.mark.parametrize("command", [
    "/bin/rm -rf build", "sudo rm a", "shred -u key", "xargs /usr/bin/rm < list",
    "curl -X DELETE http://127.0.0.1:9999/api/items/3", "psql -c 'delete from users where 1'",
    "docker rm -f web", "git push --force origin main", "find . -delete && psql -c 'DROP TABLE x'",
])
def test_a_permanent_delete_still_asks_under_the_trash(command):
    assert tp.decision(tp.OPEN, "Bash", {"command": command}, trash=True)[0] == "ask", command


@pytest.mark.parametrize("command, says", [
    ("find . -name '*.tmp' -delete", "-exec rm"),
    ("python3 -c 'import shutil; shutil.rmtree(\"x\")'", "rm in the shell"),
    ("git clean -fd", "git clean -n"),
])
def test_a_permanent_delete_with_an_undoable_way_is_turned_back_with_it(command, says):
    verdict, reason = tp.decision(tp.OPEN, "Bash", {"command": command}, trash=True)
    assert verdict == "deny" and says in reason
    assert tp.decision(tp.OPEN, "Bash", {"command": command})[0] == "ask"


def test_the_trash_counts_only_where_its_installer_left_a_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "shim_dir", lambda: str(tmp_path))
    assert not tp.trash_active("abc-123")
    (tmp_path / "rm").write_text("#!/bin/sh\n")
    (tmp_path / "rm").chmod(0o755)
    assert not tp.trash_active("abc-123")
    (tmp_path / "on-abc-123").write_text("")
    assert tp.trash_active("abc-123")
    assert not tp.trash_active("../abc-123") and not tp.trash_active("")


def _keep(command, cwd):
    kept = tp.preserve(command, cwd)
    return tp.decision(tp.OPEN, "Bash", {"command": command}, cwd=cwd, kept=kept)[0]


def test_git_saves_what_it_would_discard_and_then_runs(repo):
    cwd = str(repo)
    git = lambda *a: subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True).stdout.strip()
    git("checkout", "-qb", "side")
    (repo / "src" / "c.py").write_text("c\n")
    git("add", ".")
    git("commit", "-qm", "side only")
    tip = git("rev-parse", "HEAD")
    git("checkout", "-q", "-")
    assert _keep("git branch -D side", cwd) == "allow"
    assert tip in git("for-each-ref", "refs/wip/branches", "--format=%(objectname)")
    (repo / "src" / "a.py").write_text("unsaved\n")
    assert _keep("git checkout -- src/a.py", cwd) == "allow"
    saved = git("for-each-ref", "refs/wip/discarded", "--format=%(refname)")
    assert git("show", f"{saved}:src/a.py") == "unsaved"
    (repo / "src" / "b.py").write_text("stashed\n")
    git("stash", "-q")
    stash = git("rev-parse", "stash@{0}")
    assert _keep("git stash drop", cwd) == "allow"
    assert stash in git("for-each-ref", "refs/wip/stash", "--format=%(objectname)")


def test_a_merged_branch_needs_nothing_saved(repo):
    cwd = str(repo)
    subprocess.run(["git", "-C", cwd, "branch", "done"], check=True)
    assert _keep("git branch -d done", cwd) == "allow"
    out = subprocess.run(["git", "-C", cwd, "for-each-ref", "refs/wip"], capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_force_removing_a_worktree_with_work_in_it_is_a_delete(repo, tmp_path):
    cwd = str(repo)
    tree = tmp_path / "copy"
    subprocess.run(["git", "-C", cwd, "worktree", "add", "-q", "--detach", str(tree)], check=True)
    (tree / "node_modules").symlink_to(tmp_path)
    command = {"command": f"git worktree remove --force {tree}"}
    assert tp.decision(tp.OPEN, "Bash", command, cwd=cwd)[0] == "allow"
    (tree / "probe.txt").write_text("work\n")
    assert tp.decision(tp.OPEN, "Bash", command, cwd=cwd)[0] == "ask"
    verdict, reason = tp.decision(tp.OPEN, "Bash", command, cwd=cwd, trash=True)
    assert verdict == "deny" and "git worktree prune" in reason
