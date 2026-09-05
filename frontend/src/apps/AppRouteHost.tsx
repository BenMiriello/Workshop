// The component the shell puts in every route a runtime-loaded app declared.
//
// It is the ONLY place in the shell that knows an app bundle can fail. Nav,
// breadcrumbs, the dock tile, the icon, the switcher and the launcher card all
// come from JSON, so a broken app is still visible and still navigable, and the
// failure is a message inside one region instead of an absence everywhere.
//
// `AppSuspense` and the shell's own `RouteBoundary` are untouched: they still
// cover the shell's lazy chunks, and this sits inside them for the case an app
// throws during its own render.
import { useEffect, useState } from "react";
import { Button, StateBlock } from "@workshop/ui";
import { RouteSkeleton } from "../lib/lazyPage";
import { AppHostProvider } from "../runtime/shared/workshop-app-host";
import { AppGroupNav, makeChatDock } from "./appShell";
import type { AppDeclaration, AppLoadFailure } from "./contract";
import { bundleExport, loadApp, type AppExportResult } from "./loader";
import { refreshDeclarations } from "./declarations";

type State =
  | { kind: "loading" }
  | { kind: "ready"; Component: React.ComponentType }
  | ({ kind: "failed" } & AppLoadFailure);

export function AppRouteHost({
  app,
  exportName,
}: {
  app: AppDeclaration;
  exportName: string;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const entry = app.urls?.entry ?? "";
  const [retries, setRetries] = useState(0);

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    loadApp(app).then((result) => {
      if (!alive) return;
      const resolved: AppExportResult = bundleExport(result, exportName);
      setState(
        resolved.ok
          ? { kind: "ready", Component: resolved.Component }
          : { kind: "failed", ok: false, reason: resolved.reason, detail: resolved.detail },
      );
    });
    return () => {
      alive = false;
    };
    // `entry` carries the app's content hash, so a rebuilt app reloads and a
    // navigation between two of its pages does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry, exportName, retries]);

  if (state.kind === "loading") return <RouteSkeleton />;
  if (state.kind === "ready") {
    const { Component } = state;
    return (
      <AppHostProvider
        value={{
          appId: app.id,
          GroupNav: AppGroupNav,
          renderChatDock: makeChatDock(app.id),
        }}
      >
        <Component />
      </AppHostProvider>
    );
  }
  return (
    <StateBlock
      variant="error"
      title={`${app.link?.label || app.id} did not load`}
      hint={[state.reason, state.detail].filter(Boolean).join(" ")}
      action={
        <Button
          variant="primary"
          onClick={() => {
            // One refetch of the declarations before retrying: the usual cause
            // is that the app was rebuilt while this tab was open, so the URL
            // this tab holds carries a hash that no longer exists. That is the
            // app-bundle analogue of lazyPage's one-shot document reload, and
            // unlike a chunk 404 it needs no reload, because the shell can
            // re-derive the URL.
            void refreshDeclarations().then(() => setRetries((n) => n + 1));
          }}
        >
          Retry
        </Button>
      }
    />
  );
}
