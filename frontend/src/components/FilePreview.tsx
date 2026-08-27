// A file the browser can show by itself: an image, or a PDF.
//
// This is the other half of "render the file, not the bytes". A ```mermaid
// fence inside markdown is drawn by the kit (packages/ui/src/MermaidDiagram.tsx);
// a file that is not markdown at all is drawn here, at the one file-content
// surface both the Files tab and Memory already use.
//
// NOTHING IS DECODED IN THE APP. An `<img>` and an `<iframe>` are the whole
// implementation: every browser Workshop runs in already has an image decoder
// and a PDF viewer, and a bundled PDF renderer would cost more than the entire
// rest of the app to do worse. The bytes come from `GET /api/files/raw`, which
// serves them with their real media type and with `default-src 'none'` so an
// agent-authored SVG cannot run as same-origin script.
//
// Which files get here is `previewKindFor` in lib/filePreview.ts, tested there.
import { useState } from "react";
import { Notice } from "@workshop/ui";
import { previewKindFor, rawFileUrl } from "../lib/filePreview";

/** The frame a PDF gets when it is one pane among others. In the phone
 *  takeover the document owns the content area, so it fills it instead. Same
 *  arrangement, and the same reason, as the editor's height in AppFilesTab. */
const PDF_HEIGHT = "70vh";

export function FilePreview({
  workspace,
  path,
  name,
  takeover,
}: {
  /** A workspace reference the file API understands: `app:<slug>`,
   *  `project:<slug>`, `documents`. */
  workspace: string;
  path: string;
  /** The file's own name, for the alt text and the frame title. Passed in
   *  rather than split off `path` here because both callers already hold it,
   *  and a component under `components/` should not be reaching into a page's
   *  helpers to re-derive something the page has. */
  name: string;
  takeover?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const kind = previewKindFor(path);
  const src = rawFileUrl(workspace, path);

  if (kind === "none") return null;

  // The file did not load. The document header and the file's own facts are
  // still on screen, so this is a line beside content, not a region.
  if (failed) {
    return (
      <Notice tone="error" where={<code>{src}</code>}>
        Couldn't load this file.
      </Notice>
    );
  }

  if (kind === "pdf") {
    return (
      <iframe
        title={name}
        src={src}
        onError={() => setFailed(true)}
        style={{
          width: "100%",
          height: takeover ? "100%" : PDF_HEIGHT,
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          background: "var(--bg-inset)",
        }}
      />
    );
  }

  return (
    <img
      // The file name is the only description anything here has. It is a real
      // one: an agent that wrote `architecture.png` named the picture.
      alt={name}
      src={src}
      onError={() => setFailed(true)}
      style={{
        maxWidth: "100%",
        height: "auto",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-inset)",
      }}
    />
  );
}
