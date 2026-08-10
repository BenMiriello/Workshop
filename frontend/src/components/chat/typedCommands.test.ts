import { describe, expect, it } from "vitest";
import { retiredCommand, typedCommand } from "./typedCommands";

describe("typedCommand", () => {
  it("accepts the three that act on this conversation", () => {
    expect(typedCommand("/compact")).toBe("compact");
    expect(typedCommand("/clear")).toBe("clear");
    expect(typedCommand("/new")).toBe("new");
  });

  it("is case-insensitive and ignores surrounding space", () => {
    expect(typedCommand("  /Compact  ")).toBe("compact");
  });

  it("does not swallow a sentence that starts with one", () => {
    // "/compact the logs please" is a message ABOUT compacting. Treating it as
    // the command would silently drop everything the operator wrote after it.
    expect(typedCommand("/compact the logs please")).toBeNull();
  });

  it("leaves a path alone", () => {
    expect(typedCommand("/usr/bin/new")).toBeNull();
  });

  it("leaves ordinary text alone", () => {
    expect(typedCommand("compact")).toBeNull();
    expect(typedCommand("what does /clear do?")).toBeNull();
  });

  it("ignores a multi-line message", () => {
    expect(typedCommand("/compact\nand also this")).toBeNull();
  });
});

describe("retiredCommand", () => {
  // Measured against the real CLI on 2026-08-10: these answered "isn't
  // available in this environment" after spawning a process and spending a full
  // prompt to say so. The rest were duplicates of visible controls.
  it("points a dead CLI command at the control that does the job", () => {
    expect(retiredCommand("/model")).toContain("model picker");
    expect(retiredCommand("/context")).toContain("context percentage");
    expect(retiredCommand("/mcp")).toContain("context percentage");
    expect(retiredCommand("/cost")).toContain("Usage page");
  });

  it("points a removed local command at its button", () => {
    expect(retiredCommand("/delete")).toContain("delete control");
    expect(retiredCommand("/tasks")).toContain("tasks button");
    expect(retiredCommand("/deploy")).toContain("project page");
  });

  it("says nothing about the three that still work", () => {
    for (const ok of ["/compact", "/clear", "/new"]) {
      expect(retiredCommand(ok)).toBeNull();
    }
  });

  it("lets anything else through as an ordinary message", () => {
    // This chat is not a terminal. An unknown "/x" goes to the model rather
    // than being swallowed by a command layer that no longer exists.
    expect(retiredCommand("/whatever")).toBeNull();
    expect(retiredCommand("hello")).toBeNull();
  });

  it("names the three when asked for help", () => {
    const h = retiredCommand("/help");
    expect(h).toContain("/compact");
    expect(h).toContain("/clear");
    expect(h).toContain("/new");
  });
});
