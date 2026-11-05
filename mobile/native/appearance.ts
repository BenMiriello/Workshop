// What sits behind the phone's status bar is the shell's top inset, so the shell
// paints that inset in the page's own background and sets the bar's text to
// contrast with it. Both then follow the web UI's theme (light, dark, or system)
// and its palette, read from what the page actually renders rather than from a
// setting the shell would have to mirror.
//
// The watcher is installed beside the bridge stub (inject.ts), on the top
// document only. It reports when the document loads, when <html>'s attributes
// change (Settings writes data-theme and data-palette there), and when the
// system scheme flips under the "system" theme.

const KIND = "ws-appearance";

export interface PageAppearance {
  /** The page background as the engine computes it, e.g. "rgb(16, 13, 17)". */
  bg: string;
  /** Whether light text reads better than dark text over `bg`. */
  dark: boolean;
}

export function buildAppearanceWatch(): string {
  // NOTE: kept ES5-ish on purpose, like the bridge stub.
  return `(function(){
    var last="";
    function linear(c){
      c=c/255;
      return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4);
    }
    function report(){
      var root=document.documentElement;
      var post=window.ReactNativeWebView && window.ReactNativeWebView.postMessage;
      if (!root || !post) return;
      var bg=getComputedStyle(root).backgroundColor;
      var m=/^rgba?\\(([^)]+)\\)$/.exec(bg || "");
      if (!m) return;
      var p=m[1].split(/[\\s,\\/]+/).map(parseFloat);
      if (p.length < 3 || p[3] === 0) return;
      var lum=0.2126*linear(p[0]) + 0.7152*linear(p[1]) + 0.0722*linear(p[2]);
      var msg=JSON.stringify({ t: ${JSON.stringify(KIND)}, bg: bg, dark: lum < 0.179 });
      if (msg === last) return;
      last=msg;
      window.ReactNativeWebView.postMessage(msg);
    }
    function watch(){
      new MutationObserver(report).observe(document.documentElement, { attributes: true });
      report();
    }
    if (document.documentElement) watch();
    else document.addEventListener("DOMContentLoaded", watch);
    window.addEventListener("load", report);
    window.addEventListener("pageshow", report);
    if (window.matchMedia) {
      var mq=window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", report);
    }
  })();
  true;`;
}

/** The watcher's report, or null for any other message from the page. */
export function parseAppearance(raw: string): PageAppearance | null {
  let msg: unknown;
  try {
    msg = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!msg || typeof msg !== "object") return null;
  const { t, bg, dark } = msg as { t?: unknown; bg?: unknown; dark?: unknown };
  if (t !== KIND || typeof bg !== "string" || typeof dark !== "boolean") return null;
  return { bg, dark };
}
