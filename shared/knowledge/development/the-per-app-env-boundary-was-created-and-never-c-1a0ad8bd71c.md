<!-- author: self; chat: 4181312fd46e4cdd913d7d3dd18f880a -->
Measured 2026-09-16, after the operator asked why one app's banking credentials were a concern for an unrelated change.

**The mechanism is correct and shipped.** `app_registry._load_app_env` (backend/services/app_registry.py:150) reads `apps/<slug>/app.env` for each installed app. It is ALREADY-SET-WINS on purpose, so systemd's `EnvironmentFile` and the operator's shell are never overwritten: an app ships a default, it does not impose a setting.

**The migration added the files and never removed the originals.** Four apps have an `app.env`: birds (5 vars), commute (1), health (1), location (1). Every single one of those variables is ALSO still in `~/.config/workshop/instance.env` or `secrets.env`. Already-set-wins therefore means the PLATFORM copy wins every time, and all four `app.env` files are dead weight. The boundary exists, is populated, and carries nothing.

**Three more apps never got one.** finance (`PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`, `WORKSHOP_PLAID_ENC_KEY`), wearables (`OPEN_WEARABLES_API_KEY`, `WORKSHOP_WEARABLES_URL`), commute (`WORKSHOP_511_API_KEY`, `WORKSHOP_SVC_COMMUTE`). Sixteen app variables total across six apps, all loaded into the backend's own process environment.

**Why the placement costs something real.** Four of the sixteen are live banking credentials. Because they sit at the platform layer, protecting them became a pattern-matching hook in the operator's SHELL (`~/.claude/hooks/egress-guard.py`, whose header cites `curl -d @plaid-tokens.json`) instead of an app boundary. A secret at the wrong layer forces every other layer to defend it, and makes unrelated work adjacent to it.

**The distinction that matters and is easy to state wrong.** App CODE is already correctly out of the platform: `apps/` is its own git repo and `git ls-files apps/` returns 0. This is configuration only. Saying "it lives in apps/finance" reads as "it is in our source" and is the wrong summary.

**The general rule.** Adding the new home does not complete a migration; removing the old one does. A boundary that the old path still shadows is worth less than no boundary, because it looks done. Check the precedence rule, not the file listing.

Ticketed with a gate that reads app declarations rather than carrying app names.