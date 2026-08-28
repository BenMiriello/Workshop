// What a ```mermaid fence does in an installation that did not install mermaid.
//
// The library is an optional dependency: it is ~84 MB on disk and ~3.4 MB of
// lazily-fetched chunks in a build, for a feature many installations never
// reach, so `npm install --omit=optional` leaves it out entirely. That has to
// be a supported configuration rather than a broken one, and with a bundler the
// hard part is that `import("mermaid")` fails at BUILD time when the package is
// absent — nothing reaches runtime to fall back.
//
// So the consuming app's build resolves the specifier to THIS module when the
// package is not on disk (`frontend/vite.config.ts`), and the fence renders as
// the code block it was before diagrams existed:
//
//   import("mermaid")  ->  mermaidOptional.ts  ->  MermaidUnavailableError
//                          (a few bytes, no diagram library in the graph)
//
// Nothing here is imported by that path when mermaid IS installed; the alias is
// not applied, `import("mermaid")` is the real library, and the only part of
// this module in play is `diagramNotice`.

/** Thrown when the diagram library is not part of this installation. Distinct
 *  from a parse failure because the two read completely differently to a
 *  reader: a diagram that does not parse is worth a warning beside the source,
 *  while a diagram library that was never installed is not the author's
 *  business and says nothing about their fence. */
export class MermaidUnavailableError extends Error {
  constructor() {
    super("Diagrams are not available: this installation does not include mermaid.");
    this.name = "MermaidUnavailableError";
  }
}

/** The stand-in for the library's default export. Every entry point rejects, so
 *  the failure arrives at the same `catch` that already handles a diagram that
 *  would not draw — there is no second failure path to keep working.
 *
 *  Deliberately not typed as mermaid's own `Mermaid`: this object is never
 *  passed to mermaid, it REPLACES it, and claiming the full interface would
 *  mean growing a fake for every method the library adds. */
const mermaidNotInstalled = {
  initialize(): void {
    throw new MermaidUnavailableError();
  },
  render(): Promise<{ svg: string }> {
    return Promise.reject(new MermaidUnavailableError());
  },
};

export default mermaidNotInstalled;

/** The text to show beside a fence that was not drawn, or `null` for "show the
 *  fence and say nothing". The source is on screen either way. */
export function diagramNotice(error: unknown): string | null {
  if (error instanceof MermaidUnavailableError) return null;
  if (error instanceof Error) return error.message;
  return String(error);
}
