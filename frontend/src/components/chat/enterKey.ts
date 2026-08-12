// What the Return key does, and how the answer is arrived at.
//
// THE CONTRACT
//
//   physical keyboard   Enter sends. Shift+Enter starts a new line.
//   virtual keyboard    Enter starts a new line. The Send button sends.
//   either              Cmd/Ctrl+Enter sends, always.
//
// WHY IT IS KEYED ON THE KEYBOARD AND NOT ON THE DEVICE
//
// "Phone means newline, desktop means send" is the right instinct and the wrong
// test, because the thing that actually decides is whether the operator HAS a
// second affordance. On a physical keyboard Shift+Enter is a free, universally
// known way to get a newline, so Enter is spare and sending with it is the
// convention every chat app on the desktop shares. On a virtual keyboard there
// is no Shift+Enter: soft keyboards do not report `shiftKey` on Return, so the
// Return key is the ONLY way to type a line break. Take it for sending and
// multi-line messages become impossible to write.
//
// Sending is never in the same bind, because this composer always has a Send
// button. That asymmetry is the whole design:
//
//   guess "newline" wrongly -> the operator taps Send. One extra tap.
//   guess "send" wrongly    -> a half-written message is gone. Not undoable.
//
// So bare Enter sends only on POSITIVE evidence of a physical keyboard, and
// every ambiguous case falls to newline.
//
// Width is not the test. An iPad with a Magic Keyboard is 1024px and should
// send; a phone held in landscape is 900px and must not. Both are decided
// correctly below and neither is decided by a breakpoint.
//
// Composition (IME) is checked before anything else. During a Chinese, Japanese
// or Korean composition the Enter key COMMITS the candidate; it is not a
// message-level keypress at all, and acting on it sends half a word. Browsers
// report this as `isComposing`, and legacy engines as keyCode 229.

export type EnterAction = "send" | "newline" | "ignore";

/** Just the fields of a KeyboardEvent this rule reads, so it can be tested
 *  without a DOM and without a synthetic React event. */
export interface EnterEventLike {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  isComposing: boolean;
  keyCode?: number;
}

/** "ignore" means this rule has no opinion: let the browser do its default. */
export function enterAction(e: EnterEventLike, physicalKeyboard: boolean): EnterAction {
  if (e.key !== "Enter") return "ignore";
  // Mid-composition. `isComposing` is the standard signal; keyCode 229 is what
  // engines that predate it report for every key inside a composition.
  if (e.isComposing || e.keyCode === 229) return "ignore";
  // The escape hatch, and the one binding that means the same thing on every
  // keyboard and every platform. It is also the answer to "how do I send from a
  // Bluetooth keyboard if the detection ever gets it wrong".
  if (e.metaKey || e.ctrlKey) return "send";
  // Shift+Enter is the desktop newline. Alt+Enter is the same intent from the
  // spreadsheet world and costs nothing to honour. Either one is also proof of
  // a physical keyboard, but it does not matter here: both mean newline.
  if (e.shiftKey || e.altKey) return "newline";
  return physicalKeyboard ? "send" : "newline";
}

// ---- Is there a physical keyboard? ----------------------------------------
//
// There is no browser API that answers this, so it is settled from three
// signals, read at the moment Enter is pressed rather than once at load. That
// reframing is what makes it tractable: a keyboard can be paired, unpaired or
// folded away at any time, but the app only ever needs the answer right now.

/** Keys a virtual keyboard does not produce. Seeing any one of them is proof a
 *  real keyboard is attached, and the proof is kept: soft keyboards emit no
 *  bare modifier, no Tab, no Escape, no arrows and no function keys. */
const PHYSICAL_ONLY = new Set([
  "Shift", "Control", "Alt", "Meta", "Tab", "Escape",
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "Home", "End", "PageUp", "PageDown", "Insert",
]);

export function isPhysicalKeyEvidence(key: string): boolean {
  return PHYSICAL_ONLY.has(key) || /^F\d{1,2}$/.test(key);
}

/** How much the on-screen keyboard has to eat before it counts as being up.
 *
 *  Not zero, because an iPad with an external keyboard still shows a slim
 *  shortcut/predictive bar (roughly 55px). That bar is exactly the case that
 *  must read as "physical", so the threshold sits well above it and well below
 *  a real soft keyboard, which takes a third of the screen or more. */
export const OSK_MIN_SHRINK_PX = 150;

export interface KeyboardSignals {
  /** A physical-only key has been seen in this tab. Sticky. */
  sawPhysicalKey: boolean;
  /** `(pointer: coarse)` — a touch-first device. Device class, not keyboard. */
  coarsePointer: boolean;
  /** `innerHeight - visualViewport.height`, or null where unmeasurable. */
  viewportShrinkPx: number | null;
}

/** The decision, pure. See the asymmetry note at the top: everything unknown
 *  resolves to false, because false is the cheap mistake. */
export function hasPhysicalKeyboard(s: KeyboardSignals): boolean {
  // Positive proof outranks everything, including the device class. This is
  // what makes a Bluetooth keyboard on a phone work, and it needs no viewport
  // measurement: one arrow key or one Shift is enough, forever.
  if (s.sawPhysicalKey) return true;
  // A fine pointer is a mouse or a trackpad. That includes an iPad driving a
  // Magic Keyboard, which reports fine, and it is the ordinary desktop case.
  if (!s.coarsePointer) return true;
  // Touch device, no proof yet. The on-screen keyboard settles it: if the
  // operator is typing and it never came up, something else is producing the
  // keystrokes. Unmeasurable falls to the safe side rather than guessing.
  if (s.viewportShrinkPx === null) return false;
  return s.viewportShrinkPx < OSK_MIN_SHRINK_PX;
}
