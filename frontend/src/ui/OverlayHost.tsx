import { createContext, useContext, useState, type ReactNode } from "react";

// WHERE OVERLAYS LAND IS THE SHELL'S JOB, NOT THE DIALOG'S.
//
// Every modal in the app used to portal straight to <body> and cover the whole
// window, including the Workshop top nav — so a dialog opened inside an app
// looked like it had escaped the app. The fix is not per-modal styling (a
// dialog must not have to know what chrome exists above it, or how tall it is).
// The shell wraps its content region in this provider, which renders a host
// element filling exactly that region, and `Modal` portals into it. Anything
// rendered through the host is automatically scoped below the top nav, with the
// shell's own padding around it, and nothing in an app has to participate.
//
// Outside a provider (the springer entry, tests) the context is null and Modal
// falls back to <body> with its normal fixed positioning.
const OverlayHostContext = createContext<HTMLElement | null>(null);

export function useOverlayHost(): HTMLElement | null {
  return useContext(OverlayHostContext);
}

// Renders `children` plus the host, in one element that fills the shell's
// content region. Position/inset live in `.ws-app__body` / `.ws-overlay-host`.
export function OverlayHost({ children }: { children: ReactNode }) {
  // State, not a ref: the host node must be in the DOM and cause a re-render
  // before a portal can target it, or the first modal of a session renders
  // into nothing.
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  return (
    <div className="ws-app__body">
      <OverlayHostContext.Provider value={host}>{children}</OverlayHostContext.Provider>
      <div className="ws-overlay-host" ref={setHost} />
    </div>
  );
}
