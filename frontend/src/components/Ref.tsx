// The one chip for a `workshop:<kind>/...` ref (memory-and-documentation-
// model-2026-08-14.md sec 10): a lucide icon per kind, the resolved title,
// click navigates to the in-app route. An unresolvable ref (refused by the
// backend resolver -- a deleted note, a bad path) renders as plain text,
// never as a link that goes nowhere.
//
// Reads as a link WITHOUT hover (the operator is often on a phone): a faint
// underline at rest, same rule as `.ws-md a` in the kit and `.chat-receipt--
// link` in styles.css, not a hover-only affordance.
//
// A `file` ref is the exception (Layer 3 of managed-file-references-and-path-
// scoping-2026-08-18): instead of navigating, the chip opens a preview panel
// with the file's thumbnail/icon and the three managed actions -- Open, Copy
// URL (the in-app link), Copy path (the managed address, for every user). No
// raw OS path is ever shown.
import { Link } from "react-router-dom";
import {
  FileText, BookOpen, Ticket as TicketIcon, MessageSquare, Play,
  FolderGit2, Bot, Blocks, Paperclip, ExternalLink,
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
  if (resolved.kind === "file") {
    return <FileRef resolved={resolved} refStr={refStr} Icon={Icon} />;
  }
  return (
    <Link className="ws-ref ws-ref--link" to={resolved.href}>
      <Icon size={13} strokeWidth={1.8} aria-hidden />
      <span>{resolved.title}</span>
    </Link>
  );
}

function FileRef({
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

  const trigger = (
    <button type="button" className="ws-ref ws-ref--file" title={resolved.title}>
      <Icon size={13} strokeWidth={1.8} aria-hidden />
      <span>{resolved.title}</span>
    </button>
  );

  return (
    <Popover trigger={trigger} label={resolved.title} side="bottom" className="ws-ref-pop">
      <div className="ws-ref-card">
        <div className="ws-ref-card__preview">
          {p.thumb_url ? (
            <img className="ws-ref-card__thumb" src={p.thumb_url} alt="" />
          ) : (
            <Icon size={28} strokeWidth={1.4} aria-hidden />
          )}
        </div>
        <div className="ws-ref-card__info">
          <div className="ws-ref-card__title">{resolved.title}</div>
          {meta ? <div className="ws-ref-card__meta">{meta}</div> : null}
        </div>
      </div>
      <div className="ws-ref-card__actions">
        <a
          className="ws-ref-action"
          href={resolved.href}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink size={14} strokeWidth={1.8} aria-hidden />
          <span>Open</span>
        </a>
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
    </Popover>
  );
}
