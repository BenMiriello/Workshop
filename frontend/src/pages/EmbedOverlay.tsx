// The overlay app that keeps copies, alone, for the Mac app's paste panel
// (shared/native-bridge.ts PANEL_CHANNEL): a shortcut pressed in any app on
// the Mac opens this page in a floating panel over it.
//
//   /embed/overlay?into=<name of the app the panel opened over>
//
// It speaks the overlay messages the shell's own host speaks
// (lib/overlayApps/messages.ts). The app is told the app it opened over as
// its field; `insertText`, from an app that sends it, hands the text to the
// panel, which puts it on the clipboard and pastes it there. The panel shows the
// page again rather than loading it, so each showing remounts the app's frame
// (fresh history, its search focused) and putting it away unmounts it.

import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Skeleton, StateBlock } from "@workshop/ui";
import { useSearchParams } from "react-router-dom";
import {
  PANEL_CLOSE_EVENT, PANEL_OPEN_EVENT, type PanelOpenDetail,
} from "@shared/native-bridge";
import { contextMessage, parseOverlayMessage } from "../lib/overlayApps/messages";
import { nativePanel, panelApp } from "../lib/overlayApps/panel";
import { overlayApps, useOverlayApps } from "../lib/overlayApps/store";
import "../components/shell/overlays/overlays.css";

type Load = "loading" | "ready" | "failed";

export default function EmbedOverlay() {
  const [search] = useSearchParams();
  const into = useRef(search.get("into") ?? "");
  const frame = useRef<HTMLIFrameElement>(null);
  const [showing, setShowing] = useState(1);
  const [load, setLoad] = useState<Load>("loading");
  const app = panelApp(useOverlayApps().apps);

  const read = useCallback(() => {
    void overlayApps.load().then((ok) => setLoad((was) => (ok ? "ready" : was === "ready" ? was : "failed")));
  }, []);

  useEffect(() => {
    read();
    const onOpen = (e: Event) => {
      into.current = (e as CustomEvent<PanelOpenDetail>).detail?.into ?? "";
      setShowing((n) => n + 1);
      read();
    };
    const onClose = () => setShowing(0);
    window.addEventListener(PANEL_OPEN_EVENT, onOpen);
    window.addEventListener(PANEL_CLOSE_EVENT, onClose);
    return () => {
      window.removeEventListener(PANEL_OPEN_EVENT, onOpen);
      window.removeEventListener(PANEL_CLOSE_EVENT, onClose);
    };
  }, [read]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || e.source !== win || e.origin !== window.location.origin) return;
      const req = parseOverlayMessage(e.data);
      if (!req) return;
      if (req.kind === "hello") {
        win.postMessage(contextMessage(into.current || null), window.location.origin);
      } else if (req.kind === "insert") {
        nativePanel.send({ t: "insert", text: req.text });
      } else {
        nativePanel.send({ t: "close" });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <div className="ws-root ws-panel-overlay">
      {app ? (
        showing > 0 && (
          <iframe
            ref={frame}
            key={`${app.slug}:${showing}`}
            src={app.url}
            title={app.title}
            className="ws-panel-overlay__frame"
            onLoad={() => frame.current?.contentWindow?.focus()}
          />
        )
      ) : load === "loading" ? (
        <div className="ws-panel-overlay__state"><Skeleton /><Skeleton width="60%" /></div>
      ) : load === "failed" ? (
        <StateBlock
          variant="error"
          title="Couldn't read the apps that open over pages"
          hint="GET /api/apps/overlays failed."
          action={<Button onClick={read}>Retry</Button>}
          className="ws-panel-overlay__state"
        />
      ) : (
        <StateBlock
          title="No app opens over pages"
          hint="Turn on an app that declares an overlay, such as a clipboard app, in Settings > Apps."
          className="ws-panel-overlay__state"
        />
      )}
    </div>
  );
}
