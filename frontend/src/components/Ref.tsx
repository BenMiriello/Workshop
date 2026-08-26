// The one chip for a `workshop:<kind>/...` ref (memory-and-documentation-
// model-2026-08-14.md sec 10; managed-file-references-and-path-scoping-
// 2026-08-18 sec "Managed-link chip"): a lucide icon per kind, the resolved
// title, and a dropdown with a preview (thumbnail for a showable file, else
// the kind's icon) and three actions -- Open, Copy URL (the in-app link) and
// Copy path (the managed `workshop:<kind>/...` address, for every user, no
// developer-mode gate). EVERY kind gets this, not just `file`: a bare
// navigating link never showed the managed address at all, which was the
// point of the ticket. An unresolvable ref (refused by the backend resolver
// -- a deleted note, a bad path) renders as plain text, never as a link that
// goes nowhere and never as the raw ref string.
//
// The trigger reads as a link WITHOUT hover (the operator is often on a
// phone): a faint underline at rest, same rule as `.ws-md a` in the kit and
// `.chat-receipt--link` in styles.css, not a hover-only affordance.
import { Link } from "react-router-dom";
import {
  FileText, BookOpen, Ticket as TicketIcon, MessageSquare, Play,
  FolderGit2, Bot, Blocks, Paperclip, ChevronRight,
} from "lucide-react";
import { Popover, CopyButton } from "@workshop/ui";
import type { ResolvedRef, RefKind, RefPreview } from "../lib/refs";

const REF_ICON: Record<RefKind, typeof FileText> = {
  note: FileText,
  doc: BookOpen,
  ticket: TicketIcon,
  chat: MessageSquare,
  run: Play,
  project: FolderGit2,
  agent: Bot,
  app: Blocks,
  file: Paperclip,
};

function humanSize(n: number | null | undefined): string {
  if (n == null || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function Ref({
  resolved,
  refStr,
}: {
  resolved: ResolvedRef | null | undefined;
  // The original `workshop:...` string this chip was resolved from. A file ref
  // needs it so Copy path can hand back the managed address; other kinds do not.
  refStr?: string;
}) {
  // `undefined` = not resolved yet (the batch call is in flight); nothing
  // renders rather than flashing a false "unavailable". `null` = the backend
  // POSITIVELY refused it (a deleted note, a bad path) -- inert text, never a
  // link that might go nowhere, and never the raw ref string either, since
  // that would be exactly the file-path/URL leak sec 10 rules out.
  if (resolved === undefined) return null;
  if (resolved === null) {
    return <span className="ws-ref ws-ref--unresolved">Unavailable</span>;
  }
  const Icon = REF_ICON[resolved.kind] ?? FileText;
  return <RefCard resolved={resolved} refStr={refStr} Icon={Icon} />;
}

function RefCard({
  resolved,
  refStr,
  Icon,
}: {
  resolved: ResolvedRef;
  refStr?: string;
  Icon: typeof FileText;
}) {
  const p: RefPreview = resolved.preview ?? {};
  const meta = [p.mime, humanSize(p.size_bytes)].filter(Boolean).join(" · ");
  // The in-app URL, made absolute so Copy URL yields something shareable.
  const url =
    typeof window !== "undefined"
      ? `${window.location.origin}${resolved.href}`
      : resolved.href;
  // A `file` ref's href is a raw serve endpoint (/api/attachments/<id>), so
  // Open leaves the SPA; every other kind's href is an in-app route, so Open
  // navigates through the router instead of a full page load.
  const isFile = resolved.kind === "file";

  const trigger = (
    <button type="button" className="ws-ref ws-ref--trigger" title={resolved.title}>
      <Icon size={13} strokeWidth={1.8} aria-hidden />
      <span>{resolved.title}</span>
    </button>
  );

  // The whole header row IS the link: a small kind icon (no taller than the
  // title), the title, and a caret at the end that says it opens. No separate
  // "Open" action, no external-link glyph. A `file` opens in a new tab (its
  // href is a raw serve endpoint that leaves the SPA); every other kind
  // navigates through the router.
  const head = (
    <>
      <Icon size={15} strokeWidth={1.8} aria-hidden />
      <span className="ws-ref-card__title">{resolved.title}</span>
      <ChevronRight className="ws-ref-card__caret" size={15} strokeWidth={1.8} aria-hidden />
    </>
  );

  return (
    <Popover trigger={trigger} label={resolved.title} side="bottom">
      <div className="ws-ref-card">
        {isFile ? (
          <a
            className="ws-ref-card__open"
            href={resolved.href}
            target="_blank"
            rel="noopener noreferrer"
          >
            {head}
          </a>
        ) : (
          <Link className="ws-ref-card__open" to={resolved.href}>
            {head}
          </Link>
        )}
        {p.thumb_url ? <img className="ws-ref-card__thumb" src={p.thumb_url} alt="" /> : null}
        {meta ? <div className="ws-ref-card__meta">{meta}</div> : null}
        <div className="ws-ref-card__actions">
          <div className="ws-ref-row">
            <span className="ws-ref-row__label">URL</span>
            <CopyButton value={url} />
          </div>
          {refStr ? (
            <div className="ws-ref-row">
              <span className="ws-ref-row__label">Path</span>
              <CopyButton value={refStr} />
            </div>
          ) : null}
        </div>
      </div>
    </Popover>
  );
}
