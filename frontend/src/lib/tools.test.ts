import { describe, expect, it } from "vitest";
import {
  BLANK_TOOL,
  NO_TOOL_FILTER,
  canSave,
  capRows,
  catalogSummary,
  countByProvenance,
  createBody,
  draftFromTool,
  filterTools,
  formatArgv,
  parseArgv,
  provenanceLabel,
  toolFacet,
  updateBody,
  type ToolRecord,
} from "./tools";

function tool(over: Partial<ToolRecord> = {}): ToolRecord {
  return {
    id: "preview",
    title: "Preview",
    description: "",
    kind: "command",
    provenance: "user",
    spec: {},
    scopes: ["*"],
    ceiling: [],
    editable: true,
    ...over,
  };
}

describe("parseArgv", () => {
  it("splits on whitespace", () => {
    expect(parseArgv("shoot.sh /settings/tools 390")).toEqual(["shoot.sh", "/settings/tools", "390"]);
  });

  it("collapses runs of whitespace and trims the ends", () => {
    expect(parseArgv("  a   b\tc \n")).toEqual(["a", "b", "c"]);
  });

  it("keeps a quoted argument whole, which a naive split loses", () => {
    expect(parseArgv('note "two words" end')).toEqual(["note", "two words", "end"]);
    expect(parseArgv("note 'two words' end")).toEqual(["note", "two words", "end"]);
  });

  it("treats the other quote as an ordinary character inside a quote", () => {
    expect(parseArgv(`say "it's fine"`)).toEqual(["say", "it's fine"]);
  });

  it("escapes with a backslash outside quotes and inside double quotes", () => {
    expect(parseArgv("cat /My\\ Files/a.txt")).toEqual(["cat", "/My Files/a.txt"]);
    expect(parseArgv('echo "a \\"quote\\""')).toEqual(["echo", 'a "quote"']);
  });

  it("leaves a backslash literal inside single quotes, as sh does", () => {
    expect(parseArgv("echo 'a\\b'")).toEqual(["echo", "a\\b"]);
  });

  it("keeps an empty quoted argument, which is a real argument", () => {
    expect(parseArgv('run "" x')).toEqual(["run", "", "x"]);
  });

  it("does not throw on an unterminated quote, because it runs while typing", () => {
    expect(parseArgv('run "half')).toEqual(["run", "half"]);
  });

  it("is empty for an empty or blank line", () => {
    expect(parseArgv("")).toEqual([]);
    expect(parseArgv("   ")).toEqual([]);
  });
});

describe("formatArgv", () => {
  it("joins plain tokens with a single space", () => {
    expect(formatArgv(["a", "b"])).toBe("a b");
  });

  it("is empty for a missing command", () => {
    expect(formatArgv(null)).toBe("");
    expect(formatArgv(undefined)).toBe("");
  });

  it.each([
    [["a", "two words", "b"]],
    [["say", "it's fine"]],
    [['a "quote"']],
    [["back\\slash"]],
    [["", "after an empty one"]],
    [["/My Files/a.txt", "--flag=x y"]],
  ])("round trips %j through the field and back", (argv) => {
    expect(parseArgv(formatArgv(argv))).toEqual(argv);
  });
});

describe("draftFromTool", () => {
  it("puts the stored argv back on one line", () => {
    const d = draftFromTool(tool({ spec: { command: ["shoot.sh", "a b"] } }));
    expect(d.command).toBe('shoot.sh "a b"');
    expect(d.id).toBe("preview");
    expect(d.kind).toBe("command");
  });

  it("leaves the command line empty for a tool that has none", () => {
    expect(draftFromTool(tool({ kind: "builtin", spec: {} })).command).toBe("");
  });
});

describe("createBody", () => {
  it("trims the id and the text, and sends the parsed argv", () => {
    const body = createBody({
      ...BLANK_TOOL,
      id: "  preview  ",
      title: " Preview ",
      description: " Shoot a page. ",
      command: 'shoot.sh "/a path"',
    });
    expect(body.id).toBe("preview");
    expect(body.title).toBe("Preview");
    expect(body.description).toBe("Shoot a page.");
    expect(body.spec?.command).toEqual(["shoot.sh", "/a path"]);
  });

  it("sends no command key at all when the line is blank", () => {
    const body = createBody({ ...BLANK_TOOL, id: "x" });
    expect(body.spec && "command" in body.spec).toBe(false);
  });

  it("does not judge a colliding id: the registry's refusal names it", () => {
    // A client-side "that name is taken" would replace the backend sentence
    // that actually names the conflicting id and its provenance.
    expect(createBody({ ...BLANK_TOOL, id: "WebSearch" }).id).toBe("WebSearch");
  });
});

describe("updateBody", () => {
  it("carries scopes and ceiling forward, which the form does not show", () => {
    const base = tool({ scopes: ["chat"], ceiling: ["Bash"], spec: { command: ["a"] } });
    const body = updateBody({ ...draftFromTool(base), title: "New title" }, base);
    expect(body.scopes).toEqual(["chat"]);
    expect(body.ceiling).toEqual(["Bash"]);
    expect(body.title).toBe("New title");
  });

  it("carries spec keys the form does not own, so a title edit cannot erase them", () => {
    const base = tool({
      kind: "command",
      spec: { command: ["run.sh"], params: [{ name: "target", type: "string", description: "", required: true }] },
    });
    const body = updateBody({ ...draftFromTool(base), description: "why" }, base);
    expect(body.spec?.params).toEqual([{ name: "target", type: "string", description: "", required: true }]);
    expect(body.spec?.command).toEqual(["run.sh"]);
  });

  it("keeps an mcp record's server and tool when the command line is untouched", () => {
    const base = tool({ kind: "mcp", spec: { server: "workshop", tool: "search" } });
    const body = updateBody({ ...draftFromTool(base), title: "Search" }, base);
    expect(body.spec?.server).toBe("workshop");
    expect(body.spec?.tool).toBe("search");
  });

  it("drops the command when the line is emptied", () => {
    const base = tool({ spec: { command: ["run.sh"] } });
    const body = updateBody({ ...draftFromTool(base), command: "" }, base);
    expect(body.spec && "command" in body.spec).toBe(false);
  });
});

