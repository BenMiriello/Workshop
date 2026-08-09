import { useCallback, useState } from "react";
import { Check, Copy } from "lucide-react";

// Copy one message's raw text.
//
// Not the `CopyButton` primitive: that one renders the value beside the button
// (right for a URL or an id in Settings), and a whole chat message is not a
// value you display twice. This is the icon alone.
//
// It is positioned by .chat-copy, absolutely, in the bubble's bottom-right. That
// is one mechanism covering both cases the operator asked for: with no expand
// control it sits in the corner of the last text row, and when a Show more/less
// row exists that row IS the bottom row, so the button lands at its far right
// without the two needing to know about each other.
export default function CopyMessage({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    const done = () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1100);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(value).then(done, done);
    else done();
  }, [value]);
  return (
    <button
      type="button"
      className="chat-copy"
      onClick={copy}
      aria-label={copied ? "Copied" : "Copy message"}
      title={copied ? "Copied" : "Copy message"}
    >
      {copied
        ? <Check size={13} strokeWidth={1.8} aria-hidden />
        : <Copy size={13} strokeWidth={1.8} aria-hidden />}
    </button>
  );
}
