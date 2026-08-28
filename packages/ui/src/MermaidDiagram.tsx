// A ```mermaid fence, drawn as a diagram. Reached only through `Markdown`, so
// every surface that renders markdown gets diagrams with no per-surface wiring.
//
// ---- WHY THE LIBRARY IS LOADED LATE ---------------------------------------
//
// mermaid pulls in d3, dagre, cytoscape and katex; it is around a megabyte
// gzipped, which is several times the whole Workshop bundle. A static import
// would put all of it in the main chunk for a feature most pages never reach.
// So the ONLY reference to it is the `import("mermaid")` inside `loadMermaid`,
// which the bundler turns into its own lazily fetched chunk. Do not hoist that
// into a top-level import: nothing would fail, the app would just get a
// megabyte heavier on every first load, and no test would notice.
//
// The promise is module-scoped, so a page with twelve diagrams downloads and
// initialises the library once.
//
// ---- WHY THE PACKAGE MIGHT NOT BE THERE AT ALL ----------------------------
//
// mermaid is an OPTIONAL dependency, so an installation that does not want a
// megabyte of diagram library omits it and `import("mermaid")` resolves to
// `mermaidOptional.ts` instead (the consuming app's build decides; see that
// file and `frontend/vite.config.ts`). Nothing here branches on it: the stand-in
// rejects with `MermaidUnavailableError`, which lands in the same catch as a
// diagram that would not draw, and the fence renders as a code block. Do not
// add an "is it installed" check — the point of the substitution is that this
// component has one path.
//
// ---- WHY THERE IS AN innerHTML HERE, AND WHY IT IS NOT RAW DOCUMENT HTML ---
//
// `Markdown` refuses to render HTML written in the document (no rehype-raw),
// and that is unchanged: nothing an author puts between the fences reaches the
// DOM as markup. What is injected below is the SVG that MERMAID produced from
// the diagram source, after mermaid ran it through DOMPurify (it sanitises for
// every security level except `loose`). `securityLevel: "strict"` and
// `htmlLabels: false` are what keep that true, so do not relax either: loose
// mode disables the sanitiser and html labels put author text into a
// `foreignObject` as markup.
//
// ---- WHY A FAILURE SHOWS THE SOURCE ---------------------------------------
//
// Assistant output is written in one pass and is often half a diagram, and a
// diagram streams in token by token, so a fence that does not parse is the
// normal case rather than the exceptional one. Every state other than "drawn"
// renders the fence as the code block it would have been, which means the
// reader can always read what the author wrote. `suppressErrorRendering` is on
// so mermaid raises the parse error instead of drawing its own error graphic
// into the page, and so it cleans up its temporary element on the way out.
// `diagramNotice` decides whether the reason is worth printing beside it: a
// broken diagram is, a library this installation never installed is not.
import { useEffect, useId, useRef, useState } from "react";
import type { MermaidConfig } from "mermaid";
import { Notice } from "./feedback";
import { diagramNotice } from "./mermaidOptional";

/** Appearance tokens the diagram is painted from. Only tokens whose value is a
 *  literal colour are listed: `getComputedStyle().getPropertyValue()` returns a
 *  custom property's SPECIFIED value, so an alias like `--accent: var(--you)`
 *  would come back as the text "var(--you)" and mermaid cannot read that. */
const THEME_TOKENS = {
  background: "--bg-block",
  mainBkg: "--bg-inset",
  primaryColor: "--bg-inset",
  primaryTextColor: "--fg",
  primaryBorderColor: "--border-strong",
  secondaryColor: "--card",
  secondaryTextColor: "--fg",
  secondaryBorderColor: "--border",
  tertiaryColor: "--bg-sunken",
  tertiaryTextColor: "--fg",
  tertiaryBorderColor: "--border",
  nodeBorder: "--border-strong",
  nodeTextColor: "--fg",
  clusterBkg: "--bg-block",
  clusterBorder: "--border",
  lineColor: "--muted",
  textColor: "--fg",
  titleColor: "--fg",
  edgeLabelBackground: "--bg-block",
  noteBkgColor: "--bg-inset",
  noteTextColor: "--fg",
  noteBorderColor: "--border",
  fontFamily: "--font-sans",
} as const;

