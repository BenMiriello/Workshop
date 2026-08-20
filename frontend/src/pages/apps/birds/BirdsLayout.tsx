// The Birds app's own route layout: its persistent Location-Device-Time scope
// wrapped around its responsive shell.
//
// THIS LIVES IN THE APP, not in the platform's app registry, and it is lazy.
// It used to be a component defined in `frontend/src/apps/registry.tsx`, which
// statically imported `./scope` and `./BirdsShell` to build it. `registry.tsx`
// is imported by App.tsx, appNav, useShell, Apps and Settings, so it is
// unconditionally in the entry graph -- which dragged BirdsShell, BirdsHeader
// and `birds.css` in with it. The measured cost was 24 `.birds-*` rules sitting
// in the render-blocking entry stylesheet that every visitor downloads,
// including people with the app uninstalled.
//
// The registry's own comment claimed lazy imports kept an uninstalled app off
// the critical path. That was true of the pages and false of this layout.
//
// See docs/platform-redesign/app-separation-and-github-2026-08-20.md section 1.3.

import { Outlet } from "react-router-dom";
import BirdsShell from "./BirdsShell";
import { BirdsScopeProvider } from "./scope";

export default function BirdsLayout() {
  return (
    <BirdsScopeProvider>
      <BirdsShell>
        <Outlet />
      </BirdsShell>
    </BirdsScopeProvider>
  );
}
