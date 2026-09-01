// The Chats tab: every conversation bound to this app.
//
// IT MOUNTS THE CHAT LIST. It does not draw one. `ChatsView` (pages/Chats.tsx)
// is the list, `/chats` is a thin wrapper over the same component, and this is
// the same component with `app` set. Exactly the shape the Tickets tab already
// had, for exactly the same reason: a tab that draws its own list is a worse
// copy of the real one, and this repo has lost five design generations to
// parallel copies of the same surface.
//
// WHAT THE COPY WAS MISSING, measured 2026-09-01 by screenshotting both pages
// side by side: the armed filter, archive, delete, the delete confirmation,
// infinite scroll, and the running-dot ordinals. What it ADDED: a "CHATS"
// section heading under a page already headed Chats, and a scope tag reading
// "Development" on every row of the Development app's own page.
//
// The operator reported this on 2026-08-29, 2026-08-31 and twice on 2026-09-01.
// Three previous rounds changed how the copy rendered. The copy was the bug, so
// the copy is deleted rather than improved a fourth time.
import { useParams } from "react-router-dom";
import { ChatsView } from "../../Chats";
import { AppTabPage } from "./AppTabShell";

export default function AppChats() {
  const { appSlug = "" } = useParams();
  return (
    // No `actions` here: the list carries its own controls (the armed filter
    // and New chat), and they have to sit with the list because their state
    // does. A second New chat button in this header would be two doors to one
    // room, which is the duplication the mount exists to remove.
    <AppTabPage slug={appSlug} title="Chats" sub="Every conversation in this app.">
      {(app) => (
        <ChatsView
          app={{ id: app.id, slug: app.slug, name: app.name }}
          header={false}
        />
      )}
    </AppTabPage>
  );
}
