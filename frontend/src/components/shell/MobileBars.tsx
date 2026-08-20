// The phone shell: bar one, then the page toolbar, then content.
//
// Bar one is `place tile · place name + caret · avatar`. The tile opens the
// place menu; the name and its animated caret toggle the page toolbar. There is
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

import { Link } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import { Menu, MenuItem } from "@workshop/ui";
import { useAuth } from "../../auth/AuthProvider";
import type { Shell } from "./useShell";

function initials(name: string | null | undefined, email: string): string {
  const source = (name || email || "").trim();
  if (!source) return "?";
  const words = source.split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : source.slice(0, 2);
  return letters.toUpperCase();
}

export function MobileBars({ shell }: { shell: Shell }) {
  const { place, page, tabsOpen, fullScreen } = shell;
  const { user } = useAuth();

  return (
    <div className="ws-bar1__wrap">
      <div className="ws-bar1">
        <Menu
          className="ws-bar1__places"
          label="Places"
          trigger={
            <button type="button" className="ws-bar1__tile" aria-label={`${place.name}. Switch place`}>
              {place.Icon ? <place.Icon size={17} aria-hidden /> : <span aria-hidden>{place.letter}</span>}
            </button>
          }
        >
          {shell.places.map((p) => (
            <MenuItem
              key={p.id}
              icon={p.Icon ? <p.Icon size={16} /> : <span className="ws-bar1__menuletter">{p.letter}</span>}
              active={p.id === place.id}
              onSelect={() => shell.selectPlace(p)}
            >
              {p.name}
            </MenuItem>
          ))}
        </Menu>

        {fullScreen ? (
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
    </div>
  );
}
