import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bird, Mic, Square, SlidersHorizontal, Smartphone, Loader2, X, ChevronDown } from "lucide-react";
import { useRecorder } from "./recorder";
import { jget } from "./ui";

// The Birds app's top toolbar, shared across pages so the place/device filters,
// Listen, and Settings sit in one row after the title. The title is "Birds", or
// the species name on a species page. Order: title, filters, Listen, Settings.

const PUBLIC = import.meta.env.VITE_PUBLIC === "1";
type KnownDevice = { token: string; name: string };

export default function BirdsHeader({ title, children }: { title?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="birds-toolbar">
      <h2 className="birds-toolbar__title"><Bird size={22} strokeWidth={1.8} /> {title ?? "Birds"}</h2>
      {children}
      {!PUBLIC && <ListenButton />}
      {!PUBLIC && (
        <Link to="/apps/birds/settings" className="btn sm birds-toolbar__end" title="Settings">
          <SlidersHorizontal size={15} /> <span className="birds-label">Settings</span>
        </Link>
      )}
    </div>
  );
}

// An inline, text-style selector: the current value reads as plain text behind an
// icon (not a chunky control); clicking the icon or text opens the option list
// below it. Used for the place and device filters.
export function InlineSelect({ icon, value, options, onChange, ariaLabel }: {
  icon: React.ReactNode; value: string; ariaLabel: string;
  options: { value: string; label: string }[]; onChange: (v: string) => void;
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
            <li key={o.value}>
              <button type="button" role="option" aria-selected={o.value === value}
                className={o.value === value ? "is-selected" : ""}
                onClick={() => { onChange(o.value); setOpen(false); }}>{o.label}</button>
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
      <button className="btn sm" title="Listen on this device" aria-label="Listen on this device"
        onClick={rec.start} disabled={rec.analyzerUp === false}>
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
