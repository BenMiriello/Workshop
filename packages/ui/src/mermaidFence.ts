// The pure rule behind a rendered diagram: given the `pre` element
// react-markdown is about to draw, is this a ```mermaid fence, and what is the
// source inside it?
//
// WHY `pre` AND NOT `code`. react-markdown v10 passes the hast node to every
// component (`passNode` is on unconditionally), and a fenced block arrives as
// `pre > code.language-mermaid`. Intercepting `code` would leave the diagram
// inside a `<pre>` that is styled as a scrolling code block, so the interception
// happens one level up and REPLACES the block. v10 also removed the `inline`
// prop that v8 passed to `code`, which is the other reason not to intercept
// there: an inline `` `mermaid` `` and a fenced one are told apart by the parent
// tag now, and that is exactly what this reads.
//
// Kept as a pure function over a structural shape rather than typed against
// `hast`, so it runs under vitest with no DOM and no markdown parser.

type TextNode = { type: "text"; value: string };
type ElementNode = {
  type: string;
  tagName?: string;
  properties?: { className?: unknown } | null;
  children?: unknown[];
};

function isElement(node: unknown, tagName: string): node is ElementNode {
  return (
    typeof node === "object" &&
    node !== null &&
    (node as ElementNode).type === "element" &&
    (node as ElementNode).tagName === tagName
  );
}

/** The class names on a hast element. hast stores `class` as an array, but a
 *  string survives from hand-built trees and from some rehype plugins, so both
 *  are read. */
function classNamesOf(node: ElementNode): string[] {
  const raw = node.properties?.className;
  if (Array.isArray(raw)) return raw.filter((c): c is string => typeof c === "string");
  if (typeof raw === "string") return raw.split(/\s+/).filter(Boolean);
  return [];
}

function textOf(children: unknown[] | undefined): string {
  let out = "";
  for (const child of children ?? []) {
    if (typeof child === "object" && child !== null && (child as TextNode).type === "text") {
      out += (child as TextNode).value ?? "";
    }
  }
  return out;
}

/** The diagram source inside a ```mermaid fence, or `null` for every other
 *  `pre` (which then renders as the code block it has always been).
 *
 *  An empty or whitespace-only fence is `null` too: there is no diagram to
 *  draw, and an empty code block is a truer rendering of it than a failed
 *  diagram would be. */
export function mermaidSourceOf(node: unknown): string | null {
  if (!isElement(node, "pre")) return null;
  const children = (node.children ?? []).filter(
    (c) => !(typeof c === "object" && c !== null && (c as TextNode).type === "text" && !(c as TextNode).value.trim()),
  );
  const [code] = children;
  if (children.length !== 1 || !isElement(code, "code")) return null;
  if (!classNamesOf(code).includes("language-mermaid")) return null;
  const source = textOf(code.children).replace(/\s+$/, "");
  return source.trim() ? source : null;
}
