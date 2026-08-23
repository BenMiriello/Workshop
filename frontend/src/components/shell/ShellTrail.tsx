// A trail, only where one is needed.
//
// The mockup has no top toolbar, because the dock and the page column between
// them answer "where am I" for every screen it drew. It drew no RECORD pages,
// and this app is full of them: an agent, a project, a ticket, a run, a bird at
// a location. Deleting the breadcrumb outright would strand those, because only
// five of the twenty-six page files carry their own `back` slot today.
//
// So the trail survives, moved and cut down:
//
//   * it renders inside the content column, not in a bar across the top;
//   * it renders ONLY when the trail goes deeper than the page column already
//     says, which is never on any screen the mockup drew;
//   * the "Workshop" brand link is gone. The dock's Home tile is that, and the
//     duplication between the breadcrumb and the section links is one of the
//     complaints this redesign started from.
//
// When every detail page carries a `back` slot (the ratified pattern, see
// frontend/CLAUDE.md "A record page is not a list page") this can go.

import { Link } from "react-router-dom";
import { useCrumbs } from "../appNav";
import { useTakeover } from "./takeover";

export function ShellTrail() {
  const crumbs = useCrumbs();
  // An open document is a FULL content takeover (shell-mockup-spec-2026-08-22.md
  // section 6): the shell collapses its page toolbar and the page drops its own
  // header, so a trail above the file would be the last piece of chrome left
  // standing between the top of the screen and the thing you opened.
  const takeover = useTakeover();
  if (takeover) return null;
  if (crumbs.length < 2) return null;
  const last = crumbs.length - 1;
  return (
    <nav className="ws-trail" aria-label="Breadcrumb">
      {crumbs.map((c, i) => (
        <span className="ws-trail__item" key={`${c.label}-${i}`}>
          {i > 0 && (
            <span className="ws-trail__sep" aria-hidden>
              /
            </span>
          )}
          {i === last || !c.to ? (
            <span className="ws-trail__cur" aria-current={i === last ? "page" : undefined}>
              {c.label}
            </span>
          ) : (
            <Link to={c.to} className="ws-trail__link">
              {c.label}
            </Link>
          )}
        </span>
      ))}
    </nav>
  );
}
