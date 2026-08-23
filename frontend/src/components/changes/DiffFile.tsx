import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Badge } from "@workshop/ui";

import {
  DIFF_LINE_CAP, clipDiff, diffLineKind, fileStat, fileStatusLabel,
  startsExpanded, type ChangeFileDiff,
} from "../../lib/changeReview";

// One file's patch, collapsible.
//
// EVERY NUMBER ON THIS ROW IS COUNTED OFF THE PATCH, never read from a field
// somebody wrote. That is the whole posture of the review surface
// (changes-and-review-2026-08-22.md sec 5): the diff is the evidence and the
// prose is a claim about it.
//
// A LARGE FILE MUST NOT BLOW UP THE PAGE, and two rules keep that true. The
// patch starts collapsed unless the whole change is small enough that opening
// everything is what a reader wanted (`startsExpanded`), and the body draws at
// most DIFF_LINE_CAP lines and then says how many it did not draw. Neither is a
// scroll trick: a 12,000-line lockfile diff mounted as 12,000 spans is a frozen
// tab, and lockfiles are exactly what a dependency change contains.
//
// The rules live in lib/changeReview.ts and are tested there.

export default function DiffFile({ file, fileCount }: { file: ChangeFileDiff; fileCount: number }) {
  const stat = fileStat(file.diff);
  const [open, setOpen] = useState(() => startsExpanded(fileCount, stat));
  const clipped = clipDiff(file.diff);
  const lines = clipped.text ? clipped.text.split("\n") : [];

  return (
    <div className="ws-diff__file">
      <button
        type="button"
        className="ws-diff__head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight
          size={14}
          aria-hidden
          className={"ws-diff__caret" + (open ? " is-open" : "")}
        />
        <span className="ws-diff__path">{file.path}</span>
        <Badge>{fileStatusLabel(file.status)}</Badge>
        {stat.binary ? (
          <span className="ws-diff__stat ws-muted">binary</span>
        ) : (
          <span className="ws-diff__stat">
            <span className="ws-diff__plus">+{stat.added}</span>
            <span className="ws-diff__minus">-{stat.removed}</span>
          </span>
        )}
      </button>

      {open && (
        stat.binary || lines.length === 0 ? (
          <p className="ws-diff__note">
            {stat.binary
              ? "Binary file. Git records that it changed and nothing more, so there is no patch to read."
              : "Git produced no patch text for this path."}
          </p>
        ) : (
          <>
            <pre className="ws-diff__body">
              <code>
                {lines.map((line, i) => (
                  <span key={i} className={`ws-diff__line ws-diff__line--${diffLineKind(line)}`}>
                    {line || " "}
                  </span>
                ))}
              </code>
            </pre>
            {clipped.clipped > 0 && (
              <p className="ws-diff__note">
                {clipped.clipped} more lines in this patch, not drawn. The first
                {" "}{DIFF_LINE_CAP} are above; read the rest on the branch.
              </p>
            )}
          </>
        )
      )}
    </div>
  );
}
