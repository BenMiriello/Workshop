# {{title}}

> {{purpose}}

## Goal

An integrated Workshop sub-app. Will mount under `/apps/{{slug}}` in the
main Workshop frontend and (optionally) declare a manifest in `apps.json`
with `provides` / `needs` capability declarations per the app-platform
plan.

## Constraints

- Build from `frontend/src/ui` primitives only. No inline hex; no emoji.
- Five status kinds. Edit-mode for writes. ConfirmDialog for destructive.
- All four list states (loading/empty/error/overflow).
- Reviewer gate (`scripts/reviewer.sh`) must pass before any TODO `[x]`.

## Open questions

- Does this need a backend route under `backend/routes/`?
- Capabilities: what does it `provide` / `need` in the platform sense?
- Privacy posture: status-on-glance only; specific values one tap deeper.

## Wiring checklist

- [ ] Add to `frontend/src/App.tsx` routes
- [ ] Add card to `frontend/src/pages/Apps.tsx`
- [ ] Add breadcrumb entry in `frontend/src/components/appNav.tsx`
- [ ] (If backend) register router in `backend/main.py`
- [ ] (If capability provider) declare in `apps.json` when that exists
