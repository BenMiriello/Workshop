// The phone shell: bar one, then the page toolbar, then content.
//
// Bar one is `place tile · place name + caret · avatar`. The tile opens the
// place menu (PlaceMenu.tsx owns that dropdown, its scrim and its focus
// behaviour); the name and its animated caret toggle the page toolbar. There is
// no separate Home button (Home is a row in the menu) and no hamburger (the
// caret took its job). Search will sit between the caret and the avatar when it
// has a backend; it is not drawn as a dead button until then.
//
// Two rules here were reversed during design and are easy to re-break:
//
//   1. NOTHING IS RAISED on selection in the page toolbar. Selected is weight
//      plus a 2px underline on the bar's own bottom edge, and every label stays
//      on one baseline.
//   2. THE PAGE TOOLBAR NEVER DEFAULTS TO CLOSED, on any page, including an
//      app's own. A manual toggle holds until you change place or page.
//
// `title + caret` conventionally means "open a menu", not "expand the bar
// below". That is the one learnable element in this design and it was adopted
// knowingly, with the caret carrying the state.
//
// AN OPEN DOCUMENT COLLAPSES THE TOOLBAR TO ZERO (spec section 6: the editor is
// a full content takeover, and section 1.1: "It force-collapses to 0 while a
// document is open"). It collapses rather than unmounts, so the bar animates
// shut on the way in and back open on the way out, which is the same 40px-to-0
// transition the caret already drives. `documentOpen` is DERIVED
// (shell/takeover.tsx), so it never overwrites the remembered open/closed
// preference: closing the document restores whatever it was.

import { Link } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import { monogram } from "../../lib/appIcon";
import { useAuth } from "../../auth/AuthProvider";
import { PlaceMenu } from "./PlaceMenu";
import type { Shell } from "./useShell";

/** The signed-in person's initials. Same two-letter rule as an app with no
 *  icon, so it is the same function; this file and Dock.tsx each had their own
 *  copy of it, which is how three spellings of one rule appear. */
const initials = (name: string | null | undefined, email: string) => monogram(name || email || "");

export function MobileBars({ shell }: { shell: Shell }) {
  const { place, page, tabsOpen, fullScreen, hasPageList, documentOpen } = shell;
  // No pages to switch between (an app with one screen), one conversation
  // taking the whole screen, or an open document taking it: the title is a
  // label, not a control. A caret is the toolbar's state (spec section 1.1), so
  // while the toolbar is force-collapsed there is no state for it to carry and
  // no toggle it could perform.
  const togglable = hasPageList && !fullScreen && !documentOpen;
  const { user } = useAuth();

  return (
    <div className="ws-bar1__wrap">
      <div className="ws-bar1">
        <PlaceMenu shell={shell} />

        {!togglable ? (
          <span className="ws-bar1__title">{place.name}</span>
        ) : (
          <button
            type="button"
            className="ws-bar1__titlebtn"
            aria-expanded={tabsOpen}
            aria-label={tabsOpen ? "Hide pages" : "Show pages"}
            onClick={shell.toggleTabs}
          >
            <span className="ws-bar1__title">{place.name}</span>
            <ChevronDown
              size={13}
              aria-hidden
              className={"ws-bar1__caret" + (tabsOpen ? " is-open" : "")}
            />
          </button>
        )}

        <Link
          to="/settings"
          className="ws-bar1__avatar"
          aria-label="Account and settings"
        >
          {initials(user?.name, user?.email ?? "")}
        </Link>
      </div>

      {hasPageList && (
      <div className={"ws-tabbar__slot" + (tabsOpen ? " is-open" : "")}>
        <nav className="ws-tabbar" aria-label={`${place.name} pages`}>
          {place.pages.map((p) => {
            const active = page?.id === p.id;
            return (
              <Link
                key={p.id}
                to={p.to}
                className={"ws-tabbar__tab" + (active ? " is-active" : "")}
                aria-current={active ? "page" : undefined}
                tabIndex={tabsOpen ? undefined : -1}
              >
                {p.label}
              </Link>
            );
          })}
        </nav>
      </div>
      )}
    </div>
  );
}
