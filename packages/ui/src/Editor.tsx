// Editor: the one text and code editor in Workshop, on CodeMirror 6.
//
// Adopted in docs/platform-redesign/file-viewer-and-editor-2026-08-22.md section 3,
// which records why (markdown AND source, light AND dark across eight palettes,
// web AND the mobile WebView, and a theme that consumes CSS custom properties
// instead of fighting them) and why not Monaco or Tiptap. Do not re-litigate the
// pick here.
//
// It is a SUBPATH export, @workshop/ui/editor, with the CodeMirror packages as
// OPTIONAL peer dependencies, exactly like @workshop/ui/map does with maplibre-gl
// and @workshop/ui/markdown does with react-markdown. The kit's core entry gains
// no dependency and a consumer that never opens a file never pays for one.
//
// ## The two axes (section 4)
//
// `profile` is which extensions load; `editable` is a fact about whether the
// path can be written. They are INDEPENDENT. A read-only source file is the
// `code` profile with `editable={false}`, not a third mode, and it must not be
// drawn as a disabled control: design rule 10 forbids the refusal cursor, and a
// file you may not write is not a broken text box.
//
// `editable={false}` removes `contenteditable` (EditorView.editable) as well as
// refusing modification (EditorState.readOnly). The first is what matters on a
// phone: a contenteditable element opens the virtual keyboard on tap even when
// every edit would be rejected, so refusing edits alone gives you a keyboard
// covering half the screen and nothing to type into.
//
// ## Why the theme is CSS variables and not a color object
//
// The app is eight palettes x light/dark, switched by two attributes on <html>.
// A theme built from `var(--bg-inset)` follows an attribute change with no
// reconfiguration and no teardown, so switching palette costs nothing here.
// EditorView.theme() and HighlightStyle.define() both take plain CSS strings,
// which is what makes that possible. It is also required: check_design.sh fails
// the build on a raw hex in a .tsx file, and Map.tsx is the only exemption.
//
// The nine syntax roles are generated tokens (`--syn-*`), authored in
// src/styles/palette.mjs across all sixteen scheme x modes and contrast-checked
// there like every other text tone. Never write a color into this file.
import { useEffect, useRef } from "react";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  placeholder as cmPlaceholder,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import {
  HighlightStyle,
  LanguageDescription,
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { tags as t } from "@lezer/highlight";
import { cx } from "./cx";
import { languageForPath, type EditorProfile } from "./editorProfile";

// Re-exported so a consumer can ask what a path is before it renders anything
// (which tab to show, whether the file even has a rendered form), without a
// second copy of the table.
export { languageForPath, type EditorProfile, type PathProfile, type PathLanguage } from "./editorProfile";

/* ---- the surface -----------------------------------------------------------
   Tokens only. `.cm-*` class names are stable and non-generated, which is the
   property that lets a theme target them at all; the generated `.ͼ*` class
   CodeMirror puts on the editor is what scopes these rules to this theme, and
   a theme outranks the library's base theme by construction, so nothing here
   needs an `!important` or a specificity trick.

   color-mix() rather than a `-soft` token for selections and matches: a `-soft`
   fill is a near-surface tint (it is the background of a chip), so it is nearly
   invisible behind text on an already-recessed panel in light mode. A mix with
   the surface is legible in both modes from one declaration. */
const THEME = EditorView.theme({
  "&": {
    color: "var(--fg)",
    backgroundColor: "var(--bg-inset)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-md)",
    // The editor's type size lives HERE, on the editor element, rather than in
    // the .ws-editor CSS, for two reasons. First, `&` is the one ancestor that
    // both .cm-content and .cm-gutters inherit from, and .cm-scroller sets the
    // line-height for both, so one declaration scales text and line numbers by
    // the same factor and they cannot drift out of alignment -- putting it on
    // .cm-content instead would move the code and leave the gutter behind.
    // Second, the container rule in components.css exists only to make `height`
    // reach the editor; the SURFACE is themed from tokens in this object, and
    // splitting one property out of it would put the editor's appearance in two
    // places.
    // --fs-field-code is --fs-sm (13px) on a fine pointer and 1rem on a coarse
    // one, which is what keeps a tap on a phone from zooming the page in (see
    // "THE 16px FLOOR" in components.css). 16px of mono is also simply a better
    // read on a phone.
    fontSize: "var(--fs-field-code)",
  },
  "&.cm-focused": {
    outline: "none",
    borderColor: "var(--accent)",
    boxShadow: "var(--focus-ring)",
  },
  ".cm-scroller": {
    fontFamily: "var(--font-mono)",
    lineHeight: "var(--lh-snug)",
    overflow: "auto",
    borderRadius: "inherit",
  },
  ".cm-content": {
    padding: "var(--space-3) 0",
    caretColor: "var(--accent)",
  },
  ".cm-line": { padding: "0 var(--space-3)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
    backgroundColor: "color-mix(in srgb, var(--accent) 28%, transparent)",
  },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--warn) 22%, transparent)" },
  ".cm-searchMatch": {
    backgroundColor: "color-mix(in srgb, var(--warn) 26%, transparent)",
    outline: "1px solid var(--warn)",
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "color-mix(in srgb, var(--accent) 34%, transparent)",
  },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--fg) 5%, transparent)" },
  ".cm-gutters": {
    backgroundColor: "var(--bg-inset)",
    color: "var(--faint)",
    borderRight: "1px solid var(--border)",
    borderTopLeftRadius: "inherit",
    borderBottomLeftRadius: "inherit",
    fontFamily: "var(--font-mono)",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "color-mix(in srgb, var(--fg) 5%, transparent)",
    color: "var(--muted)",
  },
  ".cm-foldGutter .cm-gutterElement": { color: "var(--faint)" },
  ".cm-foldPlaceholder": {
    backgroundColor: "var(--bg-block)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    color: "var(--muted)",
    padding: "0 var(--space-1)",
  },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
    backgroundColor: "color-mix(in srgb, var(--accent) 22%, transparent)",
    outline: "none",
  },
  ".cm-nonmatchingBracket, &.cm-focused .cm-nonmatchingBracket": {
    backgroundColor: "color-mix(in srgb, var(--danger) 22%, transparent)",
    outline: "none",
  },
  ".cm-specialChar": { color: "var(--danger)" },
  ".cm-placeholder": { color: "var(--faint)" },
  ".cm-panels": { backgroundColor: "var(--card)", color: "var(--fg)", border: "none" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--border)" },
  ".cm-panel": { fontFamily: "var(--font-sans)", fontSize: "var(--fs-xs)", padding: "var(--space-2)" },
  ".cm-panel label": { color: "var(--muted)" },
  ".cm-panel input, .cm-panel button, .cm-panel select": {
    fontFamily: "inherit",
    fontSize: "inherit",
    color: "var(--fg)",
    backgroundColor: "var(--field-bg)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-sm)",
    padding: "2px var(--space-2)",
  },
  ".cm-panel button:hover": { backgroundColor: "var(--card-hover)" },
  ".cm-tooltip": {
    backgroundColor: "var(--card)",
    color: "var(--fg)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-md)",
    boxShadow: "var(--shadow-md)",
  },
  ".cm-tooltip .cm-tooltip-arrow:after": {
    borderTopColor: "var(--card)",
    borderBottomColor: "var(--card)",
  },
});

