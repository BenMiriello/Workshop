// The optional `mermaid` package, declared here instead of resolved from
// node_modules.
//
// mermaid is an OPTIONAL peer of this kit (package.json) and an optional
// dependency of the app that consumes it: an installation that never draws a
// diagram should not carry a megabyte of diagram library, so it installs with
// `--omit=optional` and the package is simply not on disk. `tsc` does not know
// that is allowed — with the package missing, every reference to the specifier
// is TS2307 and the typecheck fails, which fails the build (`tsc -b && vite
// build`) for exactly the installations that opted out.
//
// An ambient module declaration is matched before node_modules resolution, so
// this is the type of `import("mermaid")` in BOTH configurations. That is the
// point: the code compiles identically whether or not the package is there,
// and the difference is confined to what the bundler resolves the specifier to
// (see mermaidOptional.ts and the app's vite config).
//
// This is a CONTRACT with mermaid >= 11, not a copy of its API. It carries only
// the surface MermaidDiagram.tsx uses; widen it in the same change as the call
// that needs it.
declare module "mermaid" {
  export interface MermaidConfig {
    startOnLoad?: boolean;
    fontFamily?: string;
    securityLevel?: "strict" | "loose" | "antiscript" | "sandbox";
    htmlLabels?: boolean;
    flowchart?: { htmlLabels?: boolean };
    suppressErrorRendering?: boolean;
    theme?: "base" | "default" | "dark" | "forest" | "neutral" | "null";
    themeVariables?: Record<string, string>;
  }

  export interface Mermaid {
    /** Replaces the one global config. Called before every draw so a theme
     *  switch is picked up. */
    initialize(config: MermaidConfig): void;
    /** Draws `text` into a scratch element addressed by `#id` and returns the
     *  sanitised SVG. Rejects when the diagram does not parse. */
    render(id: string, text: string): Promise<{ svg: string }>;
  }

  const mermaid: Mermaid;
  export default mermaid;
}
