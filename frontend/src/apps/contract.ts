// THE CONTRACT between the shell and an app bundle it loads at runtime.
//
// Pure types and two constants. Nothing here imports from the shell, and
// nothing in the shell has to be imported to write an app against it -- which
// is the point: an app is compiled somewhere else, by a build the platform does
// not run, and this file is the whole of what both sides have to agree on.
//
// THE THREE RULES, each of which exists because of something the old build-time
// registry had to do:
//
//   1. A PAGE COMPONENT TAKES NO PROPS. `apps/registry.tsx` passed
//      `groupNav={<AppGroupNav />}` and `renderChatDock={makeChatDock(id)}` into
//      pages, and those props are the reason a registry has to know an app's
//      id. They arrive through `@workshop/app-host` instead.
//   2. THE APP NEVER CONSTRUCTS A <Route>. It declares `{path, export, layout}`
//      and the shell builds the router elements. That is what keeps the mount
//      vocabulary closed, and it is also why a second copy of react-router
//      could not break routing even if one were loaded: no `<Route>` element
//      ever crosses the boundary.
//   3. `contract` IS ON THE BUNDLE AS WELL AS IN THE MANIFEST. The manifest says
//      what the app claims; the bundle says what it is. A manifest is hand
//      edited and a bundle is not, so the two are checked against each other.
import type { ComponentType } from "react";
import type { components } from "../api/schema.gen";

/** The shape below. Bumped only when THIS file's shape changes, which includes
 *  a React major: React is external to an app's bundle, so an app compiled
 *  against 18 would silently receive 19 the day the shell moves. */
export const APP_UI_CONTRACT = 1;

/** The closed vocabulary the platform publishes. The shell reads `mount` and
 *  never reads an app's id to decide anything. */
export const APP_MOUNTS = [
  "app.route",
  "app.layout",
  "app.settings",
  "app.session-provider",
] as const;

export type AppMount = (typeof APP_MOUNTS)[number];

/** One app's declaration as the platform serves it. Named from the generated
 *  client, so a change to the route's model is a red typecheck here. */
export type AppDeclaration = components["schemas"]["AppUiContribution"];
export type AppDeclarationPage = components["schemas"]["AppUiPage"];

/** What an app's built ESM entry default-exports. Components take no props. */
export type WorkshopAppBundle = {
  contract: number;
  exports: Record<string, ComponentType>;
};

/** Why an app did not activate, as data rather than as a thrown error.
 *
 *  `loadApp` resolves this instead of rejecting, and that is not a style
 *  preference: a rejection that escapes into render is a blank page, which is
 *  the one outcome `lib/lazyPage.tsx` exists to prevent for the shell's own
 *  chunks. The same rule, extended to code the platform did not compile. */
export type AppLoadFailure = {
  ok: false;
  /** One line, already written for a reader. Never an exception message alone. */
  reason: string;
  /** Where to look: the URL, the export name, the version that did not match. */
  detail?: string;
};

export type AppLoadResult = { ok: true; bundle: WorkshopAppBundle } | AppLoadFailure;
