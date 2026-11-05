// What the shells' own "Read aloud" runs in the page: the item the iPhone app
// adds to the system text-selection menu, and the Mac app to its right-click
// menu. It finds the selected text in the shell document or a same-origin app
// frame, the focused frame first, and hands it to the reader the web shell
// keeps on its window (`__workshopReadAloud`, packages/ui/src/readAloud.ts).
//
// Installed beside the bridge stub (inject.ts), on the top document only, so
// the native side just calls the global. The macOS shell bundles it the same
// way (macos/bridge/client.ts) and calls the same name.

export const READ_SELECTION_GLOBAL = "__workshopReadSelection";

/** The call a shell evaluates when its "Read aloud" item is chosen. */
export const READ_SELECTION_CALL =
  `window.${READ_SELECTION_GLOBAL} && window.${READ_SELECTION_GLOBAL}(); true;`;

export function buildReadSelection(): string {
  // NOTE: kept ES5-ish on purpose, like the bridge stub.
  return `(function(){
    var MAX_DEPTH=6;
    function frameDoc(frame){
      try { return frame.contentDocument; } catch(e){ return null; }
    }
    function fieldText(el){
      if (!el || (el.tagName !== "TEXTAREA" && el.tagName !== "INPUT")) return "";
      try {
        var s=el.selectionStart, e=el.selectionEnd;
        if (s != null && e != null && e > s) return el.value.slice(s, e);
      } catch(x){}
      return "";
    }
    function own(doc){
      var t=fieldText(doc.activeElement).trim();
      if (t) return t;
      var sel=doc.getSelection();
      return sel ? sel.toString().trim() : "";
    }
    function find(doc, depth){
      var active=doc.activeElement, inner=null, t="";
      if (depth < MAX_DEPTH && active && active.tagName === "IFRAME") {
        inner=frameDoc(active);
        if (inner) { t=find(inner, depth + 1); if (t) return t; }
      }
      t=own(doc);
      if (t || depth >= MAX_DEPTH) return t;
      var frames=doc.querySelectorAll("iframe");
      for (var i=0;i<frames.length;i++){
        if (frames[i] === active) continue;
        var d=frameDoc(frames[i]);
        if (d) { t=find(d, depth + 1); if (t) return t; }
      }
      return "";
    }
    window[${JSON.stringify(READ_SELECTION_GLOBAL)}]=function(){
      var reader=window.__workshopReadAloud;
      var text=find(document, 0);
      if (!reader || !text) return false;
      reader.read(text, "selection");
      return true;
    };
  })();
  true;`;
}