/* ---- the nine roles --------------------------------------------------------
   Lezer's tag set is a hierarchy, so a rule on a parent covers its children:
   `comment` catches lineComment/blockComment/docComment, `keyword` catches
   controlKeyword and the rest of that family, `string` catches docString and
   character, `punctuation` catches every bracket. That is why nine roles can
   cover a grammar with thirty tags without thirty rules.

   Plain `variableName` deliberately has NO rule: an identifier is the body text
   of a source file, and painting all of it is how a theme ends up saying
   nothing. It reads as --fg, like the prose it is.

   The markdown rules at the end add NO tenth color. A heading is the keyword
   tone at heavier weight, a link is the property tone, and emphasis is the
   font's own italic. That is highlighting, not the live-preview decoration
   parked in section 8.1 of the design doc: it colors the source, it does not
   replace it with its rendered form. */
const SYNTAX = HighlightStyle.define([
  { tag: t.comment, color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: [t.string, t.regexp, t.escape, t.url], color: "var(--syn-string)" },
  { tag: t.keyword, color: "var(--syn-keyword)" },
  { tag: [t.number, t.bool], color: "var(--syn-number)" },
  {
    tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName)), t.macroName],
    color: "var(--syn-function)",
  },
  { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], color: "var(--syn-type)" },
  { tag: t.propertyName, color: "var(--syn-property)" },
  { tag: [t.punctuation, t.operator, t.meta], color: "var(--syn-punctuation)" },
  { tag: t.invalid, color: "var(--syn-invalid)" },

  // Markdown structure, in the roles above.
  { tag: t.heading, color: "var(--syn-keyword)", fontWeight: "var(--fw-semi)" },
  { tag: t.strong, fontWeight: "var(--fw-bold)" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: t.link, color: "var(--syn-property)" },
  { tag: t.monospace, color: "var(--syn-string)" },
  { tag: t.quote, color: "var(--syn-comment)" },
  { tag: [t.list, t.contentSeparator], color: "var(--syn-punctuation)" },
]);

