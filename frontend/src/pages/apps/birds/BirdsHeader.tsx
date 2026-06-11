import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Bird, Mic, Square, SlidersHorizontal, Smartphone, Loader2, X, ChevronDown, ChevronRight, Pencil, Waypoints, CalendarDays, AudioLines } from "lucide-react";
import { useRecorder } from "./recorder";
import { jget } from "./ui";

// The Birds app's top toolbar, shared across EVERY birds page. It draws the section
// nav with the current section highlighted, so navigation is identical everywhere
// and "you are here" is always clear.
//
// NAVIGATION CONTRACT (keep it hard to break):
//   1. Every page under pages/apps/birds renders <BirdsHeader> as its top element.
//      The home dashboard passes its place/device selectors as children.
//   2. BIRDS_NAV is the SINGLE source of navigable sections. To add a page: add a
//      <Route> in App.tsx AND one entry here. Don't hand-roll a birds header or
//      scatter one-off section links in page bodies.
//   3. The bird icon is always Home (the dashboard).
export const BIRDS_NAV: { path: string; label: string; Icon: typeof Bird }[] = [
  { path: "/apps/birds/forecast", label: "Forecast", Icon: CalendarDays },
  { path: "/apps/birds/atlas", label: "Atlas", Icon: Waypoints },
  { path: "/apps/birds/recordings", label: "Recordings", Icon: AudioLines },
  { path: "/apps/birds/settings", label: "Settings", Icon: SlidersHorizontal },
];

const PUBLIC = import.meta.env.VITE_PUBLIC === "1";
type KnownDevice = { token: string; name: string };

export default function BirdsHeader({ title, children }: { title?: React.ReactNode; children?: React.ReactNode }) {
  const { pathname } = useLocation();
  const onHome = pathname === "/apps/birds";
  return (
    <div className="birds-toolbar">
      <h2 className="birds-toolbar__title">
        <Link to="/apps/birds" className={`birds-toolbar__home${onHome ? " is-active" : ""}`}
          title="Birds home" aria-label="Birds home" aria-current={onHome ? "page" : undefined}>
          <Bird size={22} strokeWidth={1.8} />
        </Link>
        {title && <span>{title}</span>}
      </h2>
      {children}
      {!PUBLIC && (
        <nav className="birds-toolbar__nav" aria-label="Birds sections">
          <ListenButton />
          {BIRDS_NAV.map((s) => {
            const active = pathname === s.path;
            return (
              <Link key={s.path} to={s.path} className={`btn sm${active ? " is-active" : ""}`}
                title={s.label} aria-current={active ? "page" : undefined}>
                <s.Icon size={15} /> <span className="birds-label">{s.label}</span>
              </Link>
            );
          })}
        </nav>
      )}
    </div>
  );
}

// An inline, text-style selector: the current value reads as plain text behind an
// icon (not a chunky control); clicking the icon or text opens the option list
// below it. Used for the place and device filters. When `onEdit` is given, each
// editable option carries a pencil flush to the far right of the menu that opens
// that item's editor (the place editor, the device editor) without selecting it.
export function InlineSelect({ icon, value, options, onChange, ariaLabel, onEdit, editTitle, onListAll, listAllTitle }: {
  icon: React.ReactNode; value: string; ariaLabel: string;
  options: { value: string; label: string; editable?: boolean; listAll?: boolean }[]; onChange: (v: string) => void;
  onEdit?: (value: string) => void; editTitle?: string;
  onListAll?: (value: string) => void; listAllTitle?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const cur = options.find((o) => o.value === value);
  return (
    <div className="birds-inline" ref={ref}>
      <button type="button" className="birds-inline__trigger" aria-haspopup="listbox" aria-expanded={open}
        aria-label={ariaLabel} onClick={() => setOpen((v) => !v)}>
        {icon}
        <span className="birds-inline__val">{cur?.label ?? ""}</span>
        <ChevronDown size={13} className="muted" />
      </button>
      {open && (
        <ul className="birds-inline__menu" role="listbox" aria-label={ariaLabel}>
          {options.map((o) => (
            <li key={o.value} className="birds-inline__row">
              <button type="button" role="option" aria-selected={o.value === value}
                className={o.value === value ? "is-selected" : ""}
                onClick={() => { onChange(o.value); setOpen(false); }}>{o.label}</button>
              {onEdit && o.editable && (
                <button type="button" className="birds-inline__edit" title={editTitle ?? "Edit"} aria-label={`${editTitle ?? "Edit"}: ${o.label}`}
                  onClick={(e) => { e.stopPropagation(); setOpen(false); onEdit(o.value); }}>
                  <Pencil size={13} />
                </button>
              )}
              {onListAll && o.listAll && (
                <button type="button" className="birds-inline__edit" title={listAllTitle ?? "See all"} aria-label={`${listAllTitle ?? "See all"}: ${o.label}`}
                  onClick={(e) => { e.stopPropagation(); setOpen(false); onListAll(o.value); }}>
                  <ChevronRight size={15} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Listen on this device: an icon only (name on hover), with an optional "adopt an
// existing device" picker for a brand-new browser. Reachable from every page.
export function ListenButton() {
  const rec = useRecorder();
  const [adopting, setAdopting] = useState(false);
  const [devices, setDevices] = useState<KnownDevice[] | null>(null);

  if (rec.recording) {
    return <button className="btn sm" title="Stop listening" aria-label="Stop listening" onClick={rec.stop}><Square size={16} /></button>;
  }
  if (rec.wasListening) {
    return <button className="btn sm" title="Resume listening" aria-label="Resume listening" onClick={rec.start}><Mic size={16} /></button>;
  }

  const openAdopt = () => {
    setAdopting(true);
    if (!devices) {
      jget<{ devices: KnownDevice[] }>("/devices")
        .then((r) => setDevices(r.devices.filter((d) => d.token !== rec.device.token)))
        .catch(() => setDevices([]));
    }
  };

  if (adopting) {
    return (
      <span style={{ display: "inline-flex", gap: ".4rem", alignItems: "center", flexWrap: "wrap" }}>
        {devices === null
          ? <Loader2 size={14} className="muted" style={{ animation: "spin 1s linear infinite" }} />
          : devices.length === 0
            ? <span className="muted" style={{ fontSize: ".85rem" }}>No other registered devices.</span>
            : devices.map((d) => (
                <button key={d.token} className="btn sm" onClick={() => { rec.adoptDevice(d.token, d.name); setAdopting(false); rec.start(); }}>
                  <Smartphone size={14} /> {d.name}
                </button>
              ))
        }
        <button className="btn sm" title="Cancel" onClick={() => setAdopting(false)}><X size={14} /></button>
      </span>
    );
  }

  return (
    <span style={{ display: "inline-flex", gap: ".4rem", alignItems: "center" }}>
      {/* Never hard-disable the primary action on a health probe: a single failed
          /analyze/health check used to leave this button permanently dead until a
          manual reload. Always let the tap through; if the analyzer is genuinely
          down, start() surfaces the error and the live panel shows a notice. */}
      <button className="btn sm"
        title={rec.analyzerUp === false ? "Listen (analyzer may be offline)" : "Listen on this device"}
        aria-label="Listen on this device" onClick={rec.start}>
        <Mic size={16} />
      </button>
      {!rec.device.name && (
        <button className="btn sm" title="This is a new browser. Adopt an existing device?" onClick={openAdopt}>
          <Smartphone size={14} />
        </button>
      )}
    </span>
  );
}
