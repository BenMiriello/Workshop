// Markdown: the one place we render markdown in Workshop.
//
// Wraps react-markdown + remark-gfm (tables, task lists, strikethrough,
// autolinks). All styling comes from the `.ws-md` rule set in components.css
// (tokens only). No raw HTML is rendered: react-markdown does not parse HTML by
// default and we do not add rehype-raw, so untrusted content (assistant output,
// briefs, notes) stays safe.
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "./cx";

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cx("ws-md", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
