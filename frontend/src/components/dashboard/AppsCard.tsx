import { useMemo } from "react";
import { Link } from "react-router-dom";
import { appPlaces } from "../../apps/registry";
import { AppGlyph } from "../../lib/appIcon";
import { DashboardCard } from "./DashboardCard";

// THE APPS, and they are the DOCK'S apps.
//
// `appPlaces()` is the same function `useShell` builds dock tiles from, so this
// card and the dock cannot disagree about which apps exist. Not `/api/apps`,
// not a second catalog: the shell must not become a second place that lists
// apps, which is the rule `appPlaces` was extracted to enforce.
//
// It is a synchronous read of the installed registry, so this card has no
// loading state and no error state -- there is no request to fail. It is the
// only one of the four like that, and that is a property of the data, not an
// omission.

// EVERY INSTALLED APP, in one scrolling grid. It used to page eight at a time
// behind a row of dots, which was the short card's constraint dressed up as a
// feature: paging a list of a dozen tiles hides half of them behind a control
// nobody asked for. The card scrolls like the other three now.

export function AppsCard() {
  const apps = useMemo(() => appPlaces(), []);

  return (
    <DashboardCard label="Apps" link={{ to: "/apps", label: "all →" }}>
      {apps.length === 0 ? (
        <p className="ws-dashcard__empty">No apps installed.</p>
      ) : (
        <ul className="ws-dashapps">
          {apps.map((a) => (
            <li key={a.id}>
              <Link className="ws-dashapps__tile" to={a.home} title={a.name}>
                <span className="ws-dashapps__glyph">
                  <AppGlyph icon={a.icon} name={a.name} size={22} textSize="var(--fs-base)" />
                </span>
                <span className="ws-dashapps__name">{a.name}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </DashboardCard>
  );
}
