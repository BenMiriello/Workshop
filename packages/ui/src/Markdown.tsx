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
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "./cx";

const REMARK_PLUGINS = [remarkGfm];

export const Markdown = memo(function Markdown(
  { children, className }: { children: string; className?: string },
) {
  return (
    <div className={cx("ws-md", className)}>
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{children}</ReactMarkdown>
    </div>
  );
});
