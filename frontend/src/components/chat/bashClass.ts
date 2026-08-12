// IS THIS SHELL COMMAND JUST LOOKING AT SOMETHING?
//
// Grouping merges read-only steps and never merges a write or a command, and on
// this system that rule bought almost nothing: measured over 10,847 real steps,
// Bash is 64% of everything the assistant does and was excluded wholesale, so
// only 6.2% of rows ever collapsed. But most of that Bash is reading. `grep` is
// 16% of it, `sed -n` 11%, plus ls / cat / wc / find. Treating "Bash" as one
// opaque thing is what made the transcript a flat wall.
//
// So a shell command is classified by WHAT IT DOES, which is the same axis
// Codex's TUI uses for its "Explored" cell (read-only calls fold under one
// header; anything else stays a row). This is not a new hierarchy invented for
// this app. The only nesting the harness actually gives us is the subagent
// (`parent_tool_use_id`), and that is modelled separately as an `agent` segment.
//
// THE CLASSIFIER IS CONSERVATIVE AND MUST STAY THAT WAY. A false negative costs
// one extra row. A false positive hides a mutation inside a count, which is
// exactly the failure microsoft/vscode#292985 documents, where `rm -rf` showed
// as "Executed command in terminal". When in doubt it returns null.

/** What a read-only command is doing, mapped onto the same classes the native
 *  tools use so a shell `grep` and a `Grep` call merge into one row. They are
 *  the same act; only the spelling differs. */
export type BashKind = "read" | "grep" | "list" | "inspect";

const READ_HEADS = new Set([
  "ls", "cat", "head", "tail", "wc", "grep", "rg", "egrep", "fgrep", "find",
  "awk", "cut", "sort", "uniq", "tr", "pwd", "date", "stat", "file", "du",
  "df", "which", "basename", "dirname", "echo", "printf", "jq", "ps", "env",
  "tree", "comm", "column", "nl", "realpath", "true",
]);

// `git` is the one head where the SUBCOMMAND decides. `git status` reads;
// `git commit` does not, and neither does `git checkout`.
const GIT_READ = new Set([
  "status", "log", "diff", "show", "ls-files", "rev-parse", "describe",
  "blame", "remote",
]);

// Redirections that provably mutate nothing: a duplicated fd, or a write to the
// null device. Any OTHER `>` is a write to a file and disqualifies the command.
const SAFE_REDIRECTS = ["2>&1", "2>/dev/null", "2> /dev/null", ">/dev/null", "> /dev/null"];

/** Split on shell operators, ignoring any that sit inside quotes.
 *
 *  Quote awareness is not a nicety. `grep -n "a\|b" f | head` and
 *  `sed -n '1,5p;9,12p' f` are the two commonest shapes in this transcript, and
 *  a naive split on `|` or `;` tears both of them into fragments that classify
 *  as unknown, so the commands that most deserve merging were the ones rejected. */
export function splitShellOps(cmd: string): string[] {
  const out: string[] = [];
  let buf = "";
  let quote: string | null = null;
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i];
    if (quote) {
      buf += c;
      if (c === quote && cmd[i - 1] !== "\\") quote = null;
      continue;
    }
    if (c === "'" || c === '"') { quote = c; buf += c; continue; }
    if (c === "\\" && i + 1 < cmd.length) { buf += cmd[i] + cmd[i + 1]; i++; continue; }
    if (cmd.startsWith("&&", i) || cmd.startsWith("||", i)) { out.push(buf); buf = ""; i++; continue; }
    if (c === ";" || c === "|") { out.push(buf); buf = ""; continue; }
    buf += c;
  }
  out.push(buf);
  return out;
}

/** An unquoted `>`, `$(` or backtick: a write, or a substitution that could run
 *  anything. Either way, not classifiable as read-only. */
