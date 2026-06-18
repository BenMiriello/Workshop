import { useEffect, useRef, useState } from "react";
import { Bird, Mic, Square, SlidersHorizontal, Smartphone, Loader2, X, ChevronDown, ChevronRight, Pencil, Waypoints, CalendarDays, AudioLines, MapPin } from "lucide-react";
import { useRecorder } from "./recorder";
import { StatusPill } from "../../../ui";
import { jget, isRecording } from "./ui";

// Navigation for the Birds app, the SINGLE source of navigable sections. The
// responsive chrome (BirdsShell) renders these; pages never hand-roll section
// links. To add a page: add a <Route> in App.tsx AND one entry to the right list.
//   · PRIMARY_NAV — the four destinations (sidebar top / mobile bottom tabs).
//   · MANAGE_NAV  — secondary lists (sidebar "Manage" group / mobile "More").
// Home is the first primary item; its Bird glyph doubles as the shell brand mark.
export type NavItem = { path: string; label: string; Icon: typeof Bird };
export const PRIMARY_NAV: NavItem[] = [
  { path: "/apps/birds", label: "Home", Icon: Bird },
  { path: "/apps/birds/forecast", label: "Forecast", Icon: CalendarDays },
  { path: "/apps/birds/atlas", label: "Atlas", Icon: Waypoints },
];
export const MANAGE_NAV: NavItem[] = [
  { path: "/apps/birds/locations", label: "Locations", Icon: MapPin },
  { path: "/apps/birds/devices", label: "Devices", Icon: Smartphone },
  { path: "/apps/birds/recordings", label: "Recordings", Icon: AudioLines },
  { path: "/apps/birds/settings", label: "Settings", Icon: SlidersHorizontal },
];

type KnownDevice = { token: string; name: string };

// An in-content page title. Navigation and scope live in the shell now, so this
// is just the page's heading (and an optional inline slot for page-local actions).
export default function BirdsHeader({ title, children }: { title?: React.ReactNode; children?: React.ReactNode }) {
  if (!title && !children) return null;
  return (
    <div className="birds-pagehead">
      {title && <h2 className="birds-pagehead__title">{title}</h2>}
      {children}
    </div>
  );
}

// A glanceable "something is listening right now" badge, shown on every birds page
// (including the public site). Polls the device list and lights up while any device
// is actively posting — primarily the always-on yard Pi. Animated dot = the only
// thing on the page that animates, per the design system's "running" status.
export function RecordingIndicator() {
  const rec = useRecorder();
  const [others, setOthers] = useState(0);
  useEffect(() => {
    let alive = true;
    // Count OTHER devices by last_seen (the always-on yard Pi only ever bumps it
    // by posting audio, so freshness == recording). The current browser is excluded
    // here because its last_seen is also bumped just by loading the app; its true
    // recording state comes from the local recorder below.
    const load = () => jget<{ devices: { token: string; last_seen: string | null }[] }>("/devices")
      .then((d) => { if (alive) setOthers(d.devices.filter((x) => x.token !== rec.device.token && isRecording(x.last_seen)).length); })
      .catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => { alive = false; clearInterval(t); };
  }, [rec.device.token]);
  const count = others + (rec.recording ? 1 : 0);
  if (count < 1) return null;
  return (
    <StatusPill status="running" className="birds-live">
      {count === 1 ? "Recording" : `${count} recording`}
    </StatusPill>
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
