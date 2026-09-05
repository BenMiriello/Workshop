// The types of `@workshop/app-host`, the shell module an app imports for the
// services the design kit does not own.
//
// The IMPLEMENTATION is the shell's (`frontend/src/runtime/shared/app-host.tsx`)
// and reaches the app through the import map, exactly as react does. Only the
// types ship here, so an app can typecheck against the seam without depending
// on the platform's source tree.
import type { ComponentType, ReactNode } from "react";

/** How a page asks for its own chat dock. The kit owns this type; it is
 *  restated structurally so an app needs no kit import to read this file. */
export type AppChatDockOptions = {
  title?: string;
  placeholder?: string;
  onAction?: (action: { kind: string; [key: string]: unknown }) => void;
};

export type AppHost = {
  /** The id the platform knows this app by. An app should not spell its own id
   *  in two places; this is the one the shell routed to. */
  appId: string;
  /** The sibling-app switcher for this app's declared group. Renders nothing
   *  when the app has no siblings. */
  GroupNav: ComponentType;
  /** A ready-to-mount chat dock bound to this app. */
  renderChatDock: (options?: AppChatDockOptions) => ReactNode;
};

/** The host services for the app currently rendering. Throws outside an app
 *  route, which is a programming error rather than a state to render. */
export declare function useAppHost(): AppHost;