/** Everything every profile has: selection drawing, the theme, the roles. */
const BASE: Extension = [
  highlightSpecialChars(),
  drawSelection(),
  EditorState.allowMultipleSelections.of(true),
  syntaxHighlighting(SYNTAX),
  THEME,
];

/* Section 4.1's table, as extensions. Three profiles, no fourth.

   Tab is NOT bound to indent. `indentWithTab` is standard in CodeMirror demos
   and it makes the editor a keyboard trap: once focused, Tab can never leave.
   Design rule 10 is WCAG AA, and 2.1.2 is explicit about this. `indentOnInput`
   plus the newline auto-indent covers the actual need. */
// What `editable` means to CodeMirror. Two switches, not one: `readOnly`
// refuses modification, while `editable.of(false)` removes contenteditable
// altogether so a phone does not open its keyboard for a file you are only
// reading. With contenteditable gone the content is no longer focusable, so a
// keyboard user could not scroll a long file; a plain tabindex restores that
// without bringing the keyboard back, because a phone opens the keyboard for
// an editable element, not for a focusable one.
function editExtensions(canEdit: boolean): Extension {
  return [
    EditorView.editable.of(canEdit),
    EditorState.readOnly.of(!canEdit),
    ...(canEdit ? [] : [EditorView.contentAttributes.of({ tabindex: "0" })]),
  ];
}

function profileExtensions(
  profile: EditorProfile,
  ariaLabel?: string,
  placeholder?: string,
): Extension {
  const attrs: Record<string, string> = {
    // A memory document is prose somebody wrote; a source file is not, and a
    // red squiggle under every identifier is noise.
    spellcheck: profile === "prose" ? "true" : "false",
    autocapitalize: "off",
    autocorrect: profile === "prose" ? "on" : "off",
  };
  if (ariaLabel) attrs["aria-label"] = ariaLabel;
  const content = EditorView.contentAttributes.of(attrs);
  // WHAT AN EMPTY EDITOR LOOKS LIKE, and why it needs saying. An empty editor
  // in a full-width document has no border on its long edges and a background a
  // shade off the page's, so on 2026-08-28 a new note's body was an editor the
  // size of the screen that read as blank page: measured 1128x513 and invisible.
  // A placeholder is the fix that also answers the more useful question, which
  // is what to write there. `view` gets none: a reader is not about to type.
  const hint = placeholder && profile !== "view" ? [cmPlaceholder(placeholder)] : [];

  // `view`: the profile's highlighting and nothing that edits. Wrapped, because
  // a reader should never have to scroll sideways to finish a line.
  if (profile === "view") return [content, EditorView.lineWrapping];

  // `prose`: soft wrap and history. No line numbers, no folding, no bracket
  // matching, and no active-line highlight either. A memory document is read as
  // a document, and a running bar down the line you are on is code furniture.
  if (profile === "prose") {
    return [content, ...hint, EditorView.lineWrapping, history(), keymap.of([...defaultKeymap, ...historyKeymap])];
  }

  return [
    content,
    ...hint,
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    foldGutter(),
    bracketMatching(),
    indentOnInput(),
    history(),
    search({ top: true }),
    highlightSelectionMatches(),
    keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, ...foldKeymap]),
  ];
}

export interface EditorProps {
  /** The document. Replacing it from outside replaces the text in place. */
  value: string;
  /** The whole document after every edit. Omit it for a viewer. */
  onChange?: (value: string) => void;
  /** Selects the language and, unless `profile` says otherwise, the profile. */
  path?: string;
  /** Overrides what the path would pick. `view` is only ever explicit. */
  profile?: EditorProfile;
  /** False removes `contenteditable`: no virtual keyboard opens on a phone. */
  editable?: boolean;
  /** A fixed height. Omit it and the editor grows with its content. */
  height?: number | string;
  /** What an EMPTY editor says. Not decoration: an empty editor in a
   *  full-width document has no side border and a background a shade off the
   *  page's, so without one it reads as blank page rather than as somewhere to
   *  type. Ignored by the `view` profile, which is not for typing in. */
  placeholder?: string;
  className?: string;
  /** The accessible name. An editor is a text box and needs one. */
  ariaLabel?: string;
}

