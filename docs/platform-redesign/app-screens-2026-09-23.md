# An app's own screens live inside the app (2026-09-23)

Status: proposed. Builds on `app-is-the-entity-2026-08-20.md`.

## The problem

An app that runs its own server (a `workshop.app.json` with `services`) has
nowhere to be seen inside its own page.

- `/apps/<slug>` offers the platform's tabs only: Work, Overview, Chats,
  Memory, Files. An `app` row exists, but only for apps whose screens ship in
  this bundle (`ownScreen`).
- The running UI is reachable from Overview -> Services -> "open in new tab",
  or from `/projects/<slug>/run`, which nothing links to and which shows only
  the first exposed service.
- `/projects/<slug>/run` is outside `/apps/<slug>`, and the shell decides which
  app you are in by prefix (`placeFor`), so a screen opened there is drawn in
  the Workshop home place: Home highlighted in the rail, the Workshop sidebar
  showing, the app's own tile not selected.
- A process-mode app that should be running does not come back after a
  backend restart. `app_runner` persists `desired: running` and its comment
  says the monitor restarts it; the monitor was removed (8c41ad56).

## The principle

An app is like an app on an operating system. Opening it takes you into it,
it has its own sidebar, and what it serves is the first thing you see.

## The design

1. **Screens.** Every service a manifest marks `expose: true` is a screen of
   the app. An optional `label` names it; the service name is the fallback.
   Any number of screens is allowed: a site and its admin console are two.
   Services that are not exposed (an API) are not screens; they stay in
   Overview.
2. **Where a screen lives.** `/apps/<slug>/ui/<service>/<path...>`. It frames
   the existing proxy on the page's own origin,
   `/api/base-apps/run/<slug>/proxy/<service>/<path...>` (the edge relays
   `/api`), so a deep link into the app is a real, bookmarkable URL inside the
   app's page and no port is guessed.
3. **The sidebar.** An app's pages are its screens first, in manifest order,
   then the platform tabs it can have. This is the existing `app` row
   generalized from one bundled screen to N served ones.
4. **Where an app opens.** On its first screen, whenever it has one, including
   while it is being built. With no screen it opens on its first tab, as
   today. `surface.home` in the manifest can still name either.
5. **Running is the normal state.**
   - Opening a screen of a stopped app starts it (the same call as Overview's
     Start, which sets `desired: running`) and shows its progress in place of
     the frame until the service answers.
   - At backend startup, every app whose persisted state is
     `desired: running` and whose services are not alive is started again.
     Containers are already brought back by docker's restart policy; this
     covers the process path.
6. **Full screen.** A screen has a control that puts the frame in the
   browser's full screen, and "open in new tab" to the bare proxy URL.
7. **Old routes.** `/projects/<slug>/run` redirects to the app, which opens on
   its first screen.

## What changes

Backend:
- `app_surface.surface_for`: add `screens: [{service, label}]` from the
  manifest, and resolve `home` to the first screen when one exists.
- `app_runner`: the startup restore in 5; `label` on `ServiceSpec`.

Frontend:
- `places.ts`: one page per screen, ahead of the tabs.
- A route `/apps/:slug/ui/:service/*` rendering the framed screen (the
  current `AppPreview`, given the service and subpath instead of picking one),
  with start-on-open and the full-screen control.
- `/projects/:slug/run` becomes a redirect.

Nothing names an app.

## Not in scope

- Crash restart for a process-mode app while the backend is up. Docker covers
  containers; whether process mode needs a supervisor again is a separate
  decision.
- WebSockets through the proxy. A screen that needs one (a dev server's hot
  reload, a realtime app) still cannot use it when framed.
