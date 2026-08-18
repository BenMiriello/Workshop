import { describe, it, expect } from "vitest";
import {
  admit, attachmentKind, canSendWith, formatSize, rejectionNotice, sendableRefs,
  MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES,
  type Attachment,
} from "./attachments";

const cand = (name: string, size = 10, type = "") => ({ name, size, type });

const att = (over: Partial<Attachment> = {}): Attachment => ({
  localId: over.localId ?? "l1",
  id: over.id,
  name: over.name ?? "a.png",
  size: over.size ?? 10,
  kind: over.kind ?? "image",
  status: over.status ?? "ready",
  error: over.error,
});

describe("attachmentKind", () => {
  it("reads a MIME type first, which is all a pasted screenshot has", () => {
    expect(attachmentKind("image/png", "")).toBe("image");
    expect(attachmentKind("image/webp", "image.png")).toBe("image");
    expect(attachmentKind("application/pdf", "")).toBe("pdf");
  });

  it("falls back to the extension, which is all a file-manager drag has", () => {
    expect(attachmentKind("", "shot.JPG")).toBe("image");
    expect(attachmentKind("", "report.pdf")).toBe("pdf");
    expect(attachmentKind("", "notes.md")).toBe("text");
    expect(attachmentKind("", "server.py")).toBe("text");
  });

  it("prefers a known extension over a text/* MIME, so a browser drag of markup does not become a file", () => {
    expect(attachmentKind("text/html", "diagram.png")).toBe("image");
  });

  it("recognises extensionless names that carry their type whole", () => {
    expect(attachmentKind("", "Dockerfile")).toBe("text");
    expect(attachmentKind("", "Makefile")).toBe("text");
  });

  it("accepts a convertible binary as text: the server derives its rendering", () => {
    expect(attachmentKind("application/zip", "bundle.zip")).toBe("text");
    expect(attachmentKind("", "report.docx")).toBe("text");
    expect(attachmentKind("", "book.xlsx")).toBe("text");
    expect(attachmentKind("", "deck.pptx")).toBe("text");
    expect(attachmentKind("", "arc.tar")).toBe("text");
  });

  it("refuses what a model could not read, rather than attaching it and failing later", () => {
    expect(attachmentKind("", "clip.mp4")).toBeNull();
    expect(attachmentKind("", "binary")).toBeNull();
    expect(attachmentKind("", "app.exe")).toBeNull();
  });
});

describe("admit", () => {
  it("accepts a plain file and reports its kind", () => {
    const { accepted, rejected } = admit([], [cand("a.png", 100, "image/png")]);
    expect(accepted).toHaveLength(1);
    expect(accepted[0].kind).toBe("image");
    expect(rejected).toEqual([]);
  });

  it("refuses an unsupported type with a reason, not silently", () => {
    const { accepted, rejected } = admit([], [cand("clip.mp4", 5, "video/mp4")]);
    expect(accepted).toEqual([]);
    expect(rejected[0].reason).toBe("unsupported file type");
  });

  it("refuses an oversize file and an empty one", () => {
    const { rejected } = admit([], [
      cand("big.png", MAX_ATTACHMENT_BYTES + 1, "image/png"),
      cand("zero.png", 0, "image/png"),
    ]);
    expect(rejected.map((r) => r.reason)).toEqual([
      `larger than ${formatSize(MAX_ATTACHMENT_BYTES)}`,
      "empty file",
    ]);
  });

  it("treats same name AND size as already attached, so a double drop does not double the list", () => {
    const existing = [att({ name: "a.png", size: 100 })];
    const { accepted, rejected } = admit(existing, [cand("a.png", 100, "image/png")]);
    expect(accepted).toEqual([]);
    expect(rejected[0].reason).toBe("already attached");
  });

  it("lets the same name through at a different size: it is a different file", () => {
    const existing = [att({ name: "a.png", size: 100 })];
    const { accepted } = admit(existing, [cand("a.png", 200, "image/png")]);
    expect(accepted).toHaveLength(1);
  });

  it("dedupes WITHIN one drop, not just against what is already attached", () => {
    const { accepted, rejected } = admit([], [
      cand("a.png", 100, "image/png"),
      cand("a.png", 100, "image/png"),
    ]);
    expect(accepted).toHaveLength(1);
    expect(rejected[0].reason).toBe("already attached");
  });

  it("counts the existing list against the cap, so the cap is a total and not a per-drop limit", () => {
    const existing = Array.from({ length: MAX_ATTACHMENTS - 1 }, (_, i) =>
      att({ localId: `l${i}`, name: `f${i}.png`, size: i + 1 }));
    const { accepted, rejected } = admit(existing, [
      cand("new1.png", 900, "image/png"),
      cand("new2.png", 901, "image/png"),
    ]);
    expect(accepted).toHaveLength(1);
    expect(rejected[0].reason).toBe(`over the limit of ${MAX_ATTACHMENTS}`);
  });
});

describe("canSendWith", () => {
  it("allows text alone, as it always did", () => {
    expect(canSendWith("hello", [])).toBe(true);
  });

  it("allows a file with no text: dropping a screenshot and pressing send is a message", () => {
    expect(canSendWith("", [att({ status: "ready" })])).toBe(true);
  });

  it("blocks while an upload is still going, so a turn cannot reference bytes the server lacks", () => {
    expect(canSendWith("hello", [att({ status: "uploading" })])).toBe(false);
  });

  it("blocks an empty send", () => {
    expect(canSendWith("   ", [])).toBe(false);
  });

  it("does not treat a failed upload as something to send", () => {
    expect(canSendWith("", [att({ status: "failed" })])).toBe(false);
  });

  it("still sends the text when an attachment failed: the message is not held hostage", () => {
    expect(canSendWith("hello", [att({ status: "failed" })])).toBe(true);
  });
});

describe("sendableRefs", () => {
  it("carries only what actually reached the server", () => {
    const refs = sendableRefs([
      att({ localId: "a", id: "srv-1", name: "one.png", status: "ready" }),
      att({ localId: "b", status: "uploading" }),
      att({ localId: "c", status: "failed" }),
      att({ localId: "d", id: undefined, status: "ready" }),
    ]);
    expect(refs).toEqual([{ id: "srv-1", name: "one.png", kind: "image" }]);
  });
});

describe("formatSize", () => {
  it("scales without pretending to a precision it does not have", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(2048)).toBe("2 KB");
    expect(formatSize(1024 * 1024 * 3.5)).toBe("3.5 MB");
  });
});

describe("rejectionNotice", () => {
  it("is silent when nothing was refused", () => {
    expect(rejectionNotice([])).toBeNull();
  });

  it("groups by reason, so twelve refused zips are one sentence and not twelve", () => {
    const note = rejectionNotice([
      { name: "a.zip", reason: "unsupported file type" },
      { name: "b.zip", reason: "unsupported file type" },
      { name: "c.zip", reason: "unsupported file type" },
      { name: "d.zip", reason: "unsupported file type" },
    ]);
    expect(note).toBe("Not attached. a.zip, b.zip, c.zip and 1 more: unsupported file type.");
  });

  it("keeps distinct reasons distinct", () => {
    const note = rejectionNotice([
      { name: "a.zip", reason: "unsupported file type" },
      { name: "b.png", reason: "empty file" },
    ]);
    expect(note).toBe("Not attached. a.zip: unsupported file type. b.png: empty file.");
  });
});
