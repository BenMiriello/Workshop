import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// A generic "tell my host what my current page is called" mechanism.
//
// Originated as part of Workshop's `components/appNav.tsx`, promoted here
// 2026-08-20 (Stage 4b) because the OVERRIDE half of it, a mounted page
// announcing its own dynamic label or a full custom trail, has nothing
// platform-specific in it: it is a page telling its host what it is, which
// any app-hosting shell wants. The RESOLUTION half, mapping a route to its
// default breadcrumb trail from a route table that knows every platform page
// and every installed app, stays in the host (Workshop's `components/
// appNav.tsx`): that table is inherently platform knowledge, not something a
// kit consumer should have to hand this package.
//
// The host wraps its tree in `BreadcrumbProvider`, computes the route's
// default trail itself, and folds in whatever `useBreadcrumbOverride()`
// currently holds (see `useCrumbs` in Workshop's `appNav.tsx` for the worked
// example). A page, platform or app, calls `useCrumbLabel` or `useCrumbTrail`
// and does not need to know the resolution side exists.

export type Crumb = { label: string; to?: string };

type Override = { trail?: Crumb[]; leafLabel?: string };

const SetCtx = createContext<(o: Override) => void>(() => {});
const ValueCtx = createContext<Override>({});

export function BreadcrumbProvider({ children }: { children: ReactNode }) {
  const [override, setOverride] = useState<Override>({});
  return (
    <SetCtx.Provider value={setOverride}>
      <ValueCtx.Provider value={override}>{children}</ValueCtx.Provider>
    </SetCtx.Provider>
  );
}

/** Override the leaf (current-page) crumb label with an entity name. Pass a
 *  falsy value while the entity is still loading; the host's route default
 *  shows until then. Safe to call unconditionally (hooks rule). */
export function useCrumbLabel(label?: string | null) {
  const setOverride = useContext(SetCtx);
  useEffect(() => {
    if (!label) return;
    setOverride({ leafLabel: label });
    return () => setOverride({});
  }, [label, setOverride]);
}

/** Supply a fully custom trail (everything after the host's root crumb). Use
 *  when the route hierarchy alone can't express the trail, e.g. an entity
 *  scoped under a parent that isn't in the path. */
export function useCrumbTrail(trail: Crumb[]) {
  const setOverride = useContext(SetCtx);
  const key = trail.map((c) => `${c.label}:${c.to ?? ""}`).join("|");
  useEffect(() => {
    setOverride({ trail });
    return () => setOverride({});
    // key captures the trail's content; `trail` identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setOverride]);
}

/** Read-side: whatever the currently mounted page has announced, or `{}` if
 *  nothing has. For the HOST's own trail resolver to fold in; a page does not
 *  read this directly. */
export function useBreadcrumbOverride(): Override {
  return useContext(ValueCtx);
}
