// The bar a window shows while another of this browser's Workshop windows is
// dictating (elsewhere.ts): where those words are going, Move here to carry on
// dictating to the same place from this window, and Stop.

import { useSyncExternalStore } from "react";
import { ArrowDownToLine, Square } from "lucide-react";
import { Button } from "@workshop/ui";
import { CLIPBOARD, fromRecent } from "./destination";
import { dictation } from "./dictation";
import { elsewhere } from "./elsewhere";
import { KIND_ICON } from "./PillMenu";
import { keepFocus } from "./press";

export function ElsewhereBar() {
  const other = useSyncExternalStore(elsewhere.subscribe, elsewhere.get, elsewhere.get);
  if (!other || dictation.busy) return null;
  const Icon = KIND_ICON[other.kind];
  const moveHere = () => {
    elsewhere.stop(other.tab);
    void dictation.start(null, other.place ? fromRecent(other.place) : CLIPBOARD);
  };
  return (
    <div className="ws-dictpill" role="region" aria-label="Dictation in another window"
      onPointerDown={keepFocus} onMouseDown={keepFocus}>
      <span className="ws-dictpill__state"><span className="ws-dictpill__dot" aria-hidden /></span>
      <span className="ws-dictpill__where ws-dictpill__other" title={`Another window is dictating into ${other.name}`}>
        <Icon aria-hidden />
        <span className="ws-dictpill__name">{other.name}</span>
        <span className="ws-dictpill__at">other window</span>
      </span>
      <Button size="sm" variant="ghost" onClick={moveHere} aria-label="Move here"
        title="Dictate to the same place from this window">
        <ArrowDownToLine aria-hidden /> <span className="ws-dictpill__label">Move here</span>
      </Button>
      <Button size="sm" variant="ghost" icon onClick={() => elsewhere.stop(other.tab)} aria-label="Stop" title="Stop">
        <Square aria-hidden />
      </Button>
    </div>
  );
}
