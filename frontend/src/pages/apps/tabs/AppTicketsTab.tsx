// The Tickets tab: this app's own queue.
//
// IT IS THE TICKETS SURFACE, pinned to this app's scope, not a second list of
// the same rows. It used to be its own list -- no search, no filter chips, no
// sort, no pagination, no view switcher -- reading a bespoke
// `/api/apps/{id}/tickets` that returns every ticket in one response, of which
// it rendered the first 300. Reported by the operator: "the app Tickets tab is
// a worse copy of /tickets; make it that view or link into it."
//
// MOUNTED, NOT LINKED, and the bespoke list is deleted in the same commit
// (docs/platform-redesign/threads-branches-and-pages-2026-08-29.md: "a change
// deletes the surface it replaces"; scripts/check_dead_surface.py enforces it).
// Linking out would leave the tab a worse copy on the way to the real one, and
// a tab that navigates out of the app breaks the one-header rule the app nav is
// built on (frontend/CLAUDE.md, "App navigation").
//
// WHICH SCOPE: `scopeForContext` (lib/scope.ts), the client half of
// `scope_tree.scope_for_context`, which is what `apps.owned_scope` calls on the
// server. NOT a hardcoded `app:<slug>`: an app whose linked project is a ROOT
// scope owns that root scope, and `app:<its slug>` names a store nothing writes
// to. This tab spelled the prefixed form by hand, so for such an app its ticket
// links and its "browse the full queue" link pointed at an empty store.
// Measured 2026-09-01: one app here is in that case, and its real scope holds
// 428 tickets where the `app:`-prefixed spelling holds 0.
import { useParams } from "react-router-dom";
import { StateBlock } from "@workshop/ui";
import TicketsView from "../../../components/tickets/TicketsView";
import { scopeForContext } from "../../../lib/scope";
import { AppTabPage } from "./AppTabShell";
import { unlinkedReason } from "./appTabState";

export default function AppTicketsTab() {
  const { appSlug = "" } = useParams();
  return (
    <AppTabPage slug={appSlug} title="Tickets" sub="This app's own ticket queue.">
      {(app) =>
        // AN UNLINKED APP HAS NO TASK STORE, and says so rather than asking for
        // one. `tabsForApp` does not draw this tab for such an app, but a deep
        // link or a stale bookmark still lands here, and GET /api/tasks answers
        // a scope with no root with 400 "unknown scope" -- an error region over
        // a state that is not an error. This is the same sentence the tab has
        // always shown, from the same rule (appTabState.unlinkedReason).
        app.project_slug ? (
          // `header={false}`: the page header above already carries the
          // eyebrow, the title and the app's ref pill. The list's own count
          // still reads in the search placeholder and under the pager.
          <TicketsView scope={scopeForContext(app.slug)} header={false} />
        ) : (
          <StateBlock
            title="No tickets"
            hint={unlinkedReason("tickets", app.project_slug, 0) ?? ""}
          />
        )
      }
    </AppTabPage>
  );
}
