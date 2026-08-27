// Markdown: the one place we render markdown in Workshop.
//
// Wraps react-markdown + remark-gfm (tables, task lists, strikethrough,
// autolinks). All styling comes from the `.ws-md` rule set in components.css
// (tokens only). No raw HTML is rendered: react-markdown does not parse HTML by
// default and we do not add rehype-raw, so untrusted content (assistant output,
// briefs, notes) stays safe.
//
// ## Why this is memoized
//
// Parsing markdown is the single most expensive thing the kit does, and callers
// render it once per message in a list that re-renders for unrelated reasons.
// In the chat, the composer's draft text is state on the same component that
// owns the transcript, so every keystroke re-rendered every message; measured on
// a real 67-message / 310 KB thread, one pass of remark parse + mdast->hast is
// ~150 ms on a desktop core, so 600-900 ms on a phone, per keystroke, and it
// grows linearly with the thread. Typing became visibly unusable on mobile.
//
// `memo` fixes it at the source rather than at each call site: both props are
// strings and a given message's text does not change between renders, so the
// shallow compare skips the parse entirely. Keep the plugin array hoisted — a
// fresh `[remarkGfm]` literal per render is a new prop identity every time,
// which would defeat both this memo and react-markdown's own internal caching.
//
// ## Diagrams
//
// A ```mermaid fence renders as a diagram, everywhere markdown is rendered and
// with no per-surface configuration. That is one hoisted `components.pre`
// override (`MARKDOWN_COMPONENTS` below) plus `MermaidDiagram`, which loads the
// mermaid library lazily and falls back to the code block when a diagram will
// not parse. Read `MermaidDiagram.tsx`'s header before changing any of it.
import { memo, useMemo } from "react";
import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "./cx";
import { MermaidDiagram } from "./MermaidDiagram";
import { mermaidSourceOf } from "./mermaidFence";

const REMARK_PLUGINS = [remarkGfm];

/** The one place a fenced block is inspected. `mermaidSourceOf` reads the hast
 *  node react-markdown hands every component and answers whether this `pre` is
 *  a diagram; everything else is drawn as the code block it always was. */
function MarkdownPre({ node, children, ...rest }: JSX.IntrinsicElements["pre"] & ExtraProps) {
  const diagram = mermaidSourceOf(node);
  if (diagram !== null) return <MermaidDiagram code={diagram} />;
  return <pre {...rest}>{children}</pre>;
}

// Hoisted for the same reason `REMARK_PLUGINS` is: a fresh object here would be
// a new prop identity on every render.
const MARKDOWN_COMPONENTS: Components = { pre: MarkdownPre };

// `components` and `urlTransform` are the two react-markdown knobs a consumer
// needs to render an in-app link (e.g. Workshop's `workshop:<kind>/...` refs) as
// something other than a bare `<a>`. Both are optional; the default rendering is
// unchanged when they are absent. IMPORTANT for the memo below: pass a HOISTED
// (module-constant) object for either, never a fresh literal per render, or the
// shallow compare fails and every keystroke re-parses the markdown again (the
// exact regression this memo exists to prevent). A custom link renderer that
// needs per-message data should read it from React context, not close over it,
// so its `components` object can stay a constant.
type MarkdownProps = {
  children: string;
  className?: string;
  components?: Components;
  // react-markdown strips any URL scheme it does not recognise (http/https/
  // mailto/tel/relative) down to empty, which would erase a `workshop:` link
  // before a custom renderer ever sees it. A consumer passes a transform that
  // preserves its own scheme.
  urlTransform?: (url: string) => string;
};

export const Markdown = memo(function Markdown(
  { children, className, components, urlTransform }: MarkdownProps,
) {
  // The kit's own overrides, with the caller's on top so a consumer can still
  // replace any of them. `useMemo` keeps the merged object stable: it is a new
  // identity only when `components` itself changed, which is already the case
  // the memo above lets through.
  const merged = useMemo(
    () => (components ? { ...MARKDOWN_COMPONENTS, ...components } : MARKDOWN_COMPONENTS),
    [components],
  );
  return (
    <div className={cx("ws-md", className)}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={merged}
        urlTransform={urlTransform}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
