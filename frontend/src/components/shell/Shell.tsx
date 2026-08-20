// The container. Dock, page column, content on a desktop; bar one, page
// toolbar, content on a phone. One component, one set of state, and the
// breakpoint decides which chrome is drawn.
//
// Full design record and the reasoning behind every rule:
// docs/platform-redesign/navigation-shell-2026-08-20.md
//
// The two width modifiers from the old shell are unchanged and still the ONLY
// per-route width logic there is. Do not reintroduce a route-by-route list.

import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { OverlayHost } from "@workshop/ui";
import { Dock } from "./Dock";
import { MobileBars } from "./MobileBars";
import { PageColumn } from "./PageColumn";
import { ShellTrail } from "./ShellTrail";
import { useShell } from "./useShell";

export function Shell({ children }: { children: ReactNode }) {
  const shell = useShell();
  const { pathname } = useLocation();

  const chatFull = pathname.startsWith("/chat/");
  const appFull =
    !chatFull && (/^\/apps\/[^/]/.test(pathname) || /^\/projects\/[^/]+\/run/.test(pathname));
  const widthClass = chatFull ? " ws-app--chatfull" : appFull ? " ws-app--appfull" : "";

  return (
    <div className={"ws-root ws-app ws-shell" + widthClass}>
      <Dock shell={shell} />
      <div className="ws-shell__stage">
        <MobileBars shell={shell} />
        <div className="ws-shell__cols">
          <PageColumn shell={shell} />
          {/* The content region, and the only place an overlay may land.
              Dialogs portal into the host this renders, so they cover the page
              and not the dock or the toolbars beside it. */}
          <OverlayHost>
            <ShellTrail />
            <main>{children}</main>
          </OverlayHost>
        </div>
      </div>
    </div>
  );
}
