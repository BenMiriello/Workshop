// ATTACHING A FILE TO A TURN, as rules rather than as handlers.
//
// The composer, the drop target and the paste handler all funnel into the same
// two functions here (`admit` and `attachmentKind`), so a file dropped on the
// transcript is judged identically to one picked from the file dialog. That is
// the whole reason this is a module and not three copies of an `if` inside
// three event handlers.
//
// An attachment is a REFERENCE, not text spliced into the draft. The operator
// asked for one delete, not a backspace per character, and a chip you remove in
// one click is only possible if the file never became part of the draft string.

/** What the chip shows and what the turn carries. */
export type Attachment = {
  /** Stable from the moment the file is picked, so a chip keyed by it never
   *  remounts when the upload finishes and the server id arrives. */
  localId: string;
  /** The server's id for the stored file. Absent until the upload lands. */
  id?: string;
  name: string;
  size: number;
  kind: AttachmentKind;
  status: "uploading" | "ready" | "failed";
  /** Why an upload failed, shown on the chip. */
  error?: string;
};

export type AttachmentKind = "image" | "pdf" | "text";

/** What a turn sends to the server: just the ids it already uploaded. */
export type AttachmentRef = { id: string; name: string; kind: AttachmentKind };

export const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
export const MAX_ATTACHMENTS = 10;

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);

// Extensions we are confident a model can read as text. Deliberately a list and
// not "anything without a known-binary extension": silently attaching a .zip
// produces a file the agent opens and cannot use, which is worse than saying no.
const TEXT_EXT = new Set([
  "txt", "md", "markdown", "rst", "log", "csv", "tsv",
  "json", "jsonl", "ndjson", "yaml", "yml", "toml", "ini", "cfg", "conf", "env", "properties",
  "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "rb", "go", "rs", "java", "kt", "swift",
  "c", "h", "cc", "cpp", "hpp", "cs", "php", "lua", "pl", "r", "scala", "clj", "ex", "exs",
  "sh", "bash", "zsh", "fish", "ps1", "sql", "graphql", "gql", "proto",
  "html", "htm", "xml", "svg", "css", "scss", "sass", "less",
  "diff", "patch", "gitignore", "dockerfile", "makefile", "lock",
]);

function ext(name: string): string {
  const base = name.slice(name.lastIndexOf("/") + 1).toLowerCase();
  const dot = base.lastIndexOf(".");
  // "Dockerfile", "Makefile" carry their type in the whole name, not a suffix.
  if (dot <= 0) return base;
  return base.slice(dot + 1);
}

/**
 * The kind we will attach this as, or null if we will not attach it.
 * MIME first (a paste from the clipboard has a type and no useful name), then
 * the extension (a drag from a file manager often has an empty type).
 */
export function attachmentKind(mime: string, name: string): AttachmentKind | null {
  const m = (mime || "").toLowerCase();
  if (m.startsWith("image/")) return "image";
  if (m === "application/pdf") return "pdf";

  const e = ext(name);
  if (IMAGE_EXT.has(e)) return "image";
  if (e === "pdf") return "pdf";
  if (TEXT_EXT.has(e)) return "text";

  // A text MIME is trusted last: `text/html` on a drag from a browser is the
  // dragged markup, not a file, so an extension we recognise wins over it.
  if (m.startsWith("text/") || m === "application/json" || m === "application/xml") return "text";
  return null;
}

/** Human size for the chip. Whole numbers below a megabyte: "412 KB" reads
 *  better than "412.0 KB" and the precision is meaningless at that scale. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type Candidate = { name: string; size: number; type: string };
export type Rejection = { name: string; reason: string };

/**
 * Decide which of `files` join `existing`. Pure so every entry point (button,
 * drop, paste) gets the same answer, and so the count/size/duplicate rules are
 * testable without a DOM.
 *
 * Returns the indices of the accepted candidates rather than the candidates
 * themselves, because the caller holds the real `File` objects and only this
 * function knows which survived.
 */
export function admit<T extends Candidate>(
  existing: Attachment[], files: T[],
): { accepted: { file: T; kind: AttachmentKind }[]; rejected: Rejection[] } {
  const accepted: { file: T; kind: AttachmentKind }[] = [];
  const rejected: Rejection[] = [];
  // Same name AND size twice is the same file: dropping a folder's worth of
  // screenshots twice should not double the list.
  const seen = new Set(existing.map((a) => `${a.name}:${a.size}`));
  let room = MAX_ATTACHMENTS - existing.length;

  for (const f of files) {
    const kind = attachmentKind(f.type, f.name);
    if (!kind) {
      rejected.push({ name: f.name, reason: "unsupported file type" });
      continue;
    }
    if (f.size > MAX_ATTACHMENT_BYTES) {
      rejected.push({ name: f.name, reason: `larger than ${formatSize(MAX_ATTACHMENT_BYTES)}` });
      continue;
    }
    if (f.size === 0) {
      rejected.push({ name: f.name, reason: "empty file" });
      continue;
    }
    const key = `${f.name}:${f.size}`;
    if (seen.has(key)) {
      rejected.push({ name: f.name, reason: "already attached" });
      continue;
    }
    if (room <= 0) {
      rejected.push({ name: f.name, reason: `over the limit of ${MAX_ATTACHMENTS}` });
      continue;
    }
    seen.add(key);
    room -= 1;
    accepted.push({ file: f, kind });
  }
  return { accepted, rejected };
}

/**
 * A turn may be attachments with no text, but never a half-finished upload:
 * sending mid-upload would file a turn referring to a file the server does not
 * have yet. A failed attachment does not block the send, it is simply not sent.
 */
export function canSendWith(text: string, attachments: Attachment[]): boolean {
  if (attachments.some((a) => a.status === "uploading")) return false;
  if (text.trim()) return true;
  return attachments.some((a) => a.status === "ready");
}

/** The refs a send carries: the ones that actually made it to the server. */
export function sendableRefs(attachments: Attachment[]): AttachmentRef[] {
  const out: AttachmentRef[] = [];
  for (const a of attachments) {
    if (a.status === "ready" && a.id) out.push({ id: a.id, name: a.name, kind: a.kind });
  }
  return out;
}

/** One line naming what was refused, for the composer's notice. Grouped by
 *  reason so dropping twelve `.zip`s says one thing rather than twelve. */
export function rejectionNotice(rejected: Rejection[]): string | null {
  if (!rejected.length) return null;
  const byReason = new Map<string, string[]>();
  for (const r of rejected) {
    const list = byReason.get(r.reason) ?? [];
    list.push(r.name);
    byReason.set(r.reason, list);
  }
  const parts: string[] = [];
  for (const [reason, names] of byReason) {
    const shown = names.length > 3 ? `${names.slice(0, 3).join(", ")} and ${names.length - 3} more` : names.join(", ");
    parts.push(`${shown}: ${reason}`);
  }
  return `Not attached. ${parts.join(". ")}.`;
}
