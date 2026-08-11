# {{title}} — house rules

This sub-app mounts inside the Workshop. Design system is mandatory.

In short:
- Build from the `@workshop/ui` kit (`packages/ui`): import primitives from `@workshop/ui`, tokens from `@workshop/ui/tokens.css`.
- No inline hex colors. Lucide icons only. No emoji.
- Five status kinds. Edit-mode for config/money writes. ConfirmDialog
  for destructive actions. All four list states.
- Dark theme is default.

Before claiming a UI task done: `bash scripts/check_design.sh` and
`cd frontend && npx tsc --noEmit` from the workshop root.
