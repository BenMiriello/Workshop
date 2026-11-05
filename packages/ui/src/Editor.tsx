// The `@workshop/ui/editor` subpath: the editor, and the document frame built on
// it. The editor itself is EditorCore.tsx; this entry exists so the frame can
// import the editor without the two modules importing each other.
export * from "./EditorCore";
export {
  matchTrigger,
  type EditorCompletionItem,
  type EditorCompletionSource,
  type EditorHighlight,
  type EditorHighlightTone,
  type EditorSelection,
} from "./editorAssist";
export type { EditorConceal } from "./editorConceal";
export type { EditorMark, EditorMarks } from "./editorMarks";
export { DocumentText } from "./DocumentText";
export {
  VersionedDocument,
  type DocumentDraft,
  type DocumentState,
  type DocumentSaveResult,
  type DocumentSource,
  type DocumentVersionInfo,
  type DocumentWriteMeta,
  type VersionedDocumentProps,
} from "./VersionedDocument";
