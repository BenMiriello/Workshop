// The one table that maps a file path to a language and an editor profile.
//
// This is the rule from docs/platform-redesign/file-viewer-and-editor-2026-08-22.md
// section 4.1, and it is a PURE function on purpose: "full code features for
// source, lighter for memories" is which extensions load, not which code runs,
// so the difference between a memory document and a source file is one lookup
// rather than a second editor. Adding a language later is a row here, not a
// design.
//
// It is separate from Editor.tsx because the repo has no DOM render harness and
// none is planned (frontend/CLAUDE.md: "the target is the PURE RULE behind a
// behaviour, extracted into its own module so it can be executed"). Everything
// decidable without a browser is decided here and tested in editorProfile.test.ts.
//
// `language` is a @codemirror/language-data NAME, not a CodeMirror extension, so
// this module pulls in no CodeMirror packages at all. Editor.tsx resolves the
// name against `languages` and loads the grammar lazily. `null` means plain
// text: the file still opens, with no highlighting.

/** Which extensions an editor loads for a file. Section 4.1 defines three, and
 *  there is no fourth:
 *
 *  - `prose`   markdown, soft wrap, history. No line numbers, no folding.
 *  - `code`    language by extension, line numbers, folding, bracket matching,
 *              indent, search, history.
 *  - `view`    the language's highlighting only, and nothing that edits.
 *
 *  `prose` and `code` come from the PATH. `view` never does: it is a caller's
 *  choice about chrome, the way `editable` is a fact about the path's
 *  writability. The two axes are independent, so a read-only source file is
 *  `code` with `editable={false}`, not `view`. */
export type EditorProfile = "prose" | "code" | "view";

/** The profile a path can select on its own. `view` is not one of them. */
export type PathProfile = Exclude<EditorProfile, "view">;

export interface PathLanguage {
  /** A @codemirror/language-data language name, or null for plain text. */
  language: string | null;
  profile: PathProfile;
}

const prose = (language: string | null): PathLanguage => ({ language, profile: "prose" });
const code = (language: string | null): PathLanguage => ({ language, profile: "code" });

/** An extension nothing claims. The file still opens; it just has no grammar. */
const PLAIN: PathLanguage = code(null);

/* The table. Extension (lowercased, no dot) -> language name + profile.
 *
 * `.txt` is prose with NO language: soft wrap and no line numbers are right for
 * it, and running the markdown grammar over a plain text file would invent
 * headings out of any line that starts with a hash. */
const BY_EXTENSION: Record<string, PathLanguage> = {
  md: prose("Markdown"),
  markdown: prose("Markdown"),
  mdown: prose("Markdown"),
  mkd: prose("Markdown"),
  txt: prose(null),
  text: prose(null),

  py: code("Python"),
  pyi: code("Python"),
  pyx: code("Cython"),
  ts: code("TypeScript"),
  mts: code("TypeScript"),
  cts: code("TypeScript"),
  tsx: code("TSX"),
  js: code("JavaScript"),
  mjs: code("JavaScript"),
  cjs: code("JavaScript"),
  jsx: code("JSX"),
  json: code("JSON"),
  jsonc: code("JSON"),
  vue: code("Vue"),

  css: code("CSS"),
  scss: code("SCSS"),
  sass: code("Sass"),
  less: code("LESS"),
  html: code("HTML"),
  htm: code("HTML"),
  xml: code("XML"),
  svg: code("XML"),

  yml: code("YAML"),
  yaml: code("YAML"),
  toml: code("TOML"),
  ini: code("Properties files"),
  cfg: code("Properties files"),
  properties: code("Properties files"),
  env: code("Properties files"),

  sh: code("Shell"),
  bash: code("Shell"),
  zsh: code("Shell"),
  ps1: code("PowerShell"),
  sql: code("SQL"),
  diff: code("diff"),
  patch: code("diff"),

  go: code("Go"),
  rs: code("Rust"),
  rb: code("Ruby"),
  java: code("Java"),
  kt: code("Kotlin"),
  kts: code("Kotlin"),
  swift: code("Swift"),
  scala: code("Scala"),
  c: code("C"),
  h: code("C"),
  cc: code("C++"),
  cpp: code("C++"),
  cxx: code("C++"),
  hpp: code("C++"),
  cs: code("C#"),
  php: code("PHP"),
  lua: code("Lua"),
  pl: code("Perl"),
  r: code("R"),
  dart: code("Dart"),
  erl: code("Erlang"),
  hs: code("Haskell"),
  clj: code("Clojure"),
  wat: code("WebAssembly"),
};

/* Files whose NAME is the type. A dotfile has no extension to look up, and
 * neither does a Makefile, so the whole basename is the key. */
const BY_FILENAME: Record<string, PathLanguage> = {
  dockerfile: code("Dockerfile"),
  containerfile: code("Dockerfile"),
  makefile: code(null),
  gnumakefile: code(null),
  ".env": code("Properties files"),
  ".gitignore": code(null),
  ".dockerignore": code(null),
  ".gitattributes": code(null),
  ".editorconfig": code("Properties files"),
  ".npmrc": code("Properties files"),
  license: prose(null),
  readme: prose(null),
};

/** The last segment of a path. Accepts "", a bare filename, a relative path and
 *  an absolute one, and never throws on any of them. */
function basename(path: string): string {
  const parts = path.split("/");
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i]) return parts[i];
  }
  return "";
}

/** Own-property lookup. A file really can be called `constructor` or
 *  `toString`, and a bare `table[key]` would answer those out of Object's
 *  prototype and hand back a function where a PathLanguage belongs. */
function row(table: Record<string, PathLanguage>, key: string): PathLanguage | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

/** The language and profile for one path. Never throws: an extension nothing
 *  claims is plain text in the `code` profile, which is the honest answer for a
 *  file whose source is its only form. */
export function languageForPath(path: string): PathLanguage {
  const name = basename(path).toLowerCase();
  const byName = row(BY_FILENAME, name);
  if (byName) return byName;

  // A dotfile's only dot is at index 0, and that is a NAME, not an extension:
  // ".gitignore" must not be read as a file of type "gitignore". Requiring the
  // dot to sit past the first character is the whole rule.
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return PLAIN;

  return row(BY_EXTENSION, name.slice(dot + 1)) ?? PLAIN;
}
