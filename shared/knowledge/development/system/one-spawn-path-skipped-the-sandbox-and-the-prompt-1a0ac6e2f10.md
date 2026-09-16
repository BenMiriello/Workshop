<!-- author: self -->
Measured 2026-09-16, after the operator reported a project agent saying it was
working "on the host at" an absolute platform path and running absolute-path
queries, while separately being unable to install a system package.

**The sandbox works. Exactly one spawn path skipped it.** `WORKSHOP_SCOPED_EXEC=1`
is set, bwrap is functional, and a chat turn and a ticket worker are both
confined: verified from inside a live turn, where the cwd is the mountpoint and
the platform's real host root does not exist in the mount namespace at all.

`services/background_runs.py` was the one exception, and it is the module behind
the `spawn_background` MCP tool. It called `subprocess.run` directly with
`--permission-mode bypassPermissions`, guarded only by a name-matched deny list
of three commands. Two facts made it reachable and dangerous:

- `chat_context._grants_workshop_tool` returns True for `spawn_background` on
  ANY build provider, so every project chat has it.
- `_scope_cwd` fell back to the platform's own checkout whenever the scope root
  did not resolve, and that fired in the ORDINARY case, not a freak one: a
  project whose tree does not exist yet, and a scope the dev-mode gate refuses.

So a project chat could run an unconfined agent with write access to the
platform's own source. This is the same defect, with the same fallback, that was
removed from `MakerAgentContextProvider.__init__` earlier; the comment there
explains the reasoning and the second removal cites it.

A full sweep found no others. Every other reference to the CLI under `scripts/`
(`tree_lease.sh`, `project_ralph.sh`, `preflight.sh`, `chat_event_bridge.py`) is
a comment or a `command -v` existence check.

**The prompt handed out the host paths the sandbox exists to hide.** Separately
and more insidiously, `instruction_contract` passed the provider's absolute host
directory as the capabilities block's `cwd`, and `_task_cli()` interpolated the
PLATFORM root into every project chat's persona. The agent did not discover
those paths; it was told them, in instructions that could not even be followed,
since neither path exists inside the confined view.

**Why the gate read zero over a live leak, which is the part worth remembering.**
`scripts/check_prompt_layers.py` walks `ast.Constant` string nodes. An f-string
is an `ast.JoinedStr` and its interpolated value is a `FormattedValue`, so a
runtime-interpolated path never appears as a literal. The script's own docstring
names a host path "in two personas" among the eleven violations it was written
to catch, and that violation was closed by replacing the literal with an
interpolation. **The fix removed the detection, not the leak**, and the gate was
green from then on. The lesson generalises past this gate: measure the artifact
that ships, not the source that produces it, which is the principle
`check_fresh_clone.sh` already embodies.

**Worst of both worlds on privilege, and it is a design question rather than a
bug.** Measured inside the sandbox: uid is the host user (the UID layer is off,
`WORKSHOP_TURN_USER` unset), `/usr` is a read-only bind, `sudo` is unusable, and
`apt-get` and `docker` are both on `PATH` and both cannot work. So an agent
carries host identity and cannot provision anything. In a container-per-agent
model it would be root inside its own container precisely BECAUSE it cannot
reach the host; here it is the inverse on both axes.

But "cannot install anything" is too strong, and stating it that way has already
cost one project real work: `pip` and `npm` DO work, and egress to the allowed
package hosts is open. A dependency with a userspace distribution is installable
today. The question to ask is whether a userspace distribution exists, not
whether an apt package does.
