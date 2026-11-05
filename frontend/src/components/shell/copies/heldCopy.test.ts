import { beforeEach, describe, expect, it } from "vitest";
import { heldCopy } from "./heldCopy";

describe("heldCopy", () => {
  beforeEach(() => heldCopy.reset());

  it("gives nothing before anything is copied", () => {
    expect(heldCopy.current()).toBeNull();
  });

  it("gives the latest copy while the Workshop is not left", () => {
    heldCopy.took("first", 10);
    heldCopy.took("second", 20);
    expect(heldCopy.current()).toBe("second");
  });

  it("gives way to the system clipboard once the Workshop is left", () => {
    heldCopy.took("held", 10);
    heldCopy.left(20);
    expect(heldCopy.current()).toBeNull();
  });

  it("holds a copy made after coming back", () => {
    heldCopy.left(20);
    heldCopy.took("again", 30);
    expect(heldCopy.current()).toBe("again");
  });
});
