// The one chip for a `workshop:<kind>/...` ref (memory-and-documentation-
// model-2026-08-14.md sec 10): a lucide icon per kind, the resolved title,
// click navigates to the in-app route. An unresolvable ref (refused by the
// backend resolver -- a deleted note, a bad path) renders as plain text,
// never as a link that goes nowhere.
//
// Reads as a link WITHOUT hover (the operator is often on a phone): a faint
// underline at rest, same rule as `.ws-md a` in the kit and `.chat-receipt--
// link` in styles.css, not a hover-only affordance.
import { Link } from "react-router-dom";
import {
  FileText, BookOpen, Ticket as TicketIcon, MessageSquare, Play,
  FolderGit2, Bot, Blocks,
} from "lucide-react";
import type { ResolvedRef, RefKind } from "../lib/refs";

const REF_ICON: Record<RefKind, typeof FileText> = {
  note: FileText,
  doc: BookOpen,
  ticket: TicketIcon,
  chat: MessageSquare,
  run: Play,
  project: FolderGit2,
  agent: Bot,
  app: Blocks,
};

export function Ref({ resolved }: { resolved: ResolvedRef | null | undefined }) {
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
  return (
    <Link className="ws-ref ws-ref--link" to={resolved.href}>
      <Icon size={13} strokeWidth={1.8} aria-hidden />
      <span>{resolved.title}</span>
    </Link>
  );
}
