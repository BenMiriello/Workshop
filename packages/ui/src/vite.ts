import { APPEARANCE_SCRIPT } from "./appearanceScript";

/** Vite plugin for any page built on the kit, the shell and every framed app:
 *  puts the appearance script at the top of index.html's <head>, so the page
 *  shows the chosen theme and color scheme from its first paint and follows
 *  Settings while it is open. Node-side only; it imports nothing from React. */
export function workshopAppearance() {
  return {
    name: "workshop-appearance",
    transformIndexHtml: () => [
      { tag: "script", children: APPEARANCE_SCRIPT, injectTo: "head-prepend" as const },
    ],
  };
}
