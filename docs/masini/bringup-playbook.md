# Clawdbot bring-up playbook (draft, to be promoted to `/opt/clawd-house/docs/`)

> Drafted 2026-05-17 as part of clawd-a Phase 1 completion. Repeat this verbatim for clawd-b (or any future tenant) substituting the tenant name and tokens. Not yet committed to the canonical docs repo — commit as `benmiriello` after review.

## What you're building

A persistent Claude Code session per tenant, running under user-systemd, with one or more Channels MCP plugins (Slack first; Telegram, Discord, iMessage later) feeding inbound messages directly into the session. Outbound replies go back through the plugin's `reply` tool. All inbound traffic bills against the subscription rate limit, not the per-token API budget (ADR-006).

## Prerequisites

- Tenant Linux user exists with a `~/` mode 700 (eg. `useradd -m -s /bin/bash clawd-b && chmod 700 /home/clawd-b`)
- `loginctl enable-linger <tenant>` so user-systemd survives logout/reboot (`Linger=yes` in `loginctl show-user <tenant>`)
- nvm + node installed under the tenant (`~/.nvm/versions/node/<v>/bin/node`)
- Claude Code installed via npm under that node: `npm i -g @anthropic-ai/claude-code`
- Tenant has logged into claude.ai once (`claude` → OAuth flow → writes `~/.claude/.credentials.json`)
- `tmux` is on the system PATH (`apt install tmux` if not)

## One-time host-level setup (root)

Channel notifications are gated by `channelsEnabled` in managed settings. **Without this, every inbound message is silently dropped by Claude.** Set it once per host, listing every channel plugin every tenant is allowed to use:

```bash
sudo mkdir -p /etc/claude-code
sudo tee /etc/claude-code/managed-settings.json <<'JSON'
{
  "channelsEnabled": true,
  "allowedChannelPlugins": [
    { "marketplace": "clawd-local", "plugin": "slack-channel" }
    // add other plugins as tenants enable them
  ]
}
JSON
sudo chmod 644 /etc/claude-code/managed-settings.json
```

If you add a new plugin later (eg. `telegram@claude-plugins-official` for clawd-b), append it to `allowedChannelPlugins` and restart any running Claude sessions on the host.

## Per-tenant setup (as the tenant user)

### 1. Install the slack-channel plugin (with **user** scope)

Plugin install defaults to **project** scope, which means the plugin only loads when claude runs from the project dir where it was installed (`projectPath` in `~/.claude/plugins/installed_plugins.json`). The persistent bridge runs from a different cwd (`~/clawdbot`) than wherever you originally installed the plugin (typically `~`), so a project-scoped install won't work. **Install at user scope OR fix it after the fact** by editing `~/.claude/plugins/installed_plugins.json`:

```json
{
  "version": 2,
  "plugins": {
    "slack-channel@clawd-local": [{
      "scope": "user",                              // ← change from "project"
      "installPath": ".../slack-channel/0.1.0",
      "version": "0.1.0",
      "installedAt": "...",
      "lastUpdated": "...",
      "gitCommitSha": "..."
      // remove the projectPath field entirely
    }]
  }
}
```

Without this, the bridge claude shows "Listening for channel messages from: plugin:slack-channel@clawd-local" in its TUI but **never actually spawns the plugin's MCP server**, and Slack DMs vanish into the void. Diagnostic: `pgrep -af "slack-channel.*server.ts"` should return exactly one wrapper+node pair, both as children of the bridge claude process.

Local marketplace pointer + plugin enable goes in `~/.claude/settings.json`. Bootstrap with:

```bash
cat > ~/.claude/settings.json <<'JSON'
{
  "permissions": { "defaultMode": "auto" },
  "enabledPlugins": { "slack-channel@clawd-local": true },
  "extraKnownMarketplaces": {
    "clawd-local": {
      "source": { "source": "directory", "path": "/home/<tenant>/dev/clawd-marketplace" }
    }
  },
  "theme": "dark",
  "skipAutoPermissionPrompt": true
}
JSON
```

