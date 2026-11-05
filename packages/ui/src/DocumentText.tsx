// A document's text as it reads, for the places it is only shown: an earlier
// version in the history, another writer's version beside yours. The same
// editor and page look as the document itself, read-only, so a version reads
// exactly as the document did.
import { Editor } from "./EditorCore";
import type { EditorMarks } from "./editorMarks";

export function DocumentText({ text, marks, onLink, label }: {
  text: string;
  marks?: EditorMarks;
  onLink?: (target: string) => void;
  label?: string;
}) {
  return (
    <Editor value={text} editable={false} path="document.md" profile="prose" look="page"
      ariaLabel={label} marks={marks} onLink={onLink} />
  );
}
