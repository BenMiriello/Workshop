import { describe, expect, it } from "vitest";
import { COPY_PREFS, SET_COPY_PREFS, parseCopyPrefsRequest } from "./copyPrefsBridge";

describe("parseCopyPrefsRequest", () => {
  it("reads an ask", () => {
    expect(parseCopyPrefsRequest({ v: 1, type: COPY_PREFS })).toEqual({ kind: "get" });
  });
  it("reads a change, keeping only the values it knows", () => {
    expect(parseCopyPrefsRequest({ v: 1, type: SET_COPY_PREFS, from: "off", destination: "workshop" }))
      .toEqual({ kind: "set", from: "off", destination: "workshop" });
    expect(parseCopyPrefsRequest({ v: 1, type: SET_COPY_PREFS, from: "anywhere", destination: 3 }))
      .toEqual({ kind: "set" });
  });
  it("ignores other messages", () => {
    expect(parseCopyPrefsRequest({ v: 2, type: COPY_PREFS })).toBeNull();
    expect(parseCopyPrefsRequest({ v: 1, type: "workshop/v1/hello" })).toBeNull();
    expect(parseCopyPrefsRequest(null)).toBeNull();
  });
});
