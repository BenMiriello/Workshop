// Shared chrome for every generic per-app tab (Chats, Agents, Memory, Files,
// Tickets, Runs, Deploys). The tab itself is highlighted by the shell's page
// column (places.ts), so this renders only the page's own header and the
// app-record loading/error states every tab needs before it can call any
// nested /api/apps/{id}/... route.
import type { ReactNode } from "react";
import { PageHeader, StateBlock, Card, Stack, Skeleton, Button, Notice } from "@workshop/ui";
import { useApp, type AppRecord } from "../../../lib/apps";
import { usePageChrome } from "../../../components/shell/takeover";
import { useAppRef } from "../../../components/holders/useHolders";
import { stalenessNote } from "../../../lib/holders";

export function AppTabPage({
  slug, title, sub, actions, children,
}: {
  slug: string;
  title: string;
  sub?: ReactNode;
  actions?: (app: AppRecord) => ReactNode;
  children: (app: AppRecord) => ReactNode;
}) {
  const { app, err, reload } = useApp(slug);
  // WHO IS WRITING THIS APP'S TREE, for the staleness line below.
  //
  // THE BRANCH PICKER IS NOT HERE ANY MORE (moved 2026-09-01). It was in this
  // page's `PageHeader` actions, which meant a branch control drawn inside the
  // body of every app tab page, on every one of them. The operator: "it's just
  // on every page inside the page, which makes no sense. It should be in the
  // toolbar ... by the title." It is now in the shell's own chrome, once per
  // viewport, off one read that `useShell` holds. Do not add it back here.
  //
  // What is left is the staleness join, which is about this PAGE's content
  // rather than about the chrome: the rows on screen are still readable and one
  // thing about them is no longer guaranteed current.
  const refState = useAppRef({ projectSlug: app?.project_slug, appId: app?.id });
  const stale = stalenessNote(refState.writers);
  // THE PAGE'S CONTENT OWNS THE SCREEN IN TWO CASES, and the tab declares which
  // (components/shell/takeover.tsx); this reads them as one answer.
  //
  //   * A DOCUMENT TAKEOVER (shell-mockup-spec-2026-08-22.md section 6). The
  //     shell drops its page toolbar for it, so the page's own header goes too:
  //     on a 390px screen the point of the takeover is that the file starts at
  //     the top, and "Files" above an open file is a label for a page you can no
  //     longer see.
  //   * A DIRECTORY PAGE ON A PHONE (section 4, "The tree IS the page
  //     content"). The h1 read "Files" directly under a page tab already
  //     reading "Files", above a description nobody needs twice. Measured
  //     2026-08-22: ~290px of it before the first row.
  //
  // Only the Files tab raises either, and only on a phone. Every other tab, and
  // every tab on a desktop, keeps its header exactly as it was.
  const chrome = usePageChrome();

  // `.ws-page` opts this generic tab back into the standard content measure
  // (frontend/CLAUDE.md "Page width"). Its shell runs full-bleed under
  // `.ws-app--appfull` (Shell.tsx keys that off the `/apps/...` prefix, which
  // every one of these routes matches) -- appropriate for an app's OWN
  // custom-built screen, wrong for a platform-rendered list page that should
  // read like Tickets or Settings.
  if (err && !app) {
    return (
      <div className="ws-page">
        {chrome && <PageHeader title={title} />}
        <StateBlock
          variant="error"
          title="Couldn't load this app"
          hint={<code>GET /api/apps ({err})</code>}
          action={<Button size="sm" onClick={reload}>Retry</Button>}
        />
      </div>
    );
  }
  if (!app) {
    return (
      <div className="ws-page">
        {chrome && <PageHeader title={title} />}
        <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="40%" /></Stack></Card>
      </div>
    );
  }

  return (
    <div className="ws-page">
      {chrome && (
        // THE PAGE SAYS WHICH APP IT BELONGS TO.
        //
        // The h1 was the tab name alone, so opening an app landed you on a page
        // headed "Chats" or "Tickets" with nothing on screen naming the app. For
        // an app that ships its own crumbs that was survivable; for one that
        // does not, the only identifier left was a 40px glyph in the dock, and
        // the app's page column is collapsed by default (places.ts), so its own
        // nav was not visible either. Reported 2026-08-31, on the app that is
        // this repo: "development STILL opens to the OLD chats page".
        //
        // An eyebrow, not a bigger title: it is the same shape the Workshop
        // pages already use for their scope (WORKSHOP over "Tickets"), so this
        // adds no new idiom, and the tab name stays the h1 because the tab is
        // what the page is showing.
        <PageHeader
          eyebrow={app.name}
          title={title}
          sub={sub}
          actions={actions?.(app)}
        />
      )}
      {/* READER STALENESS (leases design section 11). A LINE, not a region: the
          content is still on screen and still readable, one thing about it is
          just no longer guaranteed current. There is deliberately no
          invalidation here. Section 11 is explicit that no protocol fixes a
          read that is already in a session's context window, and that the only
          real remedy is a message into that session, which is the steer verb in
          the directory sheet rather than a new mechanism. */}
      {stale && <Notice tone="warn">{stale}</Notice>}
      {children(app)}
    </div>
  );
}

// Loading/error/empty/overflow, in one place, for the plain-list tabs
// (Chats, Agents, Memory, Tickets, Runs, Deploys). Files draws its own body
// -- it is a tree, not a list (frontend/CLAUDE.md's directory-vs-collection
// distinction).
const MAX_ROWS = 300;

export function CollectionBody<T>({
  items, err, reload, endpoint, emptyTitle, emptyHint, emptyAction, overflowHint, render,
}: {
  items: T[] | null;
  err: string | null;
  reload: () => void;
  endpoint: string;
  emptyTitle: string;
  emptyHint: ReactNode;
  /** The one thing to do from here when there is nothing yet. Optional: a tab
   *  whose collection is filled from somewhere else (runs, deploys) has no
   *  action to offer and must not grow a button that leads nowhere. */
  emptyAction?: ReactNode;
  /** ReactNode, not string: these lines name a destination ("Browse the full
   *  queue from Tickets") and every one of them was plain text, so the page
   *  told you where to go and made you get there yourself. */
  overflowHint: ReactNode;
  render: (items: T[]) => ReactNode;
}) {
  if (err) {
    return (
      <StateBlock
        variant="error"
        title="Couldn't load"
        hint={<code>{endpoint} ({err})</code>}
        action={<Button size="sm" onClick={reload}>Retry</Button>}
      />
    );
  }
  if (items === null) {
    return <Card><Stack gap="sm"><Skeleton width="60%" /><Skeleton width="45%" /><Skeleton width="50%" /></Stack></Card>;
  }
  if (items.length === 0) {
    return <StateBlock title={emptyTitle} hint={emptyHint} action={emptyAction} />;
  }
  // OVERFLOW SHOWS THE CONTENT. This used to return a region reading "Too many
  // to render" and nothing else, so an app with 409 tickets had a Tickets tab
  // that displayed zero of them and told the operator to go delete some. That
  // is the failure state wearing the overflow state's name: a long list is not
  // an error, and the four-states rule (frontend/CLAUDE.md rule 9) says overflow
  // is paginated or scrolled, never withheld.
  //
  // A LINE, not a region, for the same reason the rest of this file uses one:
  // the content is on screen and one thing about it is qualified.
  if (items.length > MAX_ROWS) {
    return (
      <>
        <Notice>
          Showing the first {MAX_ROWS} of {items.length}. {overflowHint}
        </Notice>
        {render(items.slice(0, MAX_ROWS))}
      </>
    );
  }
  return <>{render(items)}</>;
}
