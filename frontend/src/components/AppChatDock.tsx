import { useEffect, useState } from "react";
import ChatDock from "./ChatDock";
import type { components } from "../api/schema.gen";

// Manifest-gated wrapper around ChatDock. Every app mounts this; whether the
// floating assistant button actually renders is driven by the app's manifest
// (`ui.agent_button`, GET /api/base-apps/manifests/<appId>), which the operator
// toggles per app from the Apps "Manage" view. Defaults to showing the dock —
// if the manifest can't be read, we fail open so the assistant stays reachable.

type Props = {
  appId: string;
  contextKey?: string;
  title?: string;
  placeholder?: string;
  onAction?: () => void;
  mode?: "data" | "build";
};

export default function AppChatDock({ appId, contextKey, title, placeholder, onAction, mode }: Props) {
  const [show, setShow] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/base-apps/manifests/${appId}`)
      .then((r) => (r.ok ? (r.json() as Promise<components["schemas"]["AppManifest"]>) : null))
      .then((m) => {
        if (alive) setShow(m?.ui?.agent_button !== false);
      })
      .catch(() => {
        if (alive) setShow(true); // fail open: keep the assistant reachable
      });
    return () => {
      alive = false;
    };
  }, [appId]);

  if (!show) return null;
  return (
    <ChatDock
      contextKey={contextKey ?? appId}
      title={title}
      placeholder={placeholder}
      onAction={onAction}
      mode={mode}
    />
  );
}