(Adjust the marketplace path to wherever your `clawd-marketplace` git checkout lives. For clawd-a it's `/home/clawd-a/dev/clawd-marketplace`.)

### 2. Configure Slack tokens

Create a Slack app per the plugin's `skills/configure/SKILL.md`. You need a bot token (`xoxb-...`) with scopes `chat:write,chat:write.public,im:write,im:history,im:read,channels:history,groups:history,app_mentions:read` and an app-level token (`xapp-...`) with `connections:write`. Then in a Claude session:

```
/slack-channel:configure <bot-token> <app-token>
```

This writes `~/.claude/channels/slack/.env` with mode 0600.

### 3. Allowlist your own Slack user

`defaultAccess()` in the plugin hardens to `dmPolicy: 'allowlist'` and silently drops every DM unless the sender is on the allowlist. **Without this, every inbound message is silently dropped by the plugin.** Get your Slack user ID (`U...` from your Slack profile or `auth.test`), then in a Claude session:

```
/slack-channel:access add U01234567
```

This creates `~/.claude/channels/slack/access.json` with you in `allowFrom`.

### 4. Create the persistent session's working directory

```bash
mkdir -p ~/clawdbot
cat > ~/clawdbot/CLAUDE.md <<'MD'
# clawdbot (<tenant> tenant)
[persona + operating context — see /home/clawd-a/clawdbot/CLAUDE.md for the reference template]
MD
```

The dedicated directory matters because `claude --continue` resumes the most-recent session within the current cwd. If the bridge runs from `~/clawdbot`, dev sessions started from other cwds won't interfere with the bridge's conversation history.

### 5. Install the systemd unit

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/clawd-slack.service <<'UNIT'
[Unit]
Description=<tenant> persistent Claude session with Slack channel bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment="PATH=/home/<tenant>/.nvm/versions/node/<v>/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="ANTHROPIC_API_KEY="
WorkingDirectory=/home/<tenant>/clawdbot
ExecStart=/usr/bin/tmux new-session -d -s clawd-claude '/bin/bash -c "while true; do /home/<tenant>/.nvm/versions/node/<v>/bin/claude --continue --channels plugin:slack-channel@clawd-local; sleep 5; done"'
ExecStop=-/usr/bin/tmux kill-session -t clawd-claude
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable clawd-slack.service
```

Don't `start` it yet if you have an interactive Claude session already running in this terminal — they'd both connect to the Slack Socket Mode and load-balance events between them. Either: (a) exit the interactive session first, or (b) wait until next reboot to let the systemd unit take over.

### 6. Verify

End-to-end test (the four Phase-1 criteria from ADR-006):

1. DM the bot from your Slack user. A `<channel source="plugin:slack-channel:slack" ...>` tag should appear in the persistent Claude session within a couple seconds.
2. The session replies via `mcp__plugin_slack-channel_slack__reply`; the reply appears in Slack.
3. Anthropic Console shows zero new API spend during the test window (subscription-only auth).
4. Reboot the box. With no manual intervention, step 1 still works.

If any of those fail, the diagnostic order is **always**:
1. `cat ~/.claude/channels/slack/access.json` — sender in `allowFrom`?
2. `cat /etc/claude-code/managed-settings.json` — `channelsEnabled: true` + plugin in `allowedChannelPlugins`?
3. `cat ~/.claude/plugins/installed_plugins.json` — plugin `scope: "user"` (NOT `"project"` bound to a different path than the bridge's cwd)?
4. `systemctl --user status clawd-slack.service` — bridge active?
5. `pgrep -af "slack-channel.*server.ts"` — bridge claude's child slack-channel MCP server alive? (verify with `pstree -ap <bridge-claude-pid>`)
6. Only after all five check out, look at Slack-side state, WSS routing, tokens, etc.

Gates 1–3 account for nearly every "everything looks right but it doesn't work" failure. Each fails silently in a different way: gate 1 drops the gate result; gate 2 drops the MCP notification; gate 3 skips spawning the MCP server entirely.

## Generalizing further

The `~/clawdbot/CLAUDE.md` per-tenant content can be templated. The architecture is:

- `/etc/claude-code/managed-settings.json` — host-level allowlist
- `/opt/clawd-house/docs/` — shared spec + ADRs (owned by `clawd-house`)
- `/home/<tenant>/` — per-tenant state, secrets, plugins, persona, memory
- `/home/<tenant>/clawdbot/` — the persistent session's working dir
- `/home/<tenant>/.config/systemd/user/clawd-slack.service` — boot-time bringup

When clawd-b is provisioned, repeat steps 1–6 substituting tenant name. The host-level `managed-settings.json` may already have channelsEnabled set; just append clawd-b's plugins to `allowedChannelPlugins` and reload.
