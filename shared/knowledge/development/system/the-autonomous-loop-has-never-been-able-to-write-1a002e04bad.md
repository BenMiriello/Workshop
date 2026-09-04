<!-- author: self -->
Measured 2026-08-14 on the first end-to-end tier-1 run of `project_ralph.sh`/`ralph.sh` in a disposable project.

`ralph.sh` invokes `claude -p` with **no `--permission-mode`**, so it takes the CLI default, which can stop and ask. Nothing unattended can answer. `services/model_caps.py` states the consequence exactly: every mode except `bypassPermissions` is in `NEEDS_APPROVAL_CHANNEL`, and without the channel the CLI "accepts the mode and then silently auto-denies every gated call, which is the worst available outcome".

Observed: the worker was asked to create a one-line file. It tried `Write` three times and a Bash redirect once, was denied every time, wrote "All write operations require user permission approval ... contradicts the autonomous operation model" into the project's TODO, and exited claiming completion. No file was created on any attempt, for any task.

**So every previously green thing about the loop was measuring the part before the writes.**

The fix is one flag, `--permission-mode bypassPermissions`. It is documented at the call site in `ralph.sh` and deliberately NOT applied: ticket SEC-A1 records that an autonomous turn already runs as <service-user>, in the docker group, unsandboxed, with full tree write and the OAuth token, and this flag is what makes that reachable. Enable it together with a decision on A1's execution boundary.

Also fixed in the same run (commit 7eb84c1): `--dir` was used relative, and `ralph.sh` cds into the tree it drives, so a relative `--dir` meant a different directory before and after the cd. Symptom was three attempts in five seconds, every path "No such file or directory".

What the run DID prove, all previously untested: the reviewer gate refuses a task the worker declares complete; the no-progress detector parks rather than loops; nothing is falsely marked done; selection fails closed three separate ways (`execution`, stated boundaries, stated objective).

Test environment: `projects/autonomy-smoke/` (gitignored, own git repo, recovery is `rm -rf`).