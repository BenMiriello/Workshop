// Shell-provided pieces an app's own module needs to build its routes.
//
// `AppGroupNav` and `AppChatDock` both need platform knowledge (the registry's
// group list; Workshop's chat system and its manifest endpoint) that an app page
// must not import directly (Stage 4b,
// docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md). They
// live here, apart from the registry, so an app module can use them without the
// registry having to know the app.
//
// This file imports `appGroups` from the registry, which imports every app
// module, which imports this file -- a cycle, and a deliberately harmless one:
// NOTHING HERE RUNS AT MODULE INIT. `AppGroupNav` reads the groups during
// render, by which time the registry has finished evaluating. The version of
// this code that lived in the registry computed `const APP_GROUPS = appGroups()`
// at the top level, and that is precisely the shape that cannot survive being
// moved out: apps/registry.test.tsx caught the equivalent breakage once already
// ("appCrumbs is not a function"), when appNav.tsx called it at ITS top level.

import { NavLink, useLocation } from "react-router-dom";
import type { AppChatDockRenderer } from "@workshop/ui";
import AppChatDock from "../components/AppChatDock";
import { appGroups } from "./registry";

type AppLink = { label: string; to: string };

/** Bind a ready-to-call chat dock renderer to one app's id. */
export function makeChatDock(appId: string): AppChatDockRenderer {
  return (opts) => (
    <AppChatDock
      appId={appId}
      title={opts?.title}
      placeholder={opts?.placeholder}
      onAction={opts?.onAction}
    />
  );
}

/** The sibling-app switcher for whichever group the current path belongs to. */
export function AppGroupNav() {
  const { pathname } = useLocation();
  // Read at RENDER, never at module init: see the cycle note above.
  const group: AppLink[] | null =
    appGroups().find((g) =>
      g.some((a) => pathname === a.to || pathname.startsWith(a.to + "/")),
    ) ?? null;
  if (!group) return null;
  return (
    <nav className="ws-nav ws-appgroup" aria-label="Related apps">
      {group.map((a) => {
        const active = pathname === a.to || pathname.startsWith(a.to + "/");
        return (
          <NavLink key={a.to} to={a.to} className={active ? "is-active" : undefined}>
            {a.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
