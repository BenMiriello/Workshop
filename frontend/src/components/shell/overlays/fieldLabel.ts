// What to call a field when telling an overlay app where its text will go:
// the field's own name, read the way dictation reads it (lib/speech/placeName).

import { fieldName } from "../../../lib/speech/placeName";

function labelledBy(el: Element): string {
  const ids = el.getAttribute("aria-labelledby");
  if (!ids) return "";
  const doc = el.ownerDocument;
  return ids.split(/\s+/).map((id) => doc.getElementById(id)?.textContent ?? "").join(" ");
}

export function fieldLabel(el: HTMLElement): string {
  const labels = (el as HTMLInputElement).labels;
  return fieldName({
    ariaLabel: el.getAttribute("aria-label"),
    labelledBy: labelledBy(el),
    label: labels && labels.length ? labels[0].textContent : null,
    placeholder: el.getAttribute("placeholder") ?? el.getAttribute("data-placeholder"),
    title: el.getAttribute("title"),
  }) || "Text field";
}
