// Column two: the page list for whichever place the dock is pointing at.
//
// It collapses by sliding ITSELF left, out under the dock, while content
// slides in behind it. It does not simply get covered. The title row travels
// with the list, so the column moves as one piece.
//
// The transition is applied only when `shell.animating` is set, which happens
// exactly when you toggle the column of the place you are already in. Switching
// place hard cuts. See useShell for why that distinction lives in state rather
// than in CSS.
//
// NO REF PILL. The mockup puts a branch picker on this title line for every
// place. In this system a branch belongs to a project's repo, most places have
// no repo, and the design agent's own handoff records that its pickers "open
// nothing yet". Design doc section 8.2.

import { Link } from "react-router-dom";
import type { Shell } from "./useShell";

export function PageColumn({ shell }: { shell: Shell }) {
  const { place, page, columnOpen, animating } = shell;
  return (
    <div
      className={
        "ws-pagecol" +
        (columnOpen ? " is-open" : "") +
        (animating ? " is-animating" : "")
      }
      // The column stays MOUNTED when closed so it has something to animate out
      // of, which means it also has to be taken out of the tab order and off
      // the accessibility tree by hand. (`inert` would do both in one attribute
      // and React 18 does not type it.)
      aria-hidden={columnOpen ? undefined : true}
    >
      <div className="ws-pagecol__inner">
        <div className="ws-pagecol__title">{place.name}</div>
        <nav className="ws-pagecol__list" aria-label={`${place.name} pages`}>
          {place.pages.map((p) => {
            const active = page?.id === p.id;
            const Icon = p.Icon;
            return (
              <Link
                key={p.id}
                to={p.to}
                className={"ws-pagerow" + (active ? " is-active" : "")}
                aria-current={active ? "page" : undefined}
                tabIndex={columnOpen ? undefined : -1}
              >
                <Icon size={14} aria-hidden />
                <span className="ws-pagerow__label">{p.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
