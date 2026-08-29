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
// A NOTE WEARS THIS BAR TOO, in the `title` variant (2026-08-27,
// memory-page-and-file-viewer-2026-08-27.md D7). The 2026-08-22 design kept a
// note on the record shape with an `ActionBar` on the grounds that a note has
// no filename and this bar would print its title twice in mono. That was an
// argument against the MONO FACE, not against the shared bar: `variant="title"`
// draws the name in the sans face at prose weight, which is what a title is,
// and the `path` slot carries the note's scope instead of a directory. The
// operator asked for one viewer and one editor across notes and files on
// 2026-08-27, and once the face is right there is nothing else a note needed.
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

/** Whether the thing this bar names is a FILE (a path, so mono) or a RECORD
 *  (a title, so sans at prose weight). The only difference is the face and the
 *  weight; every control, every state and the whole geometry are shared. */
export type DocumentBarVariant = "file" | "title";

export interface DocumentBarProps {
  /** The file's own name, or the record's title. Mono in the `file` variant,
   *  because a filename is not prose; sans in `title`, because a title is. */
  name: string;
  /** Which face the name is drawn in. Defaults to `file`. */
  variant?: DocumentBarVariant;
  /** Editing the name in place. When given, the name becomes an input rather
   *  than a span, which is the one thing a title needs that a filename does not:
   *  a note has no rename route anywhere else, so this bar is where its title is
   *  changed (D7, and the wikilink rewrite in D11 is what makes that safe). */
  onNameChange?: (next: string) => void;
  /** What an EMPTY name reads as. A PLACEHOLDER, so it is the input's
   *  placeholder and the span's text when there is no name yet, and never the
   *  input's VALUE: passing it as the value is how a name field ends up
   *  refilling itself the moment you clear it, which is a field you cannot
   *  empty. Reported 2026-08-28 on a new note whose title read "Untitled note"
   *  and would not go away. */
  namePlaceholder?: string;
  /** The name is FINISHED being edited: blur, or Enter. Given for a name whose
   *  write is a real operation rather than a field in a form that will be saved
   *  later -- a FILE rename moves bytes on disk and repoints every ref that
   *  named the old path, so doing it per keystroke would be one move and one
   *  ref rewrite per letter. A note's title needs none of this: it is part of
   *  the note's own draft and is written by the note's own Save. */
  onNameCommit?: () => void;
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
  name, variant = "file", onNameChange, onNameCommit, namePlaceholder,
  path, dirty = false, onBack, backLabel, meta, children, className,
}: DocumentBarProps) {
  const nameEl = onNameChange ? (
    // Sized to its own content so the bar's layout does not jump as you type,
    // and so a short title does not sit in a wide empty box. `size` is the
    // cheapest way to do that without measuring in JS.
    <input
      className="ws-docbar__name ws-docbar__nameinput"
      value={name}
      placeholder={namePlaceholder}
      size={Math.max(8, Math.min(60, (name || namePlaceholder || "").length + 1))}
      onChange={(e) => onNameChange(e.target.value)}
      onBlur={onNameCommit}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
        // Escape gives the name back rather than committing a half-typed one.
        // Blur fires after, so the commit sees the restored value and no-ops.
        if (e.key === "Escape") { e.currentTarget.blur(); }
      }}
      aria-label={variant === "title" ? "Title" : "Filename"}
    />
  ) : (
    <span className={cx("ws-docbar__name", !name && "ws-docbar__name--empty")}>
      {name || namePlaceholder}
    </span>
  );
  return (
    <div className={cx("ws-docbar", variant === "title" && "ws-docbar--title", className)}>
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
          {nameEl}
        </>
      )}

      {/* A 6px dot is too small to carry a word, so it carries a name. */}
      {dirty && <span className="ws-docbar__dot" role="img" aria-label="Unsaved changes" />}

      {meta && <span className="ws-docbar__meta">{meta}</span>}

      {children && <span className="ws-docbar__actions">{children}</span>}
    </div>
  );
}
