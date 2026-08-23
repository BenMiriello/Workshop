// The phone's place menu: a DROPDOWN under bar one's tile.
//
// Not a drawer and not a sheet. An earlier left slide-over was replaced during
// design and the mockup still carries a dead `wsDrawerIn` keyframe from it; do
// not build one back. Geometry, to the pixel:
// docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 3.
//
// Three things about it are deliberate and read as bugs if you do not know:
//
//   1. THE SCRIM STARTS BELOW BAR ONE. It never covers the bar, so the tile you
//      opened the menu with stays visible and stays live. That is also why this
//      file needs its own outside-pointerdown listener: the scrim cannot catch
//      a tap on the title or the avatar, and those have to close the menu.
//   2. IT RISES 10px AND FADES. It does not slide in from an edge, because it
//      is anchored to a control rather than to the side of the screen.
//   3. THE PANEL IS ANCHORED UNDER THE TILE, at the left edge, not centred and
//      not under the title. The tile is the trigger, so the tile is the anchor.
//      (The prose docs say "under the title"; the mockup is right, and the spec
//      file's section 7 item 2 records that.)
//
// WHY THIS IS NOT THE KIT'S `Menu`. The kit primitive is a compact list of
// choices measured against its trigger: no scrim, a 16rem cap, 8px+scale pop,
// rows sized for a toolbar. This is a 300px navigation panel over a scrim with
// 48/52px touch rows. Nothing but the behaviour was shared, so the behaviour is
// what is reimplemented here: aria-expanded on the trigger, a focus trap,
// Escape to close with focus restored, arrow keys between rows, and a close on
// any pointerdown outside. It is a superset of what `Menu` gave us (that one
// lets Tab walk out; here Tab would walk out UNDER the scrim, so it wraps).

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AppGlyph } from "../../lib/appIcon";
import { menuFocusMove, placeMenuSections, type PlaceMenuRow } from "./placeMenu";
import type { Shell } from "./useShell";

function PlaceRow({
  shell,
  row,
  platform,
  onPicked,
}: {
  shell: Shell;
  row: PlaceMenuRow;
  platform: boolean;
  onPicked: (viaKeyboard: boolean) => void;
}) {
  const { place, current } = row;
  const Icon = place.Icon;
  return (
    // A ROW IS A LINK, exactly as a dock tile is: middle-click, copy link and
    // hover preview all work, and the one intercepted case is the place you are
    // already in. The dock toggles its page column there; a phone has no page
    // column, so the honest answer is to close and stay put.
    <Link
      to={shell.hrefFor(place)}
      role="menuitem"
      className={
        "ws-placemenu__row" +
        (platform ? " ws-placemenu__row--platform" : "") +
        (current ? " is-current" : "")
      }
      aria-current={current ? "page" : undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        if (current) e.preventDefault();
        // detail === 0 means Enter or Space, not a pointer. See `close` below
        // for why the menu cares.
        onPicked(e.detail === 0);
      }}
    >
      <span className="ws-placemenu__tile" aria-hidden>
        {/* A platform place imports its glyph; an app DECLARES one and AppGlyph
            resolves it. `textSize` is the 32px tile asking for the mockup's
            14px letter rather than 0.62 of a 16px icon. */}
        {Icon ? (
          <Icon size={16} />
        ) : (
          <AppGlyph icon={place.icon} name={place.name} size={16} textSize="var(--fs-base)" />
        )}
      </span>
      <span className="ws-placemenu__name">{place.name}</span>
    </Link>
  );
}

export function PlaceMenu({ shell }: { shell: Shell }) {
  const { place } = shell;
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // Whether this open is being driven by the keyboard. Focus is only sent back
  // to the trigger when it is: a programmatic .focus() after a tap re-triggers
  // the global :focus-visible ring, which reads as a border the app stuck on
  // the tile for no reason anyone can see. Same rule as the kit's Menu.
  const viaKeyboard = useRef(false);
  const id = useId();

  const sections = placeMenuSections(shell.places, place.id);

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
    viaKeyboard.current = false;
  }, []);

  useEffect(() => {
    if (!open) return;
    // pointerdown, not click: a menu that survives until mouseup can be
    // re-triggered by the same press that was meant to dismiss it.
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target)) return;
      // The trigger's own click toggles; closing here too would reopen it.
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    // Focus the PANEL, not its first row: focusing a row draws a ring around an
    // entry nobody chose. ArrowDown from here still lands on the first row.
    panelRef.current?.focus();
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close(true);
      return;
    }
    const rows = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    const next = menuFocusMove(e.key, e.shiftKey, rows.indexOf(document.activeElement as HTMLElement), rows.length);
    if (next === null) return;
    e.preventDefault();
    viaKeyboard.current = true;
    rows[next]?.focus();
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="ws-bar1__tile"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={`${place.name}. Switch place`}
        onClick={(e) => {
          viaKeyboard.current = e.detail === 0;
          setOpen((v) => !v);
        }}
      >
        {place.Icon ? (
          <place.Icon size={17} aria-hidden />
        ) : (
          <AppGlyph icon={place.icon} name={place.name} size={17} textSize="var(--fs-md)" />
        )}
      </button>

      {open && (
        <>
          {/* Starts below bar one and runs to the bottom of the screen. Tapping
              it closes; the listener above is what actually does that, and this
              handler is here so the element reads as the control it is. */}
          <div className="ws-placemenu__scrim" aria-hidden onClick={() => close(false)} />
          <div
            id={id}
            ref={panelRef}
            role="menu"
            tabIndex={-1}
            aria-label="Places"
            className="ws-placemenu"
            onKeyDown={onKeyDown}
          >
            {sections.platform.map((row) => (
              <PlaceRow
                key={row.place.id}
                shell={shell}
                row={row}
                platform
                onPicked={close}
              />
            ))}
            {sections.apps.length > 0 && (
              <span className="ws-placemenu__group" role="presentation">
                Apps
              </span>
            )}
            {sections.apps.map((row) => (
              <PlaceRow
                key={row.place.id}
                shell={shell}
                row={row}
                platform={false}
                onPicked={close}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}
