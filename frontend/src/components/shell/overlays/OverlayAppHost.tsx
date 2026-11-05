// An app opened over whatever page the operator is on, in a centred modal
// (docs/declaring-an-app-surface.md, "Opening over any page: overlay").
//
// It opens from the app's shortcut, pressed in the shell or in any same-origin
// app frame, or from the account menu. Before it opens, the field the operator
// was in is remembered: the one focused now, else the last one focused, which
// is the field a menu click just took focus from. The app asks with `hello`
// and is told that field's name; `insertText` closes the overlay, gives the
// field its focus back and puts the text at its caret.

import { useEffect, useRef } from "react";
import { Modal, usePoll } from "@workshop/ui";
import { isMacLike } from "../../../lib/speech/shortcuts";
import { contextMessage, parseOverlayMessage } from "../../../lib/overlayApps/messages";
import { chordFor, overlayShortcuts } from "../../../lib/overlayApps/prefs";
import { pressed } from "../../../lib/overlayApps/shortcut";
import { overlayApps, useOverlayApps } from "../../../lib/overlayApps/store";
import { watchDocuments } from "../sameOriginFrames";
import { deepActiveElement } from "../speech/frames";
import { captureTarget, insertAtTarget, type DictationTarget } from "../speech/insertAtTarget";
import { fieldLabel } from "./fieldLabel";
import "./overlays.css";

const POLL_MS = 60_000;

function usable(t: DictationTarget | null): t is DictationTarget {
  return !!t && t.el.isConnected && !!t.el.ownerDocument.defaultView;
}

/** Whether `el` is inside the overlay's own frame. Nothing there is
 *  remembered: its own fields are not where its text goes. */
function within(overlay: HTMLIFrameElement | null, el: Element): boolean {
  const win = el.ownerDocument.defaultView;
  return !!overlay && !!win && win.frameElement === overlay;
}

function refocus(t: DictationTarget): void {
  const frame = t.el.ownerDocument.defaultView?.frameElement as HTMLElement | null;
  frame?.focus({ preventScroll: true });
  t.el.focus({ preventScroll: true });
}

export function OverlayAppHost() {
  const { apps, open } = useOverlayApps();
  const app = apps.find((a) => a.slug === open) ?? null;
  const frame = useRef<HTMLIFrameElement>(null);
  const last = useRef<DictationTarget | null>(null);
  const target = useRef<DictationTarget | null>(null);

  usePoll(() => overlayApps.refresh(), POLL_MS, { whenHidden: "pause" });

  useEffect(() => {
    const mac = isMacLike();
    return watchDocuments((doc) => {
      const onFocus = (e: FocusEvent) => {
        const el = e.target as Element | null;
        if (!el || within(frame.current, el)) return;
        const t = captureTarget(el);
        if (t) last.current = t;
      };
      const onKey = (e: KeyboardEvent) => {
        if (e.defaultPrevented) return;
        const { apps: list, open: current } = overlayApps.get();
        if (current && e.key === "Escape" && doc !== document) {
          e.preventDefault();
          overlayApps.close();
          return;
        }
        const overrides = overlayShortcuts.get();
        const hit = list.find((a) => {
          const chord = chordFor(a.slug, a.shortcut, overrides, mac);
          return !!chord && pressed(e, chord);
        });
        if (!hit) return;
        e.preventDefault();
        if (current !== hit.slug) {
          const active = deepActiveElement();
          const now = active && !within(frame.current, active) ? captureTarget(active) : null;
          target.current = now ?? (usable(last.current) ? last.current : null);
        }
        overlayApps.toggle(hit.slug);
      };
      doc.addEventListener("focusin", onFocus, true);
      doc.addEventListener("keydown", onKey);
      return () => {
        doc.removeEventListener("focusin", onFocus, true);
        doc.removeEventListener("keydown", onKey);
      };
    });
  }, []);

  // Opened from the menu: focus is on the menu, so the field is the last one.
  useEffect(() => {
    if (open && !target.current) target.current = usable(last.current) ? last.current : null;
    if (!open) target.current = null;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onMessage = (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      if (!win || e.source !== win || e.origin !== window.location.origin) return;
      const req = parseOverlayMessage(e.data);
      if (!req) return;
      const t = usable(target.current) ? target.current : null;
      if (req.kind === "hello") {
        win.postMessage(contextMessage(t ? fieldLabel(t.el) : null), window.location.origin);
        return;
      }
      overlayApps.close();
      if (req.kind !== "insert" || !t) return;
      // After the modal has handed focus back to where it came from.
      window.setTimeout(() => {
        if (!usable(t)) return;
        refocus(t);
        insertAtTarget(t, req.text);
      }, 0);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [open]);

  return (
    <Modal open={!!app} onClose={() => overlayApps.close()} title={app?.title} size="lg">
      {app && (
        <iframe
          ref={frame}
          key={app.slug}
          src={app.url}
          title={app.title}
          className="ws-overlay-app__frame"
        />
      )}
    </Modal>
  );
}
