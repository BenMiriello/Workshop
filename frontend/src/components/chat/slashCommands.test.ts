import { describe, expect, it } from "vitest";
import { buildSlashCommands, unsupportedCommand } from "./slashCommands";

const actions = {
  newChat: () => {}, deleteChat: () => {}, toggleTasks: () => {}, toggleInbox: () => {},
  popOut: () => {}, showHelp: () => {}, openDeploys: () => {}, compact: () => {}, clear: () => {},
};
const build = (over: Partial<Parameters<typeof buildSlashCommands>[0]> = {}) =>
  buildSlashCommands({
    isBuild: true, hasThread: true, hasTaskScope: true, canExpand: true,
    compacting: false, actions, ...over,
  });

describe("the catalogue offers nothing that cannot run", () => {
  // Measured against the real CLI on 2026-08-10 in this chat's mode
  // (`claude -p --output-format stream-json`): each of these answered
  // "/<name> isn't available in this environment", after spawning a process and
  // spending a full prompt to say so.
  const dead = ["model", "mcp", "agents", "memory", "status"];

  it("does not list a command the environment cannot run", () => {
    const names = build().map((c) => c.name);
    for (const d of dead) expect(names).not.toContain(d);
  });

  it("keeps the one passthrough that returns something real", () => {
    // /context comes back as a full markdown breakdown: tokens by category, MCP
    // tools per server, memory files, skills.
    expect(build().find((c) => c.name === "context")?.kind).toBe("passthrough");
  });

  it("keeps the two that are tasks rather than TUI commands", () => {
    // These run as an ordinary turn, which is exactly why they work.
    const names = build().map((c) => c.name);
    expect(names).toContain("init");
    expect(names).toContain("review");
  });

  it("still keeps /init and /review to a project", () => {
    const names = build({ isBuild: false }).filter((c) => c.available).map((c) => c.name);
    expect(names).not.toContain("init");
    expect(names).not.toContain("review");
  });
});

describe("unsupportedCommand", () => {
  it("answers a dead command locally instead of spending a turn", () => {
    expect(unsupportedCommand("/mcp")).toContain("doesn't work in Workshop chat");
  });

  it("points at the control that does the job", () => {
    expect(unsupportedCommand("/model")).toContain("model picker");
    expect(unsupportedCommand("/cost")).toContain("Usage page");
  });

  it("leaves a working command alone", () => {
    for (const ok of ["/context", "/compact", "/clear", "/init", "/review"]) {
      expect(unsupportedCommand(ok)).toBeNull();
    }
  });

  it("is not fooled by a path or a sentence", () => {
    expect(unsupportedCommand("/usr/bin/model is missing")).toBeNull();
    expect(unsupportedCommand("what does /model do?")).toBeNull();
  });

  it("matches a command with arguments", () => {
    expect(unsupportedCommand("/model opus")).toContain("model picker");
  });

  it("says nothing about ordinary text", () => {
    expect(unsupportedCommand("hello")).toBeNull();
  });
});
