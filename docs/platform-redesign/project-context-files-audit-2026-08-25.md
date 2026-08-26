# Project-context files: audit, the field standard, and options

Status: research + audit, 2026-08-25. No implementation. Prompted by the operator
asking what PROJECT.md is, who should write it, where it should live, and whether
it should be required, protected, or renamed. Explicit constraint: design to the
GENERALIZED standard, not to one operator's workflow.

Companion: [chat-context-and-compaction-2026-08-25.md](chat-context-and-compaction-2026-08-25.md)
(same session, the state-injection cleanup).

---

## 1. The one finding that reframes everything

The field separates THREE artifacts that Workshop has collapsed into one or scattered:

- **Rules / conventions**: durable, always-loaded, human-authored, about HOW to
  work. This is AGENTS.md / CLAUDE.md / Cursor rules / Copilot instructions.
- **Spec / plan / manifest**: per-goal, about WHAT to build and where to start.
  This is the spec-driven-development artifact (GitHub Spec-Kit's spec.md → plan.md
  → tasks.md, AWS Kiro). Explicitly NOT the rules file.
- **README**: for humans.

AGENTS.md is now the cross-vendor standard for the first one: it came out of OpenAI,
Cursor, Google, Amp, and Factory agreeing on a shared format in 2025, and is now
stewarded by the Agentic AI Foundation under the Linux Foundation, read by 20+ tools
(Codex, Jules, Aider, Copilot, VS Code) across 60k+ repos. It is "a README for
agents," plain Markdown, no schema, and OPTIONAL everywhere. Claude Code's own docs
recommend importing/symlinking AGENTS.md rather than keeping a separate CLAUDE.md.

Workshop's PROJECT.md is trying to be the manifest (goal/plan) AND the always-loaded
orientation, while AGENTS.md is our rules file and CLAUDE.md is repo rules that the
chat backend never even loads. That overlap is the root of the confusion, not the
existence of PROJECT.md.

## 2. What the field converged on (answers to the operator's questions)

**(a) Required or convention?** Convention, everywhere. No mainstream tool refuses to
run without one. On importing a repo that has none, nothing breaks; tools offer a
generator (Claude Code `/init`, which also reads existing Cursor/Copilot/AGENTS files
to seed one). So: do not require a manifest.

**(b) Who writes it, and are user and agent separated?** The dominant model:
the human writes the standing-context file; the agent gets a SEPARATE store for what
it learns. Claude Code states it as a table: CLAUDE.md is written by "You"; auto
memory is written by "Claude" and lives in a different directory. Windsurf is
identical: human-written Rules vs agent-written Memories. "Remember this" goes to the
agent's store, not into the human's file, unless the user explicitly says "add to
CLAUDE.md." So the convergent answer is: standing rules = human; accumulated memory =
agent, physically separated.

**(c) Where does it live: repo or our system?** Two tiers. Project context lives IN
the repo (committed, travels with the code, nearest-file-wins in a monorepo).
Personal/global preferences live in the user's home/tool config, not committed, and
the project file overrides the global one. Claude Code, Cursor, Copilot, and Windsurf
all implement exactly this split.

**(d) Protection / who may edit a shared file?** This is the one genuinely
DIVERGENT area. Real mechanisms in use, strongest first:
- Aider loads CONVENTIONS.md as READ-ONLY so the model consults but cannot rewrite
  the rules mid-session. The clearest "protected reference file" pattern.
- Author-separation by file (Claude Code, Windsurf): the human file and the
  agent-written store are different files, so the agent's writes never touch the
  human's file. Protection by construction, no lock needed.
- Import-approval gate (Claude Code): a one-time approval before loading an external
  import, and a managed-policy file a user cannot exclude.
- The majority default: no lock at all, the file is committed and agent edits show up
  as a reviewable git diff.
There is NO field-wide standard for locking a shared context file against agent
edits. The common thread is author-separation plus git review, not a permission gate.

## 3. Audit: what Workshop has today

Nine things play a context/manifest/memory role. Location, writer, and when-loaded:

- **PROJECT.md** (`chat_context.py` live_context + persona). Per scope: `projects/<slug>/`,
  repo root for `development`. System scaffolds a blank; after that it is agent- or
  operator-hand-edited. The "append a decision note to PROJECT.md" line is a persona
  INSTRUCTION with no code behind it. Loaded every turn as orientation (now the only
  thing live_context injects, after the 2026-08-25 cleanup). Committed with the tree.
- **TODO.md** generated export of the task store (`.tasks.json`, or Postgres for
  `development`/`workshop`). One-way render, never read back into a prompt; status
  reaches the model through the ambient digest instead. Hand-edits are healed on
  file-backed scopes. System-authored.
- **AGENTS.md** injected into the persona as "Project-local agent rules." Per-scope
  file. System scaffolds, then human/agent edits. First-turn (stable prefix).
