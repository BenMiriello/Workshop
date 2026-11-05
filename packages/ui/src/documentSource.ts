// What a VersionedDocument needs from wherever the document lives. The host
// implements it; the frame never knows which app it is in.

export interface DocumentDraft {
  title: string;
  body: string;
  tags: string[];
}

export interface DocumentState extends DocumentDraft {
  /** The current version number. A save names it as its base. */
  version: number;
  updatedAt?: string | null;
  /** Where the current version was saved from, in the source's own words. */
  savedFrom?: string | null;
  /** The source refuses writes. The frame then has no Edit at all. */
  readOnly?: boolean;
}

export interface DocumentVersionInfo {
  version: number;
  savedAt: string;
  title: string;
  savedFrom?: string | null;
}

export type DocumentSaveResult =
  | { ok: true; state: DocumentState }
  | { ok: false; conflict: DocumentState };

/** Which device a write came from, so the source can keep one history entry
 *  per stretch of typing on one device, and name the device in history. */
export interface DocumentWriteMeta {
  device: { id: string; label: string };
}

export interface DocumentSource {
  load(): Promise<{ state: DocumentState }>;
  /** Make `draft` current, unless the document is no longer at `baseVersion`:
   *  then nothing is written and the current state comes back as a conflict. */
  save(draft: DocumentDraft, baseVersion: number, meta?: DocumentWriteMeta): Promise<DocumentSaveResult>;
  versions(): Promise<DocumentVersionInfo[]>;
  version(version: number): Promise<DocumentDraft>;
  restore(version: number, meta?: DocumentWriteMeta): Promise<DocumentState>;
}
