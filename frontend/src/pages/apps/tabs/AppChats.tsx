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
// THE HEADER IS THE LIST'S, NOT THE SHELL'S (2026-09-01, second pass). Mounting
// the list under `AppTabPage`'s own header left the filter and New chat with
// nowhere to go but a row of their own, and a 90px band of empty space between
// the subtitle and the first chat. /chats has no such band, because there the
// same controls sit on the title's line. Shot at 1440px, that band -- and the
// row actions drawing on top of the meta, fixed in the kit the same day -- were
// the whole of what was left of "it is still the old chats page". So the shell
// keeps the app record, the staleness line and the loading and error states,
// and hands the h1 to the list, which puts the app's name in the eyebrow
// exactly where the shell had it.
import { useParams } from "react-router-dom";
import { ChatsView } from "../../Chats";
import { AppTabPage } from "./AppTabShell";

export default function AppChats() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Chats" header={false}>
      {(app) => (
        <ChatsView
          app={{ id: app.id, slug: app.slug, name: app.name }}
          eyebrow={app.name}
          sub="Every conversation in this app."
        />
      )}
    </AppTabPage>
  );
}
