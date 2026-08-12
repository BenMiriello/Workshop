import { describe, it, expect } from "vitest";
import {
  enterAction, hasPhysicalKeyboard, isPhysicalKeyEvidence, OSK_MIN_SHRINK_PX,
  type EnterEventLike, type KeyboardSignals,
} from "./enterKey";

function key(over: Partial<EnterEventLike> = {}): EnterEventLike {
  return {
    key: "Enter", shiftKey: false, altKey: false, ctrlKey: false,
    metaKey: false, isComposing: false, ...over,
  };
}

describe("enterAction", () => {
  describe("on a physical keyboard", () => {
    it("sends on a bare Enter", () => {
      expect(enterAction(key(), true)).toBe("send");
    });
    it("makes a new line on Shift+Enter", () => {
      expect(enterAction(key({ shiftKey: true }), true)).toBe("newline");
    });
    it("honours Alt+Enter as a new line too", () => {
      expect(enterAction(key({ altKey: true }), true)).toBe("newline");
    });
  });

  describe("on a virtual keyboard", () => {
    it("makes a new line on Return, because Send is a button", () => {
      // The reported bug: Return sent the message and there was no way at all
      // to type a line break on a phone.
      expect(enterAction(key(), false)).toBe("newline");
    });
    it("still sends on Cmd+Enter, the escape hatch", () => {
      expect(enterAction(key({ metaKey: true }), false)).toBe("send");
    });
    it("still sends on Ctrl+Enter", () => {
      expect(enterAction(key({ ctrlKey: true }), false)).toBe("send");
    });
  });

  describe("composition", () => {
    it("does nothing while an IME is composing, on either keyboard", () => {
      // Enter COMMITS a candidate here. Sending on it sends half a word.
      expect(enterAction(key({ isComposing: true }), true)).toBe("ignore");
      expect(enterAction(key({ isComposing: true }), false)).toBe("ignore");
    });
    it("treats legacy keyCode 229 as composing", () => {
      expect(enterAction(key({ keyCode: 229 }), true)).toBe("ignore");
    });
    it("does not let a modifier override a composition", () => {
      expect(enterAction(key({ isComposing: true, metaKey: true }), true)).toBe("ignore");
    });
  });

  it("has no opinion about any other key", () => {
    expect(enterAction(key({ key: "a" }), true)).toBe("ignore");
    expect(enterAction(key({ key: "Tab" }), false)).toBe("ignore");
  });
});

describe("isPhysicalKeyEvidence", () => {
  it("counts keys a soft keyboard cannot produce", () => {
    for (const k of ["Shift", "Control", "Alt", "Meta", "Tab", "Escape", "ArrowUp", "End", "F5", "F12"]) {
      expect(isPhysicalKeyEvidence(k)).toBe(true);
    }
  });
  it("does not count keys a soft keyboard produces every day", () => {
    for (const k of ["a", "Z", "1", " ", "Enter", "Backspace", "."]) {
      expect(isPhysicalKeyEvidence(k)).toBe(false);
    }
  });
});

describe("hasPhysicalKeyboard", () => {
  const base: KeyboardSignals = { sawPhysicalKey: false, coarsePointer: true, viewportShrinkPx: null };

  it("is true on an ordinary desktop, with no measurement needed", () => {
    expect(hasPhysicalKeyboard({ ...base, coarsePointer: false })).toBe(true);
  });

  it("is false on a phone with the on-screen keyboard up", () => {
    expect(hasPhysicalKeyboard({ ...base, viewportShrinkPx: 320 })).toBe(false);
  });

  it("is true on a tablet whose keyboard never came up", () => {
    // The iPad-with-a-Bluetooth-keyboard case: coarse pointer, focused, and
    // the viewport did not move.
    expect(hasPhysicalKeyboard({ ...base, viewportShrinkPx: 0 })).toBe(true);
  });

  it("reads an iPad's external-keyboard shortcut bar as physical", () => {
    // ~55px of predictive/shortcut bar, well under the threshold.
    expect(hasPhysicalKeyboard({ ...base, viewportShrinkPx: 55 })).toBe(true);
  });

  it("reads a real soft keyboard as virtual at the threshold", () => {
    expect(hasPhysicalKeyboard({ ...base, viewportShrinkPx: OSK_MIN_SHRINK_PX })).toBe(false);
  });

  it("lets one physical keypress outrank a coarse pointer forever", () => {
    // A Bluetooth keyboard paired to a phone: the pointer stays coarse and the
    // OSK may still be up, but an arrow key already proved the keyboard exists.
    expect(hasPhysicalKeyboard({ sawPhysicalKey: true, coarsePointer: true, viewportShrinkPx: 400 })).toBe(true);
  });

  it("falls to virtual when the viewport cannot be measured", () => {
    // The cheap mistake by design: an extra tap on Send, never a message sent
    // half-written.
    expect(hasPhysicalKeyboard(base)).toBe(false);
  });
});
