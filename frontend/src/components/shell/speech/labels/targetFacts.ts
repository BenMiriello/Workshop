// What the page says about a target beyond its name, read when a control is
// looked for by meaning or place rather than on every recollect: its kind, the
// icon it shows, whether it is a picture, its title, the row it sits in when
// its name is not unique, the heading, landmark or labelled region around it,
// and whether it is the shell's own rather than the page's.

import { nameFrom } from "./targetMatch";
import { kindFits, kindWordOf, placeName } from "./ordinals";
import { describeTarget, iconName, kindOf, type DescribeFacts, type TargetKind } from "./targetKinds";
import type { Target } from "./pageTargets";

export type Described = Target & DescribeFacts & { chrome: boolean; text: string };

// An image smaller than this is an icon or an avatar, not a picture.
const PICTURE_MIN = 40;

export function iconOf(el: Element): string {
  const svg = el.matches("svg") ? el : el.querySelector("svg[class*=lucide]");
  return svg ? iconName(svg.getAttribute("class") ?? "") : "";
}

export function isPicture(el: Element): boolean {
  const media = el.matches("img,video,picture,canvas") ? el : el.querySelector("img,video,picture,canvas");
  if (!media) return false;
  const r = media.getBoundingClientRect();
  return r.width >= PICTURE_MIN && r.height >= PICTURE_MIN;
}

const LANDMARKS: Record<string, string> = {
  NAV: "navigation", HEADER: "header", ASIDE: "sidebar", FOOTER: "footer",
};
const REGION = "section,article,aside,nav,header,footer,form,fieldset,dialog,[role=dialog],[role=region],[role=toolbar],[role=tablist],[role=navigation]";
const HEADING = "h1,h2,h3,h4,legend,[role=heading]";
const DEPTH = 12;
const ROW_DEPTH = 8;
// A container holding more controls than this is a list or a toolbar, not
// one control's row.
const ROW_CONTROLS = 4;
const PAGE = ".ws-app__body > main,[role=dialog],dialog";

function labelOf(el: Element): string {
  const ids = el.getAttribute("aria-labelledby")?.split(/\s+/).filter(Boolean) ?? [];
  const by = ids.map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "").join(" ");
  return nameFrom({ labelledBy: by, ariaLabel: el.getAttribute("aria-label") ?? "" });
}

// The first heading of `region` that comes before `el` and is not part of it.
function headingBefore(region: Element, el: Element): string {
  for (const h of Array.from(region.querySelectorAll(HEADING))) {
    if (h.contains(el)) continue;
    if (!(h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) break;
    return nameFrom({ text: h.textContent ?? "" });
  }
  return "";
}

/** The labelled region, heading or landmark `el` sits in; the frame's title
 *  when nothing in it says. */
export function contextOf(el: Element): string {
  let a = el.parentElement;
  for (let d = 0; a && d < DEPTH && a !== a.ownerDocument.body; d++, a = a.parentElement) {
    if (!a.matches(REGION)) continue;
    const said = labelOf(a) || headingBefore(a, el);
    if (said) return said;
    if (LANDMARKS[a.tagName]) return LANDMARKS[a.tagName];
  }
  const doc = el.ownerDocument;
  return doc !== document ? nameFrom({ text: doc.title }) : "";
}

// What tells one of several same-named controls from the rest: the text of
// the nearest container around it with text of its own, short of one that
// holds another of them or a whole list of controls.
function rowOf(t: Target, all: readonly Target[]): string {
  const twins = all.filter((o) => o !== t && o.name === t.name).map((o) => o.el);
  if (!t.name || !twins.length) return "";
  let a = t.el.parentElement;
  for (let d = 0; a && d < ROW_DEPTH && a !== a.ownerDocument.body; d++, a = a.parentElement) {
    const holder = a;
    if (twins.some((el) => holder.contains(el))) return "";
    if (all.filter((o) => holder.contains(o.el)).length > ROW_CONTROLS) return "";
    const text = (holder.textContent ?? "").replace(t.name, " ");
    const said = nameFrom({ text });
    if (said.length > 1) return said;
  }
  return "";
}

function targetKind(el: Element): TargetKind {
  const type = el.tagName === "INPUT" ? ((el as HTMLInputElement).type || "text").toLowerCase() : "";
  return kindOf({ tag: el.tagName, role: (el.getAttribute("role") ?? "").split(" ")[0], type,
    editable: (el as HTMLElement).isContentEditable });
}

const isChrome = (el: Element) => el.ownerDocument === document && !el.closest(PAGE);

/** What `t` can be said by besides its number: its name, else its place
 *  among the page's targets of its kind ("third image"), else nothing. A
 *  place is counted on the page before the shell around it (clickResolve.ts),
 *  so the shell's own unnamed controls go by their number. */
export function sayingOf(t: Target, all: readonly Target[]): string {
  if (t.name) return t.name;
  if (isChrome(t.el)) return "";
  const kinded = (u: Target) => ({ kind: targetKind(u.el), image: isPicture(u.el) });
  const word = kindWordOf(kinded(t));
  if (!word) return "";
  const before = all.slice(0, all.indexOf(t)).filter((u) => !isChrome(u.el) && kindFits(word, kinded(u)));
  return placeName(word, before.length + 1);
}

/** Each target with what is said about it, and that as one line. */
export function describeAll(targets: readonly Target[]): Described[] {
  return targets.map((t) => {
    const el = t.el;
    const facts: DescribeFacts = {
      name: t.name,
      kind: targetKind(el),
      image: isPicture(el),
      icon: iconOf(el),
      title: nameFrom({ title: el.getAttribute("title") ?? "" }),
      row: rowOf(t, targets),
      context: contextOf(el),
    };
    const chrome = isChrome(el);
    return { ...t, ...facts, chrome, text: describeTarget(facts) };
  });
}
