# Agent rules — {{title}}

- Read `PROJECT.md` first every turn.
- Update `TODO.md` as work progresses.
- Edit only within this directory unless the operator explicitly asks for
  workshop-wide wiring (then stop and surface what files outside need to change).
- Workshop design system is mandatory. Build from the `@workshop/ui` kit.
  Lucide icons only; no emoji. Five status kinds. ConfirmDialog for destructive.
- Before claiming a UI task done: run `scripts/check_design.sh` and `npm run typecheck`
  from the workshop root. Never `npx tsc` — npx picks a deprecated stub package of that
  name over the real binary and exits 0 having checked nothing.
- No stubs (TODO/FIXME/NotImplementedError/pass # stub/...). Mark `[~] BLOCKED` if
  a task is too big to finish in one tick.
- A Workshop chat can sit beside this app's screen. With nothing added it can
  read and use the page and take selected text; to give it context, your own
  page tools or a Discuss action, follow the Workshop's docs/chat-beside-a-page.md
  (`workshop/v1/page`, `pageCall`, `attach` messages to the parent window).
