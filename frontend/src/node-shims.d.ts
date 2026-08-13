// Minimal ambient declarations for the two node APIs the CSS contract tests use.
//
// Those tests read a stylesheet as text and assert on the rules in it. Both
// vitest configs stub `.css` imports to an empty string (including with `?raw`),
// which makes an assertion like `expect(css).not.toContain(...)` pass vacuously,
// so reading the file is the only honest way to do it. `tsc -b` compiles the
// test files and this repo carries no `@types/node`, so the two functions are
// declared here rather than pulling the whole node global surface into an app
// that runs in a browser.
declare module "node:fs" {
  export function readFileSync(path: string | URL, encoding: "utf8"): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string | URL): string;
}
