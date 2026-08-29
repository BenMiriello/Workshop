import { Link } from "react-router-dom";
import { useAppPlaces } from "../../apps/useAppPlaces";
import { AppGlyph } from "../../lib/appIcon";
import { DashboardCard } from "./DashboardCard";

// THE APPS, and they are the DOCK'S apps.
//
// `useAppPlaces()` is the same hook `useShell` builds dock tiles from, so this
// card and the dock cannot disagree about which apps exist. That rule is
// unchanged; what changed is where the list comes from. It used to walk the
// frontend registry, which lists only the apps that ship UI in this bundle, so
// an app the platform knew about any other way had no tile and no card while
// its chats, memory and tickets were all being served.
//
// It falls back to the bundled registry while the read is in flight, so there
// is still no loading state and no error state to show: a dock that blinks out
// on a slow request is worse than one that is briefly short.

// EVERY INSTALLED APP, in one scrolling grid. It used to page eight at a time
// behind a row of dots, which was the short card's constraint dressed up as a
// feature: paging a list of a dozen tiles hides half of them behind a control
// nobody asked for. The card scrolls like the other three now.

export function AppsCard() {
  const apps = useAppPlaces();

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
