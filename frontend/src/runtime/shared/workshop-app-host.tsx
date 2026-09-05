// `@workshop/app-host` -- the services a runtime-loaded app page needs from the
// shell, published at a URL exactly like react is.
//
// WHY THIS IS NOT PROPS. `apps/registry.tsx` passes `groupNav={<AppGroupNav />}`
// and `renderChatDock={makeChatDock("<slug>")}` into pages, and those props are
// the reason the registry has to know an app's id at all. A loaded page takes
// no props (the contract in `src/apps/contract.ts` says so), so the same two
// services arrive through a context the shell provides and the app reads.
//
// WHY THIS IS NOT A KIT EXPORT. `@workshop/ui` is versioned, apps declare a
// `kit` range, and adding an export is a minor bump that every declared `^0.7`
// range excludes -- a fleet-wide refusal for a piece of host wiring that is not
// a design primitive. The shell owns host wiring; the kit owns primitives.
//
// The TYPES ship separately, from `@workshop/app-build/host`, so an app can
// typecheck against this seam without depending on the platform's source tree.
import "./guard";
import { createContext, useContext, type ComponentType, type ReactNode } from "react";
// TYPE ONLY, and erased: the dock renderer's shape is already a kit type
// (`packages/ui/api/ui.api.md`), so restating it here would be a second
// declaration of one contract, which is how the two drift.
import type { AppChatDockRenderer } from "@workshop/ui";

export type { AppChatDockRenderer };

export type AppHost = {
  appId: string;
  GroupNav: ComponentType;
  renderChatDock: AppChatDockRenderer;
};

const HostContext = createContext<AppHost | null>(null);

export function AppHostProvider({ value, children }: { value: AppHost; children: ReactNode }) {
  return <HostContext.Provider value={value}>{children}</HostContext.Provider>;
}

/** The host services for the app currently rendering.
 *
 *  Throws rather than returning null: reaching this outside an app route is a
 *  programming error, not a state a page could render something sensible for,
 *  and the throw lands in the shell's own `RouteBoundary` with the message
 *  intact. */
export function useAppHost(): AppHost {
  const host = useContext(HostContext);
  if (!host) {
    throw new Error(
      "useAppHost() was called outside an app route. The shell provides this " +
        "context around a loaded app bundle only.",
    );
  }
  return host;
}
