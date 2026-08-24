// Column two: the page list for whichever place the dock is pointing at, and
// the directory tree that takes it over.
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
// THE TAKEOVER (navigation-shell-2026-08-20.md sections 3.4 and 8.1, built here
// for the first time). A page that declares itself a directory gets a
// right-pointing chevron on its row while that row is the active one. Tapping it
// slides the page list out to the left and the tree in from the right, with the
// page's own row risen to the top of the column as a back header. Tapping the
// header slides it back, and the CONTENT never changes: you are still looking at
// the document you opened. The chevron and the back control are the same toggle
// from opposite sides.
//
// The tree itself is not imported here. A directory page registers two strings
// and portals its own tree into `.ws-pagecol__dirbody`, so the shell knows
// nothing about scopes, notes or docs, and a second directory page later is a
// second caller rather than a branch in this file. See `directory.tsx`.
//
// NO REF PILL. The mockup puts a branch picker on this title line for every
// place. In this system a branch belongs to a project's repo, most places have
// no repo, and the design agent's own handoff records that its pickers "open
// nothing yet". Design doc section 8.2.
//
// NO STATUS AFFORDANCES EITHER (design doc section 8, and rule 7 of it): no
// counts, no activity dots, no health chrome on these rows. Nothing here is
// backed by data the operator asked to see.

import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useDirectory } from "./directory";
import type { Shell } from "./useShell";

export function PageColumn({ shell }: { shell: Shell }) {
  const { place, page, columnOpen, animating } = shell;
  const { registration, open, setOpen, setHost, setToolbarHost } = useDirectory();

  // A takeover only exists for the page you are ON. That is section 3.4's own
  // wording ("tapping a directory page while already on it"), and it is also
  // what keeps `memory` on Home from lighting up the row of the same id in
  // Settings or in an app's page list.
  const isDirectory = registration != null && page?.id === registration.pageId;
  const takeover = isDirectory && open;
  // Rows are reachable by keyboard only while they are the thing on screen.
  const rowsFocusable = columnOpen && !takeover;

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
        <div
          className={"ws-pagecol__pages" + (takeover ? " is-out" : "")}
          aria-hidden={takeover ? true : undefined}
        >
          <div className="ws-pagecol__title">{place.name}</div>
          <nav className="ws-pagecol__list" aria-label={`${place.name} pages`}>
            {place.pages.map((p) => {
              const active = page?.id === p.id;
              const Icon = p.Icon;
              const isDirRow = isDirectory && registration.pageId === p.id;
              const row = (
                <Link
                  key={p.id}
                  to={p.to}
                  className={"ws-pagerow" + (active ? " is-active" : "")}
                  aria-current={active ? "page" : undefined}
                  tabIndex={rowsFocusable ? undefined : -1}
                  // THE TRIGGER (mockup spec section 5): "tapping the page you
                  // are ALREADY on opens its tree". Only on the directory row,
                  // and only when the tree is hidden, so every other row is an
                  // ordinary link and this one still is when it is not the page
                  // you are on. The navigation is suppressed because there is
                  // none to make: it is the route you are already at.
                  onClick={isDirRow ? (e) => { e.preventDefault(); setOpen(true); } : undefined}
                >
                  <Icon size={14} aria-hidden />
                  <span className="ws-pagerow__label">{p.label}</span>
                </Link>
              );
              if (!isDirRow) return row;
              // The chevron sits OUTSIDE the link. A <button> inside an <a> is
              // invalid markup and is not operable, and this control does not
              // navigate: it opens the tree over the list you are reading.
              return (
                <div key={`${p.id}-dir`} className="ws-pagerow__wrap">
                  {row}
                  <button
                    type="button"
                    className="ws-pagerow__chev"
                    onClick={() => setOpen(true)}
                    tabIndex={rowsFocusable ? undefined : -1}
                    aria-label={`Open the ${registration.label} tree`}
                  >
                    <ChevronRight size={14} aria-hidden />
                  </button>
                </div>
              );
            })}
          </nav>
        </div>

        {/* STAYS MOUNTED, always. That is what lets it animate OUT as well as
            in, and it is also what keeps the page's tree (portalled into the
            body below) alive across a close, so coming back is instant and the
            branches you opened are still open. Section 3.4, correction 3. */}
        <div
          className={"ws-pagecol__dir" + (takeover ? " is-in" : "")}
          aria-hidden={takeover ? undefined : true}
        >
          {/* THE BACK HEADER, 46px and the whole row (mockup spec section 5).
              Not a page row: it is the panel's own header, so it spans the
              column and carries the hairline that separates it from the tree
              rather than sitting inset as a pill. No ref beside the title:
              design doc section 8.2 rejects the pill and there is no branch
              here to name. */}
          <button
            type="button"
            className="ws-pagecol__dirhead"
            onClick={() => setOpen(false)}
            tabIndex={takeover && columnOpen ? undefined : -1}
            aria-label={`Back to the ${place.name} pages`}
          >
            <ChevronLeft size={15} aria-hidden />
            <span className="ws-pagerow__label">{registration?.label ?? place.name}</span>
          </button>
          {registration?.hasToolbar && (
            <div className="ws-pagecol__dirtoolbar" ref={setToolbarHost} />
          )}
          <div className="ws-pagecol__dirbody" ref={setHost} />
        </div>
      </div>
    </div>
  );
}