- **CLAUDE.md** (`/workspace/CLAUDE.md`, `frontend/CLAUDE.md`). The chat backend does
  NOT load these into the prompt. They reach the model only through the Claude Code
  CLI's own cwd discovery on the build/agentic path, and the user-level
  `~/.claude/CLAUDE.md` is bind-mounted into the sandbox. So a convention written in
  CLAUDE.md is invisible to any data-mode chat.
- **Working-state note** the compaction handoff (objective, decisions, done,
  not-done, open questions), one per scope, ~1200-token cap, written by the
  compaction path, read every turn. Agent-authored.
- **Knowledge notes + MEMORY.md** per-scope markdown notes with an always-loaded
  index. Written by agents (note blocks) and the operator (Notes UI). Both.
- **Learnings** short guardrails/preferences in `.runtime/learnings/*.jsonl`,
  injected every turn. Agent and system authored. Instance-local.
- **docs/ + docs/decisions (ADRs)** long-form plans and decisions, committed, NOT
  injected per turn, reached on demand via search/refs. ADRs carry a
  Status/Supersedes convention. Both authored.
- **README** scaffold-only, never injected.

## 4. The overlaps that actually cause the mess

1. **Decisions have three homes with no rule between them.** The PROJECT.md persona
   says "append a decision note to PROJECT.md" (unenforced, no supersession); the
   note protocol says durable choices go to `docs/decisions/` ADRs (versioned,
   status/supersede); and notes can be typed `decision`. Three places, inconsistent
   pointers. This is the least coherent seam.
2. **Rules live in two files, one of which is invisible.** AGENTS.md reaches the
   model; CLAUDE.md does not (backend never loads it). A repo convention placed in
   CLAUDE.md is silently unseen by data-mode chats.
3. **Manifest vs rules conflated in PROJECT.md.** It is both the goal/plan doc and
   the always-loaded orientation, so it is neither cleanly.
4. **Two "scope memory" substrates** (knowledge notes and the learnings store) that
   are easy to conflate; the distinction is enforced only by protocol prose.

Already reconciled recently and worth noting as precedent: the per-turn ticket
roll-up was removed as a duplicate of the digest (2026-08-25); the `shared/learnings.md`
mirror was deleted; a ticket-writing pointer was moved out of platform text into
CLAUDE.md. The direction is consistently "one home per fact."

## 5. Options (for the operator to decide, not yet built)

Framed to the standard, not to one workflow.

**A. Adopt AGENTS.md as the one always-loaded rules file.** It is the cross-vendor
standard and the persona already reads it. Treat the platform repo's own CLAUDE.md as
this repo's AGENTS.md (symlink or `@AGENTS.md` import, per Claude Code's own
recommendation) so there is one rules file, and make the backend load it consistently
so nothing is invisible. Human-authored, committed, optional, nearest-wins.

**B. Make the manifest a separate, optional artifact, and stop requiring it.** The
goal/plan/where-to-start doc is a convention, not a gate. Keep a PROJECT.md (or rename
to a manifest/spec name) as human-seeded, agent-updatable, and clearly the "what/why,"
distinct from AGENTS.md's "how." If we ever want structured planning, the spec-driven
pattern (spec -> plan -> tasks as separate reviewable files) is the researched
standard; not required now.

**C. One home for decisions.** Pick ADRs (`docs/decisions/`, versioned, status/
supersede) as the durable record and drop the unenforced "append to PROJECT.md"
instruction, OR keep a lightweight running decision log in the manifest and stop
pointing at three places. Either is fine; having three is the problem.

**D. Authorship + protection: separate by author, review by diff.** Keep the field's
model that is already half-built: human writes rules/manifest, agent writes memory
(working-state, notes, learnings) into separate stores. For the "between user-memory
and system-memory" file the operator described, the cleanest options are:
- Aider-style: the human-authored file is read-only to the agent (it consults, never
  rewrites); agent contributions go to the memory store instead.
- Or committed + git-diff review, which is the majority default.
A hard approval-gate/lock is the divergent minority and cuts against this system's
single-operator, no-defensive-cruft stance, so it is not recommended as the default.
"Protected file" as a NAMED, marked class is a real option only if we want a small
set of files (this manifest, AGENTS.md) that the agent may propose edits to but not
land without a diff the operator sees.

**E. Location: keep project context in the repo.** The always-loaded rules/manifest
belongs in the repo so it travels with the code and matches every other tool. The
agent-written memory tier stays where it is (committed for workshop/app scopes,
instance-local for project/agent). On import of an existing repo, generate a starting
AGENTS.md rather than requiring one.

## 6. Recommendation in one paragraph

Make AGENTS.md the single, human-authored, repo-committed, always-loaded rules file
(the standard), and make the backend load it everywhere so nothing is invisible. Demote
the manifest (PROJECT.md or a renamed spec doc) to an OPTIONAL "what/why + where to
start," never required, clearly distinct from rules. Give decisions one home (ADRs).
Keep human-rules vs agent-memory separated by file, protect the human files the
Aider way (read-only to the agent) or by plain git-diff review, and do not build a
permission-gate lock. This removes the three-artifact conflation without inventing a
new system, and every piece maps to a cross-vendor convention rather than to any one
person's habits.
