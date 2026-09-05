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

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { AppGlyph, monogram } from "../../lib/appIcon";
import { useAuth } from "../../auth/AuthProvider";
import type { Shell } from "./useShell";
import type { ShellPlace } from "./places";
import type { components } from "../../api/schema.gen";

/** The signed-in person's initials. The same two-letter rule an app with no
 *  icon falls back to, so it is literally the same function. */
const initials = (name: string | null | undefined, email: string) => monogram(name || email || "");

function Tile({ shell, place }: { shell: Shell; place: ShellPlace }) {
  const active = shell.place.id === place.id;
  const Icon = place.Icon;
  return (
    <Link
      to={shell.hrefFor(place)}
      className={
        "ws-rail__tile" +
        (place.kind === "app" ? " ws-rail__tile--app" : "") +
        (active ? " is-active" : "")
      }
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
      {/* A platform place imports its glyph; an app DECLARES one and AppGlyph
          resolves it (token, custom image, or a monogram of its name). The
          platform never imports an icon on an app's behalf. */}
      {Icon ? (
        <Icon size={18} aria-hidden />
      ) : (
        <AppGlyph icon={place.icon} name={place.name} size={18} />
      )}
    </Link>
  );
}

export function Dock({ shell }: { shell: Shell }) {
  const { user } = useAuth();
  const nav = useNavigate();
  const [creating, setCreating] = useState(false);

  // The "+" creates a project and drops the operator straight into a chat in it,
  // per the operator's direction (2026-08-25): no create form, build it up from
  // the chat. The scaffold is a blank template, so there is no GitHub side
  // effect. A unique slug avoids a collision; the project is renamed from the
  // chat, which is why the default title is generic.
  const startNewProject = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const rnd = (crypto.randomUUID?.() || String(Math.random()))
        .replace(/[^a-z0-9]/gi, "").slice(0, 6).toLowerCase();
      const r = await fetch("/api/projects/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: `new-project-${rnd}`, title: "New project" }),
      });
      if (!r.ok) throw new Error(await r.text().catch(() => r.statusText));
      const p = (await r.json()) as components["schemas"]["Project"];
      nav(`/chat/${encodeURIComponent(`project:${p.slug}`)}`);
    } catch (e) {
      // A failed create must not blank the shell; report and re-enable.
      console.error("new project failed", e);
      setCreating(false);
    }
  };
  // Settings is a place but NOT a tile: the avatar below is already the way in,
  // and two controls to one destination is the duplication the dock exists to
  // remove. It still highlights, because it is still a place.
  const fixed = shell.places.filter((p) => p.kind === "home" || p.kind === "chats");
  const apps = shell.places.filter((p) => p.kind === "app");
  const inSettings = shell.place.kind === "settings";

  return (
    <nav className="ws-rail" aria-label="Places">
      {fixed.map((p) => (
        <Tile key={p.id} shell={shell} place={p} />
      ))}
      {apps.length > 0 && <span className="ws-rail__rule" aria-hidden />}
      {apps.map((p) => (
        <Tile key={p.id} shell={shell} place={p} />
      ))}
      {/* Start a new project: create it and open a chat in it. An action, not a
          place, so it is a button and never highlights. The rail lists a fixed
          set of apps today, so the new project is reached through that chat
          rather than appearing as a rail tile, until the rail is backed by the
          app registry. See project-context-files-and-flows-2026-08-25.md. */}
      <button
        type="button"
        className="ws-rail__tile ws-rail__tile--add"
        aria-label="New project"
        title="New project"
        onClick={startNewProject}
      >
        <Plus size={18} aria-hidden />
      </button>
      <span className="ws-rail__spacer" aria-hidden />
      <Link
        to="/settings"
        className={"ws-rail__avatar" + (inSettings ? " is-active" : "")}
        aria-current={inSettings ? "page" : undefined}
        aria-label="Account and settings"
        title="Account and settings"
      >
        {initials(user?.name, user?.email ?? "")}
      </Link>
    </nav>
  );
}
