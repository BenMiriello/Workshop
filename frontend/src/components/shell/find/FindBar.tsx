// Find in page: a bar at the bottom of the content region (so it never covers
// the dock), searching everything on screen: the content region, the chat
// beside it, and the app frames in them (collect.ts says how).
//
// It is a row of the content region's own column, after <main>, so it takes
// space and the page ends above it instead of running underneath. It grows to
// its height on open and shrinks away on close, so the page moves with it
// rather than jumping. It belongs to the page it was opened on (see
// FindProvider).

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { Button, Input } from "@workshop/ui";
import { clearPaint, collectRanges, exposeMatch, isHiddenMatch, paint, reveal, searchScopes } from "./collect";
import { countLabel, stepIndex } from "./matchText";
import { useFind } from "./FindProvider";

const REFRESH_MS = 250;
/** Longer than the bar's close transition, so it is unmounted only once gone. */
const EXIT_MS = 320;
/** Longer than a disclosure takes to open, so a match it hid is in place. */
const EXPOSE_MS = 320;
/** A hidden match opens what hides it only once typing pauses, so the letters
 *  on the way to a word do not open a block each. Stepping opens at once. */
const TYPING_SETTLE_MS = 600;

const contentRoot = () => document.querySelector(".ws-app__body > main");

/** The scroller walk stops at the region the match is in. In a frame there is
 *  none: it runs up to the frame's own page. */
function regionOf(range: Range): Element | null {
  if (range.startContainer.ownerDocument !== document) return null;
  return range.startContainer.parentElement?.closest(".ws-beside") ?? contentRoot();
}

export function FindBar() {
  const { open, focusTick } = useFind();
  const [mounted, setMounted] = useState(open);
  const [grown, setGrown] = useState(false);

  useEffect(() => {
    if (open) {
      setMounted(true);
      // Two frames: the bar has to be painted at height 0 before it can grow.
      let second = 0;
      const first = requestAnimationFrame(() => {
        second = requestAnimationFrame(() => setGrown(true));
      });
      return () => {
        cancelAnimationFrame(first);
        cancelAnimationFrame(second);
      };
    }
    setGrown(false);
    const timer = window.setTimeout(() => setMounted(false), EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [open]);

  return open || mounted ? <Bar open={open} grown={grown} focusTick={focusTick} /> : null;
}

function Bar({ open, grown, focusTick }: { open: boolean; grown: boolean; focusTick: number }) {
  const { closeFind, query, setQuery, scope } = useFind();
  const [state, setState] = useState({ current: -1, count: 0 });
  const ranges = useRef<Range[]>([]);
  const current = useRef(-1);
  const wrap = useRef<HTMLDivElement>(null);
  const exposing = useRef(0);

  const show = useCallback((index: number, scroll: boolean, settle = 0) => {
    current.current = index;
    paint(ranges.current, index);
    setState({ current: index, count: ranges.current.length });
    const range = ranges.current[index];
    if (!scroll || !range) return;
    window.clearTimeout(exposing.current);
    const open = () => {
      const now = ranges.current[current.current];
      if (!now || !exposeMatch(now)) return;
      exposing.current = window.setTimeout(() => {
        const settled = ranges.current[current.current];
        if (settled) reveal(settled, regionOf(settled));
      }, EXPOSE_MS);
    };
    if (settle) exposing.current = window.setTimeout(open, settle);
    else open();
    reveal(range, regionOf(range));
  }, []);

  useEffect(() => () => window.clearTimeout(exposing.current), []);

  const search = useCallback(
    (q: string, keep: boolean) => {
      const root = contentRoot();
      ranges.current = root ? collectRanges(searchScopes(root), q) : [];
      const last = ranges.current.length - 1;
      if (keep) {
        show(Math.min(current.current, last), false);
        return;
      }
      // A new search lands on the first match already in sight, if any.
      const inSight = ranges.current.findIndex((r) => !isHiddenMatch(r));
      show(inSight >= 0 ? inSight : last < 0 ? -1 : 0, true, TYPING_SETTLE_MS);
    },
    [show],
  );

  useEffect(() => {
    if (open) search(query, false);
    else clearPaint();
  }, [open, query, scope, search]);

  useEffect(() => {
    const root = contentRoot();
    if (!open || !root || !query) return;
    let timer = 0;
    let observers: MutationObserver[] = [];
    const watch = () => {
      observers.forEach((o) => o.disconnect());
      observers = searchScopes(root).map(({ root: r }) => {
        const o = new MutationObserver(refreshSoon);
        o.observe(r, { childList: true, subtree: true, characterData: true });
        return o;
      });
    };
    function refreshSoon() {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        search(query, true);
        watch();
      }, REFRESH_MS);
    }
    // A frame finishing a load is a new document: search it and watch it.
    const onLoad = (e: Event) => {
      if (e.target instanceof HTMLIFrameElement) refreshSoon();
    };
    watch();
    document.addEventListener("load", onLoad, true);
    return () => {
      observers.forEach((o) => o.disconnect());
      document.removeEventListener("load", onLoad, true);
      window.clearTimeout(timer);
    };
  }, [open, query, scope, search]);

  useEffect(() => clearPaint, []);

  // Taking the keyboard from wherever it is, including an app frame, which
  // keeps focus unless it is told to let go.
  useLayoutEffect(() => {
    if (!open) return;
    const input = wrap.current?.querySelector("input");
    if (!input) return;
    const take = () => {
      if (document.activeElement instanceof HTMLIFrameElement) document.activeElement.blur();
      window.focus();
      input.focus({ preventScroll: true });
      input.select();
    };
    take();
    const retry = requestAnimationFrame(() => {
      if (document.activeElement !== input) take();
    });
    return () => cancelAnimationFrame(retry);
  }, [open, focusTick]);

  const step = (dir: 1 | -1) => {
    const next = stepIndex(current.current, ranges.current.length, dir);
    if (next >= 0) show(next, true);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "g") return;
      e.preventDefault();
      step(e.shiftKey ? -1 : 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div
      className={"ws-findbar" + (grown && open ? " is-open" : "")}
      ref={wrap}
      role="search"
      aria-label="Find in page"
      aria-hidden={!open}
    >
      <div className="ws-findbar__inner">
        <Input
          type="text"
          value={query}
          placeholder="Find in page"
          aria-label="Find in page"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          enterKeyHint="search"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              step(e.shiftKey ? -1 : 1);
            } else if (e.key === "Escape") {
              e.preventDefault();
              closeFind();
            }
          }}
        />
        <span className="ws-findbar__count" aria-live="polite">
          {countLabel(state.current, state.count, query)}
        </span>
        <Button variant="ghost" icon aria-label="Previous match" disabled={!state.count} onClick={() => step(-1)}>
          <ChevronUp size={16} aria-hidden />
        </Button>
        <Button variant="ghost" icon aria-label="Next match" disabled={!state.count} onClick={() => step(1)}>
          <ChevronDown size={16} aria-hidden />
        </Button>
        <Button variant="ghost" icon aria-label="Close find" onClick={closeFind}>
          <X size={16} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
