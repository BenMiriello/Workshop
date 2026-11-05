// `act` from the contract (docs/chat-beside-a-page.md), over plain DOM: what the
// chat beside a page does when it clicks, types, chooses, presses, scrolls or
// hovers. Events are built from the element's OWN window, so they are real
// events of that document even inside a frame, and fields are set through the
// native value setter so frameworks that track the value see the change.

import type { OutlineState } from "./outline";

export type ActArgs = {
  action?: string; ref?: string; text?: string; submit?: boolean; key?: string; direction?: string;
};

type Win = Window & typeof globalThis;

function winOf(el: Element): Win {
  return el.ownerDocument.defaultView as Win;
}

function mouse(el: Element, type: string) {
  const W = winOf(el);
  const r = el.getBoundingClientRect();
  const init = {
    bubbles: true, cancelable: true, view: W, composed: true,
    clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0,
  };
  const Ctor = type.startsWith("pointer") && W.PointerEvent ? W.PointerEvent : W.MouseEvent;
  return el.dispatchEvent(new Ctor(type, init));
}

function key(el: Element, k: string) {
  const W = winOf(el);
  const init = { key: k, bubbles: true, cancelable: true, composed: true };
  const proceed = el.dispatchEvent(new W.KeyboardEvent("keydown", init));
  el.dispatchEvent(new W.KeyboardEvent("keyup", init));
  return proceed;
}

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const W = winOf(el);
  const proto = el.tagName === "TEXTAREA" ? W.HTMLTextAreaElement.prototype : W.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new W.Event("input", { bubbles: true }));
  el.dispatchEvent(new W.Event("change", { bubbles: true }));
}

function describe(el: Element): string {
  const label = el.getAttribute("aria-label") || (el.textContent || "").replace(/\s+/g, " ").trim()
    || el.getAttribute("placeholder") || el.tagName.toLowerCase();
  return `"${label.slice(0, 60)}"`;
}

function scrollTarget(el: Element | null, doc: Document): Element {
  return el ?? doc.scrollingElement ?? doc.documentElement;
}

/** Do one action. Returns a sentence saying what was done, or throws with one
 *  saying why it could not be. */
export function act(root: Element, state: OutlineState, args: ActArgs): string {
  const action = args.action || "";
  const el = args.ref ? state.refs.get(args.ref) ?? null : null;
  if (args.ref && (!el || !el.isConnected)) {
    throw new Error(`There is no element ${args.ref} on the page now. Read the page again.`);
  }
  const doc = el?.ownerDocument ?? root.ownerDocument;
  if (action !== "scroll" && action !== "press_key" && !el) {
    throw new Error(`${action || "That action"} needs a ref from page_read or page_find.`);
  }
  switch (action) {
    case "click": {
      el!.scrollIntoView({ block: "center", inline: "nearest" });
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup"]) mouse(el!, t);
      (el as HTMLElement).focus?.({ preventScroll: true });
      mouse(el!, "click");
      return `Clicked ${describe(el!)}.`;
    }
    case "hover": {
      for (const t of ["pointerover", "pointerenter", "mouseover", "mouseenter", "mousemove"]) mouse(el!, t);
      return `Hovered over ${describe(el!)}.`;
    }
    case "type": {
      const text = args.text ?? "";
      const h = el as HTMLElement;
      h.focus?.();
      if (h.tagName === "INPUT" || h.tagName === "TEXTAREA") {
        setValue(h as HTMLInputElement, text);
      } else if (h.isContentEditable) {
        doc.getSelection()?.selectAllChildren(h);
        doc.execCommand("insertText", false, text);
      } else {
        throw new Error(`${describe(h)} is not a text field.`);
      }
      if (args.submit) {
        const proceed = key(h, "Enter");
        const form = (h as HTMLInputElement).form;
        if (proceed && form) form.requestSubmit();
      }
      return `Typed into ${describe(h)}${args.submit ? " and pressed Enter" : ""}.`;
    }
    case "select": {
      const want = (args.text ?? "").trim().toLowerCase();
      if (el!.tagName === "SELECT") {
        const s = el as HTMLSelectElement;
        const opt = Array.from(s.options).find((o) =>
          o.value.toLowerCase() === want || (o.textContent || "").trim().toLowerCase() === want);
        if (!opt) throw new Error(`No option "${args.text}" in ${describe(s)}.`);
        s.value = opt.value;
        s.dispatchEvent(new (winOf(s).Event)("input", { bubbles: true }));
        s.dispatchEvent(new (winOf(s).Event)("change", { bubbles: true }));
        return `Chose "${(opt.textContent || opt.value).trim()}" in ${describe(s)}.`;
      }
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) mouse(el!, t);
      return `Opened ${describe(el!)}; read the page to pick the option.`;
    }
    case "press_key": {
      const k = args.key || "Enter";
      const target = el ?? doc.activeElement ?? doc.body;
      key(target, k);
      return `Pressed ${k}.`;
    }
    case "scroll": {
      const target = scrollTarget(el, doc);
      const view = doc.defaultView!;
      const dy = { up: -1, down: 1 }[args.direction || "down"] ?? 0;
      const dx = { left: -1, right: 1 }[args.direction || ""] ?? 0;
      const h = el ? el.clientHeight : view.innerHeight;
      const w = el ? el.clientWidth : view.innerWidth;
      target.scrollBy({ top: dy * h * 0.8, left: dx * w * 0.8 });
      return `Scrolled ${args.direction || "down"}.`;
    }
    default:
      throw new Error(`Unknown action "${action}". Use click, type, select, press_key, scroll or hover.`);
  }
}
