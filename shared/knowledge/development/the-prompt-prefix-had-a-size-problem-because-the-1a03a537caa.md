<!-- author: self; chat: 2c44eaf71ef14a3f83c96d119787ba9f -->
Measured 2026-08-25 on a fresh `development` chat: assembled prefix 114,192 chars (~28.5K tokens), of which `live_context()` is 77,265 (68%) and PROJECT.md alone is 75,891, its append-only `## Log` being 72,425 of that (95%, 30 entries).

Every OTHER block that reads a growing store already has an explicit cap with a written rationale: `_TODO_LIST_CAP=8`, `WORKING_STATE_MAX_CHARS=4800`, learnings `_BLOCK_TOKEN_BUDGET=900` per scope, `DEFAULT_LAYERED_INDEX_TOKEN_BUDGET=500`, `workspace_digest.CAP_TASKS=6`. Five authors each capped their own block unprompted.

So the defect is not sloppiness and not a wrong authority model. `docs/platform-redesign/agent-context-and-toolkit-2026-08-20.md` specifies authority, authorship, editability and assembly order across L0-L3 and is completely silent on cost: no layer owns a byte budget, and nothing sums the total. Every part was policed and the sum was owned by nobody.

Aggravator: `chat_context.py:1953` instructs every agent to append a decision line to PROJECT.md, which is the file `live_context()` inlines whole. `PROMPT_CEILING_TOKENS=600_000` is a fuse on conversation history only and can never fire on a 28.5K prefix.

Also measured and non-obvious: PROJECT.md is NOT in the semantic index. `collect_docs()` walks only `workshop_root()/docs` and PROJECT.md is at the repo root, so 0 of 14,413 chunks match it. Moving the Log out of the prefix therefore requires indexing project-root markdown FIRST, or 72KB of decision history becomes unfindable by question.

Scale check before blaming this: the operator measured >30% of a 1M window. Our prefix is 28.5K. The remainder is the Claude Code CLI's own system prompt, its built-in tool schemas, and CLAUDE.md files (53KB). The workshop MCP server is 6 tools / 5,917 chars and is not the cause.

Design: `docs/platform-redesign/context-assembly-budget-2026-08-25.md`.