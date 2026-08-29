<!-- author: derived:claude-sonnet-5 -->
An operator's work GitHub org is a hard boundary: this installation must never reference, depend on, host anything in, or push to it. The boundary is enforced from instance config, not from core code — `WORKSHOP_GITHUB_OWNER` names the one account this install may work under and `WORKSHOP_BLOCKED_REPO_OWNERS` names the accounts it must refuse (`services/github.py:reject_blocked`, surfaced to agents by `chat_context.git_worktree_rule`). The platform itself knows no org names, and no org name belongs in tracked source, docs or knowledge notes: naming one here would publish the very boundary it is describing.

Migrated 2026-08-14 from chat-distilled; originating chat not recorded. Renamed and de-identified 2026-08-27.
