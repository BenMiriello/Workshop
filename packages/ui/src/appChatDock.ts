import type { ReactNode } from "react";

// Type-only contract for the shell-provided assistant dock renderer.
//
// `AppChatDock` itself (the floating assistant button, gated by an app's
// manifest, wired to Workshop's own chat threads) stays a platform component:
// it is deeply tied to Workshop's chat system and its `/api/base-apps/
// manifests/<id>` endpoint, not a generic design-system capability. What an
// app page legitimately needs is the ABILITY to ask its host to render one,
// with its own title/placeholder copy and an optional callback for when the
// assistant took an action the page should react to (e.g. reload its data).
//
// So the shell (Workshop's `apps/registry.tsx`) constructs a renderer bound
// to the app's own id and passes it down as a `renderChatDock` prop; the page
// calls it wherever it wants to mount the dock, or not at all (an app may gate
// its call on a `PUBLIC` flag). This type is the only piece of that contract
// an app page needs to import, so its prop is well-typed without pulling in
// the platform's `AppChatDock` component or the chat system it wraps.
export type AppChatDockOptions = {
  title?: string;
  placeholder?: string;
  onAction?: () => void;
};

export type AppChatDockRenderer = (opts?: AppChatDockOptions) => ReactNode;