describe("provenanceLabel", () => {
  it("says Yours for the operator's own tools", () => {
    expect(provenanceLabel("user")).toBe("Yours");
  });

  it("keeps the words the design doc and the API refusals use", () => {
    expect(provenanceLabel("platform")).toBe("Platform");
    expect(provenanceLabel("app")).toBe("App");
  });

  it("passes an unknown provenance through rather than inventing one", () => {
    expect(provenanceLabel("fleet")).toBe("fleet");
  });
});

describe("catalogSummary", () => {
  const nine = Array.from({ length: 9 }, (_, i) => tool({ id: `p${i}`, provenance: "platform" }));

  it("counts each provenance in catalog order", () => {
    expect(catalogSummary([...nine, tool({ id: "a", provenance: "app" }), tool({ id: "u" })]))
      .toBe("9 platform, 1 app, 1 yours");
  });

  it("leaves out a provenance with nothing in it, rather than printing a zero", () => {
    expect(catalogSummary(nine)).toBe("9 platform");
  });

  it("is empty for an empty catalog", () => {
    expect(catalogSummary([])).toBe("");
  });

  it("still names a provenance it has never heard of", () => {
    expect(catalogSummary([tool({ provenance: "fleet" })])).toBe("1 fleet");
  });

  it("counts by provenance", () => {
    expect(countByProvenance([tool(), tool({ id: "b" }), tool({ id: "c", provenance: "platform" })]))
      .toEqual({ user: 2, platform: 1 });
  });
});

describe("capRows", () => {
  const rows = Array.from({ length: 30 }, (_, i) => i);

  it("shows everything when the list fits", () => {
    expect(capRows([1, 2, 3], false)).toEqual({ rows: [1, 2, 3], hidden: 0 });
  });

  it("caps and reports how many are not shown", () => {
    const out = capRows(rows, false);
    expect(out.rows).toHaveLength(24);
    expect(out.hidden).toBe(6);
  });

  it("shows all of them once expanded", () => {
    expect(capRows(rows, true)).toEqual({ rows, hidden: 0 });
  });

  it("does not cap at exactly the cap", () => {
    expect(capRows(rows.slice(0, 24), false).hidden).toBe(0);
  });
});

describe("canSave", () => {
  it("needs an id to create, since the API cannot default one", () => {
    expect(canSave({ ...BLANK_TOOL, id: "" }, true, false)).toBe(false);
    expect(canSave({ ...BLANK_TOOL, id: "  " }, true, false)).toBe(false);
    expect(canSave({ ...BLANK_TOOL, id: "preview" }, true, false)).toBe(true);
  });

  it("needs an actual change to save an edit", () => {
    expect(canSave({ ...BLANK_TOOL, id: "preview" }, false, false)).toBe(false);
    expect(canSave({ ...BLANK_TOOL, id: "preview" }, false, true)).toBe(true);
  });
});

describe("the catalog filter", () => {
  const rows: ToolRecord[] = [
    tool({ id: "WebSearch", title: "Web search", description: "Search the public web.", kind: "builtin", provenance: "platform" }),
    tool({ id: "tallywidget", title: "Tally", description: "Counts widgets in a hopper.", kind: "command", provenance: "app" }),
    tool({ id: "mine", title: "Mine", description: "A tool I declared.", kind: "command", provenance: "user" }),
  ];

  it("reads its menus off the rows, in first-seen order", () => {
    expect(toolFacet(rows, "kind")).toEqual(["builtin", "command"]);
    expect(toolFacet(rows, "provenance")).toEqual(["platform", "app", "user"]);
    expect(toolFacet([], "kind")).toEqual([]);
  });

  it("is inert until something is chosen or typed", () => {
    expect(filterTools(rows, NO_TOOL_FILTER)).toHaveLength(3);
    expect(filterTools(rows, { ...NO_TOOL_FILTER, q: "   " })).toHaveLength(3);
  });

  it("narrows by facet, by text, and by both together", () => {
    expect(filterTools(rows, { ...NO_TOOL_FILTER, kind: "command" }).map((t) => t.id))
      .toEqual(["tallywidget", "mine"]);
    expect(filterTools(rows, { ...NO_TOOL_FILTER, provenance: "user" }).map((t) => t.id))
      .toEqual(["mine"]);
    expect(filterTools(rows, { ...NO_TOOL_FILTER, q: "HOPPER" }).map((t) => t.id))
      .toEqual(["tallywidget"]);
    expect(filterTools(rows, { ...NO_TOOL_FILTER, kind: "command", q: "declared" }).map((t) => t.id))
      .toEqual(["mine"]);
    expect(filterTools(rows, { ...NO_TOOL_FILTER, q: "nothing here" })).toEqual([]);
  });
});
