import { useEffect, useState } from "react";
import { Button, Modal, StateBlock } from "@workshop/ui";
import { chatApi } from "../../lib/chat";

// LOOKING AT AN ATTACHMENT NEVER LEAVES THE PAGE YOU ARE ON.
//
// This replaces `<a target="_blank">` on every attachment in the app. That link
// opened the raw file URL in a new window, which is survivable in a desktop
// browser tab and a trap on mobile: in a standalone/installed app there is no
// tab strip and no back affordance, so the operator was stuck on a bare image
// and had to force-quit the app to get back to the chat. Reported 2026-08-12.
//
// So: an overlay over the surface you were already on, with Esc, a scrim click
// and a visible Close, all of which Modal already gives us. The chat keeps its
// scroll position; the Memory table keeps its filters.
//
// Deliberately NO download button. A download link is the one control that can
// re-create the original bug, because a browser that ignores the `download`
// attribute navigates to the file instead, and that behaviour is exactly what
// is unreliable in an installed mobile app. On a phone a long press on the
// image below is the native save, and it costs us nothing to leave that to the
// platform.

export type ViewableAttachment = {
  id: string;
  name: string;
  kind: "image" | "pdf" | "text";
};

export default function AttachmentViewer({
  file, onClose,
}: {
  file: ViewableAttachment;
  onClose: () => void;
}) {
  const url = chatApi.attachmentUrl(file.id);
  const [failed, setFailed] = useState(false);
  // Text is fetched rather than framed: an iframe of text/plain inherits none
  // of the page's typography and scrolls as its own document inside the modal.
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    if (file.kind !== "text") return;
    let cancelled = false;
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (!cancelled) setText(t); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [url, file.kind]);

  let body;
  if (failed) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't open this file"
        hint={<>The bytes may have been deleted. <code>{url}</code></>}
      />
    );
  } else if (file.kind === "image") {
    body = (
      <img
        className="chat-att-view__img"
        src={url}
        alt={file.name}
        onError={() => setFailed(true)}
      />
    );
  } else if (file.kind === "pdf") {
    body = <iframe className="chat-att-view__frame" src={url} title={file.name} />;
  } else if (text === null) {
    body = <p className="ws-muted">Loading the file.</p>;
  } else {
    body = <pre className="chat-att-view__text">{text}</pre>;
  }

  // An EXPLICIT Close, because Modal itself ships neither. It closes on Esc and
  // on a scrim click, and on the phone this is reported from there is no Esc
  // key and the scrim can be a thin margin around a large image. A visible
  // button is the one exit that is always there, which is the whole point of
  // this change.
  return (
    <Modal
      open
      onClose={onClose}
      title={file.name}
      size="lg"
      actions={<Button onClick={onClose} data-autofocus>Close</Button>}
    >
      <div className="chat-att-view">{body}</div>
    </Modal>
  );
}
