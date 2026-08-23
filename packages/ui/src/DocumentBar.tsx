// DocumentBar: the chrome around the file viewer and editor.
//
// ONE component for every surface that opens a file, because there were two
// near-copies of this header (the Files tab and Memory) and they had already
// drifted: different controls, different ink, one of them stacking a Row above
// a card and the other a page header. The operator's report on 2026-08-22 was
// that the file viewer and editor "does not match the design", and the design
// it does not match is the mockup, extracted as measurements in
// docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 6.
//
// THE GEOMETRY IS THE MOCKUP'S:
//
//   desktop  46px, gap 9, padding 0 20px, bottom hairline. The PATH in mono
//            12px --faint, the NAME in mono 12px --fg, the dirty dot, then
//            right-aligned Discard (quiet) and Save (filled accent, 13px/600).
//   mobile   44px, padding 0 14px. A COMBINED back-plus-filename control (a
//            13px chevron-left immediately followed by the name, both --accent
//            mono 12px, one tap target), the dirty dot, then Save. NO DISCARD.
//
// The off-grid values are snapped to the scale that exists, exactly as bar one
// already snaps the mockup's 14px padding to --space-3: the 4px spacing grid is
// design rule 6 and the mockup was never a token spec (spec section 7.6). The
// heights are the bar's own box, so they are named here like --bar1-h is.
//
// WHAT THE MOCKUP IS NOT:
//
//   - Its editor BODY is a placeholder, static spans with no text input. The
//     body is CodeMirror (Editor.tsx) and stays that way. Only this is chrome.
//   - It draws the amber dot UNCONDITIONALLY because its editor has exactly one
//     state, "editing with unsaved changes". Here dirty is a real boolean from
//     `useEditMode`, so the dot renders only when the document IS dirty, in
//     --warn. `documentBar.ts` holds that rule and every other one.
//   - It has no view/edit toggle and no read-only state. Those are absences
//     caused by the placeholder, not decisions: the rendered-versus-source rule
//     stands (file-viewer-and-editor-2026-08-22.md section 4).
//
// THE HEADER CARRIES THE ACTIONS AND THERE IS NO STICKY ActionBar HERE, which
// is the one deliberate departure from the surrounding pattern and is written
// down in full in `documentBar.ts` and in both consumers. frontend/CLAUDE.md
// rule 7 requires an explicit commit for a content write, not one specific
// container for it: this bar IS the explicit commit, permanently on screen at
// the top of the document instead of sticking to the bottom of it. Shipping
// both would put two Saves and two "unsaved" markers on one screen, and on a
// phone the spec says the bottom bar hides while a document is open, which
// would leave the ActionBar's Save unreachable exactly when it mattered.
import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { cx } from "./cx";

export interface DocumentBarProps {
  /** The file's own name. Mono, because a filename is not prose. */
  name: string;
  /** The directory that holds it, drawn ahead of the name in --faint. Hidden
   *  below 900px, where the header is a back control and there is no room. */
  path?: string;
  /** Unsaved changes. Draws the one dot, and nothing else in this app says
   *  "unsaved" on this surface. */
  dirty?: boolean;
  /** Leaving the document. On a phone this control and the name are ONE tap
   *  target, per the mockup, and it is what replaces the Up bar while a
   *  document is open. Omit it where there is a tree beside the file already. */
  onBack?: () => void;
  /** What Back reaches, for its accessible name ("Back to src/pages"). */
  backLabel?: string;
  /** One muted line about the FILE (size, when it was edited, read only).
   *  Hidden below 900px with the path, for the same reason. */
  meta?: ReactNode;
  /** The right-aligned controls. Kit `Button`s: `primary` for Save, `ghost`
   *  for Discard and for the form toggle. The bar sizes and rounds them. */
  children?: ReactNode;
  className?: string;
}

export function DocumentBar({
  name, path, dirty = false, onBack, backLabel, meta, children, className,
}: DocumentBarProps) {
  return (
    <div className={cx("ws-docbar", className)}>
      {onBack ? (
        // ONE tap target: the chevron and the name are a single button, not a
        // back button standing next to a label.
        <button
          type="button"
          className="ws-docbar__back"
          onClick={onBack}
          aria-label={backLabel ? `Back to ${backLabel}` : "Back"}
        >
          <ChevronLeft size={13} aria-hidden />
          <span className="ws-docbar__name">{name}</span>
        </button>
      ) : (
        <>
          {path && <span className="ws-docbar__path">{path}</span>}
          <span className="ws-docbar__name">{name}</span>
        </>
      )}

      {/* A 6px dot is too small to carry a word, so it carries a name. */}
      {dirty && <span className="ws-docbar__dot" role="img" aria-label="Unsaved changes" />}

      {meta && <span className="ws-docbar__meta">{meta}</span>}

      {children && <span className="ws-docbar__actions">{children}</span>}
    </div>
  );
}