export function Editor({
  value,
  onChange,
  path = "",
  profile,
  editable = true,
  height,
  className,
  ariaLabel,
  placeholder,
}: EditorProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  // What the editor's document currently is, as far as this component knows.
  // The `value` effect compares against THIS rather than against the previous
  // prop, so a caller who echoes onChange straight back into value does not
  // reset the document (and the cursor with it) on every keystroke.
  const docRef = useRef(value);
  // True while WE are writing the document, so the update listener does not
  // report the caller's own value back to them as an edit.
  const echoRef = useRef(false);

  // Compartments, one set per instance: they are identity-keyed, so a shared
  // one would reconfigure every editor on the page. Reconfiguring is also the
  // whole reason they exist. Rebuilding the view to change a profile or a
  // palette would drop the selection, the scroll position and the undo history,
  // which is exactly what a theme switch must not cost.
  const cellsRef = useRef<{ shape: Compartment; lang: Compartment; edit: Compartment } | null>(null);
  if (!cellsRef.current) {
    cellsRef.current = { shape: new Compartment(), lang: new Compartment(), edit: new Compartment() };
  }
  const { shape, lang, edit } = cellsRef.current;

  const resolved = languageForPath(path);
  // The LANGUAGE always comes from the path; the PROFILE is what a caller may
  // override. Forcing `prose` on a .py file gets you Python highlighting
  // without the code furniture, which is what asking for that means.
  const language = resolved.language;
  const active: EditorProfile = profile ?? resolved.profile;
  const canEdit = editable && active !== "view";
  // Read once at mount to seed the compartments; every later change goes
  // through the reconfiguring effects below.
  const activeRef = useRef(active);
  const ariaLabelRef = useRef(ariaLabel);
  const placeholderRef = useRef(placeholder);
  placeholderRef.current = placeholder;
  const canEditRef = useRef(canEdit);

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: docRef.current,
        extensions: [
          BASE,
          // Seeded with the real values rather than empty. The effects below
          // reconfigure these on every later change, but they are passive
          // (useEffect runs after paint), so seeding empty would paint one
          // frame of a read-only editor as contenteditable -- which on a phone
          // is a frame that can open the virtual keyboard.
          shape.of(profileExtensions(activeRef.current, ariaLabelRef.current, placeholderRef.current)),
          lang.of([]),
          edit.of(editExtensions(canEditRef.current)),
          EditorView.updateListener.of((u) => {
            if (!u.docChanged) return;
            const next = u.state.doc.toString();
            docRef.current = next;
            if (echoRef.current) return;
            onChangeRef.current?.(next);
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Mount once. Everything that can change lives in a compartment, seeded
    // above with its value at mount and reconfigured by the effects below.
  }, [shape, lang, edit]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: shape.reconfigure(profileExtensions(active, ariaLabel, placeholder)),
    });
  }, [active, ariaLabel, placeholder, shape]);

  useEffect(() => {
    viewRef.current?.dispatch({ effects: edit.reconfigure(editExtensions(canEdit)) });
  }, [canEdit, edit]);

  useEffect(() => {
    let alive = true;
    const apply = (ext: Extension) => {
      if (alive) viewRef.current?.dispatch({ effects: lang.reconfigure(ext) });
    };
    if (!language) {
      apply([]);
    } else if (language === "Markdown") {
      // Straight from @codemirror/lang-markdown rather than through
      // language-data, for `codeLanguages`: a fenced block inside a memory
      // document gets its own grammar, lazily, instead of reading as one string.
      apply(markdown({ base: markdownLanguage, codeLanguages: languages }));
    } else {
      const desc = LanguageDescription.matchLanguageName(languages, language, true);
      if (!desc) apply([]);
      else if (desc.support) apply(desc.support);
      // A grammar is a lazy chunk, so it can fail to arrive (offline, a bad
      // deploy). Plain text is the honest fallback: the file still opens and is
      // still editable, it just is not colored.
      else desc.load().then(apply, () => apply([]));
    }
    return () => {
      alive = false;
    };
  }, [language, lang]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || value === docRef.current) return;
    docRef.current = value;
    echoRef.current = true;
    try {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    } finally {
      echoRef.current = false;
    }
  }, [value]);

  return <div ref={hostRef} className={cx("ws-editor", className)} style={{ height }} />;
}
