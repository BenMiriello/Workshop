<!-- author: self; chat: edadf121ed7a4469911cedaf29af9ea1 -->
**CORRECTED 2026-09-16. `scripts/shoot.sh` CAN screenshot a chat route. Do not
skip the screenshot check on a chat surface; shoot the surface you changed.**

What this note said, and why it was true on 2026-09-03: the driver navigated
with `waitUntil: "networkidle"`, and a chat route holds an open SSE stream for
as long as it is mounted, so the network never went idle and `page.goto` timed
out at 45s on `/chat/development` at both widths.

The fix the note itself proposed has since landed. `scripts/shoot.mjs:126`
navigates with `waitUntil: "domcontentloaded"` and then waits for
`networkidle` only as a BOUNDED settle (`:131`, `cfg.settle_ms || 2500`), so a
surface that never goes idle costs the settle budget rather than the whole
timeout. The comment at `:108-113` records the reasoning.

Measured again 2026-09-16: `bash scripts/shoot.sh /chat/development 1440 900`
and the same at `390 800` both returned a real shot of the live chat, including
its toolbar and composer. Roughly 40s on the first run of a session, because
Playwright downloads its Chromium headless shell (~100MB) on first use; a few
seconds after that.

The general point the original note made still stands and is worth keeping: any
streaming surface has this property, not just chat, which is why the fix belongs
in the driver rather than in a per-surface workaround.

**The lesson this note is now really about.** This file told three sessions that
a check could not be satisfied, and stayed authoritative after the thing it
described was fixed. That is the stale-blocker shape `CLAUDE.md` warns about: an
excuse outlives the constraint, and inaction starts to look like diligence.
Re-run the command before believing a note that says something is impossible.
