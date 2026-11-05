// Settings, Speech, dictations: the last day of them, which the platform
// keeps (SpeechRecent.tsx), and the app that keeps them longer, with their
// recordings: whichever running app says in its manifest that it receives the
// `dictated` event.

import { useState } from "react";
import { Link } from "react-router-dom";
import { AppWindow, History } from "lucide-react";
import { Button, Row } from "@workshop/ui";
import { overlayApps, useOverlayApps } from "../../lib/overlayApps/store";
import { SpeechRecent } from "./SpeechRecent";

export function SpeechRecordings() {
  const { apps } = useOverlayApps();
  const [open, setOpen] = useState(false);
  const keepers = apps.filter((a) => a.events?.includes("dictated"));
  return (
    <Row wrap>
      <Button size="sm" onClick={() => setOpen(true)}>
        <History aria-hidden /> Last day
      </Button>
      {keepers.map((a) => (
        <Button key={a.slug} size="sm" onClick={() => overlayApps.open(a.slug)}>
          <AppWindow aria-hidden /> Open {a.title}
        </Button>
      ))}
      {!keepers.length && <Link to="/settings/apps">Keep them longer with an app</Link>}
      <SpeechRecent open={open} onClose={() => setOpen(false)} />
    </Row>
  );
}
