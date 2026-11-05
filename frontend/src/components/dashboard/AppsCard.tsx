import { Link } from "react-router-dom";
import { Button, cx } from "@workshop/ui";
import { useAppPlaces, type AppPlace } from "../../apps/useAppPlaces";
import { AppGlyph } from "../../lib/appIcon";
import { orderBy } from "./arrange";
import { DashboardCard } from "./DashboardCard";
import { useArrange } from "./useArrange";
import { useTileDrag } from "./useTileDrag";

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

// ARRANGING. "Arrange" turns the tiles into handles: drag one onto another to
// move it, and the order is kept on the server so it is the same everywhere.

function Tile({ app }: { app: AppPlace }) {
  return (
    <>
      <span className="ws-dashapps__glyph">
        <AppGlyph icon={app.icon} name={app.name} size={22} textSize="var(--fs-base)" />
      </span>
      <span className="ws-dashapps__name">{app.name}</span>
    </>
  );
}

export function AppsCard() {
  const arrange = useArrange();
  const saved = orderBy(useAppPlaces(), arrange.order, (a) => a.id);
  const { listRef, order, dragging, settling, props, style } = useTileDrag(
    saved.map((a) => a.id),
    undefined,
    (ids) => void arrange.reorder(ids),
  );
  const byId = new Map(saved.map((a) => [a.id, a]));
  const apps = order.flatMap((id) => byId.get(id) ?? []);

  const action = apps.length > 0 && (
    <Button size="sm" variant="ghost" onClick={arrange.arranging ? arrange.stop : arrange.start}>
      {arrange.arranging ? "Done" : "Arrange"}
    </Button>
  );

  return (
    <DashboardCard label="Apps" action={action} link={{ to: "/apps", label: "all →" }}>
      {apps.length === 0 ? (
        <p className="ws-dashcard__empty">No apps installed.</p>
      ) : (
        <ul ref={listRef} className={cx("ws-dashapps", arrange.arranging && "ws-dashapps--arranging")}>
          {apps.map((a, i) => (
            <li key={a.id}>
              {arrange.arranging ? (
                <button type="button" title={a.name} {...props(a.id, i)} style={style(a.id)}
                  className={cx("ws-dashapps__tile",
                    dragging === a.id && "is-dragging", dragging === a.id && settling && "is-settling")}>
                  <Tile app={a} />
                </button>
              ) : (
                <Link className="ws-dashapps__tile" to={a.home} title={a.name}>
                  <Tile app={a} />
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </DashboardCard>
  );
}