function writesOrSubstitutes(seg: string): boolean {
  let s = seg;
  for (const safe of SAFE_REDIRECTS) s = s.split(safe).join("");
  let quote: string | null = null;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quote) { if (c === quote && s[i - 1] !== "\\") quote = null; continue; }
    if (c === "'" || c === '"') { quote = c; continue; }
    if (c === ">" || c === "`") return true;
    if (s.startsWith("$(", i)) return true;
  }
  return false;
}

/** Bare-word tokens, with quoted runs kept whole. Enough to read a head and its
 *  flags; this is not a shell parser and does not pretend to be. */
function tokens(seg: string): string[] {
  const out: string[] = [];
  let buf = "";
  let quote: string | null = null;
  for (let i = 0; i < seg.length; i++) {
    const c = seg[i];
    if (quote) { if (c === quote && seg[i - 1] !== "\\") quote = null; else buf += c; continue; }
    if (c === "'" || c === '"') { quote = c; continue; }
    if (/\s/.test(c)) { if (buf) out.push(buf); buf = ""; continue; }
    buf += c;
  }
  if (buf) out.push(buf);
  return out;
}

/** The command a stage actually runs, past any prefix that only sets up for it.
 *  `timeout 10 cat f` is a read; naming it "timeout" files it under the wrong
 *  verb, and the read-only check already looks through the same wrappers, so
 *  the two must agree. */
function realHead(t: string[]): string | null {
  let i = 0;
  while (i < t.length) {
    const h = t[i];
    if (h === "cd") return null;                    // neutral, names nothing
    if (h === "time" || h === "nohup") { i += 1; continue; }
    if (h === "timeout") { i += 2; continue; }      // skips its duration too
    return h;
  }
  return null;
}

/** One pipeline stage. `cd` is neutral: it moves nowhere and writes nothing. */
function segmentIsReadOnly(seg: string): boolean {
  const s = seg.trim();
  if (!s) return true;
  if (writesOrSubstitutes(s)) return false;
  const t = tokens(s);
  if (!t.length) return true;
  const head = t[0];
  if (head === "cd") return true;
  // A wrapper is as read-only as what it wraps.
  if (head === "timeout" || head === "time" || head === "nohup") {
    const rest = head === "timeout" ? t.slice(2) : t.slice(1);
    return rest.length > 0 && segmentIsReadOnly(rest.join(" "));
  }
  // sed prints with -n and edits in place with -i. Only the printing form.
  if (head === "sed") return t.includes("-n") && !t.some((x) => x.startsWith("-i"));
  if (head === "git") {
    const sub = t.slice(1).find((x) => !x.startsWith("-")) ?? "";
    // `git branch -d` deletes; the bare form lists.
    if (sub === "branch") return !t.some((x) => ["-d", "-D", "-m", "-M"].includes(x));
    return GIT_READ.has(sub);
  }
  return READ_HEADS.has(head);
}

/** The class a shell command merges under, or null when it must stand on its
 *  own row. Null for anything this cannot prove is read-only. */
export function bashClass(cmd: string | undefined): BashKind | null {
  const c = (cmd || "").trim();
  if (!c) return null;
  // Control flow can do anything across an unbounded body, and a heredoc is
  // almost always writing a file. Neither is worth trying to read.
  if (/^(for|while|if|case|function)\b/.test(c)) return null;
  if (c.includes("<<")) return null;
  const segs = splitShellOps(c);
  if (!segs.every(segmentIsReadOnly)) return null;
  // The FIRST real head names the act: `grep ... | head` is a search, not a
  // read, and calling it by its last stage would file it under the wrong verb.
  const heads = segs
    .map((s) => realHead(tokens(s)))
    .filter((h): h is string => Boolean(h));
  const head = heads[0];
  if (!head) return "read";
  if (["grep", "rg", "egrep", "fgrep"].includes(head)) return "grep";
  if (["ls", "find", "tree"].includes(head)) return "list";
  if (["sed", "cat", "head", "tail"].includes(head)) return "read";
  return "inspect";
}
