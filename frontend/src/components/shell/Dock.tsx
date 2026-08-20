// The dock: primary navigation, 60px, desktop only.
//
// Home, All chats, a hairline, then one tile per installed app, then the
// avatar pinned to the bottom. There is no Settings tile (the avatar owns
// account and settings) and there is no Search or Activity tile yet, because
// neither has a backend to answer it. See
// docs/platform-redesign/navigation-shell-2026-08-20.md sections 8.3 and 8.4:
// they arrive in stages 2 and 3, with their endpoints, not before.
//
// A TILE IS A LINK THAT SOMETIMES IS NOT. Every tile carries a real `href`, so
// middle-click, copy-link and hover preview all work. Clicking the tile of the
// place you are ALREADY in does not navigate: it toggles the page column, which
// is what makes the dock behave like a dock. That is the one case where the
// click is intercepted.

import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import type { Shell } from "./useShell";
import type { ShellPlace } from "./places";

function initials(name: string | null | undefined, email: string): string {
  const source = (name || email || "").trim();
  if (!source) return "?";
  const words = source.split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : source.slice(0, 2);
  return letters.toUpperCase();
}

function Tile({ shell, place }: { shell: Shell; place: ShellPlace }) {
  const active = shell.place.id === place.id;
  const Icon = place.Icon;
  return (
    <Link
      to={shell.hrefFor(place)}
      className={"ws-rail__tile" + (active ? " is-active" : "")}
      aria-current={active ? "page" : undefined}
      aria-label={place.name}
      title={place.name}
      onClick={(e) => {
        // Only the plain click. A modified click is the operator asking the
        // browser for a new tab, and toggling a column there would be wrong.
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        if (!active) return;
        e.preventDefault();
        shell.selectPlace(place);
      }}
    >
      {Icon ? <Icon size={18} aria-hidden /> : <span aria-hidden>{place.letter}</span>}
    </Link>
  );
}

export function Dock({ shell }: { shell: Shell }) {
  const { user } = useAuth();
  const fixed = shell.places.filter((p) => p.kind !== "app");
  const apps = shell.places.filter((p) => p.kind === "app");

  return (
    <nav className="ws-rail" aria-label="Places">
      {fixed.map((p) => (
        <Tile key={p.id} shell={shell} place={p} />
      ))}
      {apps.length > 0 && <span className="ws-rail__rule" aria-hidden />}
      {apps.map((p) => (
        <Tile key={p.id} shell={shell} place={p} />
      ))}
      <span className="ws-rail__spacer" aria-hidden />
      <Link
        to="/settings"
        className="ws-rail__avatar"
        aria-label="Account and settings"
        title="Account and settings"
      >
        {initials(user?.name, user?.email ?? "")}
      </Link>
    </nav>
  );
}