function themeVariables(): Record<string, string> {
  const style = getComputedStyle(document.documentElement);
  const out: Record<string, string> = {};
  for (const [key, token] of Object.entries(THEME_TOKENS)) {
    const value = style.getPropertyValue(token).trim();
    // An unresolved alias or a missing token is left out rather than passed on:
    // mermaid derives shades from these with a colour library, and handing it
    // "var(...)" or "" throws inside the theme rather than at the call site.
    if (value && !value.includes("var(")) out[key] = value;
  }
  return out;
}

function baseConfig(): MermaidConfig {
  const variables = themeVariables();
  return {
    startOnLoad: false,
    // Also at the top level, not only in `themeVariables`. mermaid reads
    // `config.fontFamily` when it sets up the element it draws into, and
    // leaving it unset renders every label in the browser's default serif
    // regardless of what the theme variables say.
    fontFamily: variables.fontFamily,
    // Untrusted content: assistant output, briefs, notes. Strict keeps
    // mermaid's DOMPurify pass on and refuses click handlers in the source.
    securityLevel: "strict",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
    // Mermaid draws its own "Syntax error" graphic and leaks its scratch
    // element when this is off. With it on, a bad diagram is an exception this
    // component catches and reports beside the source.
    suppressErrorRendering: true,
    theme: "base",
    themeVariables: variables,
  };
}

let loading: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid(): Promise<typeof import("mermaid").default> {
  if (!loading) {
    loading = import("mermaid").then((mod) => mod.default);
  }
  return loading;
}

/** Bumps whenever the app's appearance changes. The colours are baked into the
 *  SVG string at render time, so a diagram already on screen has to be drawn
 *  again when the operator switches theme or palette in Settings. Both are
 *  attributes on `<html>`; the OS setting matters too while the mode is
 *  `system`, where the attribute does not change at all. */
function useAppearanceKey(): number {
  const [key, setKey] = useState(0);
  useEffect(() => {
    const bump = () => setKey((n) => n + 1);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "data-palette"],
    });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", bump);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", bump);
    };
  }, []);
  return key;
}

/** How long the source has to stop changing before it is worth drawing. A
 *  diagram arriving over SSE changes on every token, and each of those is a
 *  parse plus a layout pass; without this a streaming reply would run mermaid
 *  a hundred times to draw one picture. Short enough to read as immediate. */
const SETTLE_MS = 120;

export function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const appearance = useAppearanceKey();
  // Mermaid addresses its scratch element by `#id`, so the id has to be a valid
  // CSS identifier. React's useId is unique per instance but contains colons.
  const domId = `ws-mermaid-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  // Nothing here writes to the DOM directly; this only exists so the settle
  // timer can be cleared without re-running the effect.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    timer.current = setTimeout(() => {
      loadMermaid()
        .then((mermaid) => {
          // Re-initialised per draw so a theme switch is picked up. mermaid
          // holds one global config; this is the supported way to change it.
          mermaid.initialize(baseConfig());
          return mermaid.render(domId, code);
        })
        .then((result) => {
          if (live) setSvg(result.svg);
        })
        .catch((e: unknown) => {
          if (!live) return;
          setSvg(null);
          setError(diagramNotice(e));
        });
    }, SETTLE_MS);
    return () => {
      live = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [code, domId, appearance]);

  if (svg) {
    return (
      // The `<svg>` inside carries mermaid's own accessibility info (a role
      // description, plus the diagram's title and description when the source
      // declares them), so the wrapper stays a plain box and does not shadow it
      // with a second, unlabelled role.
      <div
        className="ws-mermaid"
        // See the header: mermaid's own sanitised output, not document HTML.
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    );
  }

  // Not drawn: the fence is still the content, so it stays on screen as the
  // code block it would otherwise have been, and the reason sits beside it.
  return (
    <>
      {error && (
        <Notice tone="warn" detail={error}>
          This diagram could not be drawn.
        </Notice>
      )}
      <pre>
        <code>{code}</code>
      </pre>
    </>
  );
}
