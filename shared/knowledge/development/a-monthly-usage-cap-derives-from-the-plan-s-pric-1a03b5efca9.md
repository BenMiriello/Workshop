<!-- author: self; chat: 2c44eaf71ef14a3f83c96d119787ba9f -->
Settled with the operator 2026-08-25, after two wrong attempts worth recording so they are not repeated.

**Do not derive a product default from this installation's telemetry.** The operator is the developer, not the user: "you can't base it off of my usage. I told you how to get it... This is not an application just for me." A default must be derived from the PLAN, as a product-wide default.

**Anthropic publishes no token or hours figure for any plan.** Only the price, a 5-hour session window, a weekly cap, and a relative multiplier ("5x the usage of Pro per 5-hour session"). The 2025-era "140-280h Sonnet/week" figures are no longer published; treat as stale. The ONE published conversion is that usage past a plan's allowance bills at standard API rates (support.claude.com article 12429409), which is what makes API-rate dollars the only denominator a subscription plan and a token ledger share.

**The default is 25% of the plan's monthly price:** Pro $20 gives $5/mo, Max 5x $100 gives $25/mo, Max 20x $200 gives $50/mo. The operator is on Max 5x. Configurable; the fraction's whole point is that the remaining ~75% stays free for the operator's interactive use.

**The cap governs AUTONOMOUS WORK ONLY.** Chat is not capped, not warned, and does not draw down the budget. The operator, correcting a design that merged them: "I don't want the chat warned if we're over like the 25% usage, and I don't want the chat and the autonomous agents being on the same cap. That was never part of the design. I never asked for that, never implied that." The tree already supports this: chat spend carries `role="chat"` (`build_streaming.py:3383`) and `can_admit` (`usage_ledger.py:674`) is only called on the autonomous path, so the monthly rule reads the ledger filtered to non-chat roles.

**A SEPARATE mechanism, explicitly distinguished by the operator and not to be merged into the cap:** if the plan as a whole has no headroom left, do not START autonomous work. That is an availability check on the plan, not a budget.

**Plan detection already half exists:** `backend/services/auth_health.py:172` reads `subscriptionType` from `claude auth status --json` and returns it as `plan`. It distinguishes pro from max but NOT 5x from 20x, and nothing consumes it today. So the tier is free and the multiplier becomes a setting.

Doc: `docs/platform-redesign/limiting-usage-2026-08-24.md`, final section.