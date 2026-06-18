import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { MapPin, Smartphone, Clock, RotateCcw, MoreHorizontal, X } from "lucide-react";
import { PRIMARY_NAV, MANAGE_NAV, InlineSelect, ListenButton, RecordingIndicator } from "./BirdsHeader";
import { useBirdsScope, useIsNarrow, ALL_LOCS, TIME_OPTIONS } from "./scope";

const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

// The Birds app shell — the single responsive chrome shared by every page.
//  · Web (>=920px): a left sidebar (brand · primary nav · Manage group · scope
//    panel · Listen pinned bottom) beside the page content.
//  · Mobile (<920px): a top filter bar carrying the scope + a bottom tab bar
//    (primary nav + More). Manage lives under More.
// Nav sections come from PRIMARY_NAV / MANAGE_NAV (the single source). When Birds
// runs inside Workshop the platform nav sits above this; standalone, this is the
// whole chrome.
export default function BirdsShell({ children }: { children: React.ReactNode }) {
  const narrow = useIsNarrow(920);
  return (
    <div className={`birds-shell${narrow ? " birds-shell--mobile" : ""}`}>
      {narrow ? <MobileTopBar /> : <Sidebar />}
      <div className="birds-content">{children}</div>
      {narrow && <MobileTabBar />}
    </div>
  );
}

function isActive(pathname: string, path: string): boolean {
  // Home matches only its exact path; the rest match their subtree so detail
  // pages (e.g. a bird page reached from the list) keep their section lit.
  if (path === "/apps/birds") return pathname === "/apps/birds" || pathname === "/";
  return pathname === path || pathname.startsWith(path + "/");
}

function NavLink({ path, label, Icon }: { path: string; label: string; Icon: typeof MapPin }) {
  const { pathname } = useLocation();
  const active = isActive(pathname, path);
  return (
    <Link to={path} className={`birds-nav__item${active ? " is-active" : ""}`} aria-current={active ? "page" : undefined}>
      <Icon size={17} strokeWidth={1.9} />
      <span>{label}</span>
    </Link>
  );
}

function Sidebar() {
  return (
    <aside className="birds-sidebar" aria-label="Birds navigation">
      <div className="birds-brand">
        <Link to="/apps/birds" className="birds-brand__link" aria-label="Birds home">
          {(() => { const H = PRIMARY_NAV[0].Icon; return <H size={22} strokeWidth={1.8} />; })()}
          <span>Birds</span>
        </Link>
        <RecordingIndicator />
      </div>

      <nav className="birds-nav" aria-label="Primary">
        {PRIMARY_NAV.map((s) => <NavLink key={s.path} {...s} />)}
      </nav>

      {!PUBLIC && (
        <>
          <div className="birds-nav__label">Manage</div>
          <nav className="birds-nav" aria-label="Manage">
            {MANAGE_NAV.map((s) => <NavLink key={s.path} {...s} />)}
          </nav>
        </>
      )}

      <div className="birds-side-scope">
        <div className="birds-nav__label">Scope</div>
        <ScopeControls layout="stack" />
      </div>

      {!PUBLIC && <div className="birds-side-foot"><ListenButton /></div>}
    </aside>
  );
}

function MobileTopBar() {
  return (
    <div className="birds-topbar">
      <ScopeControls layout="row" />
      <div className="birds-topbar__end">
        <RecordingIndicator />
        {!PUBLIC && <ListenButton />}
      </div>
    </div>
  );
}

function MobileTabBar() {
  const [moreOpen, setMoreOpen] = useState(false);
  const { pathname } = useLocation();
  const moreActive = MANAGE_NAV.some((s) => isActive(pathname, s.path));
  return (
    <>
      <nav className="birds-tabbar" aria-label="Birds sections">
        {PRIMARY_NAV.map(({ path, label, Icon }) => {
          const active = isActive(pathname, path);
          return (
            <Link key={path} to={path} className={`birds-tab${active ? " is-active" : ""}`} aria-current={active ? "page" : undefined}>
              <Icon size={20} strokeWidth={1.9} /><span>{label}</span>
            </Link>
          );
        })}
        {!PUBLIC && (
          <button type="button" className={`birds-tab${moreActive ? " is-active" : ""}`}
            aria-haspopup="dialog" aria-expanded={moreOpen} onClick={() => setMoreOpen(true)}>
            <MoreHorizontal size={20} strokeWidth={1.9} /><span>More</span>
          </button>
        )}
      </nav>
      {moreOpen && (
        <div className="birds-sheet-backdrop" onClick={() => setMoreOpen(false)}>
          <div className="birds-sheet" role="dialog" aria-label="More" onClick={(e) => e.stopPropagation()}>
            <div className="birds-sheet__head">
              <span className="birds-nav__label" style={{ margin: 0 }}>Manage</span>
              <button type="button" className="btn sm" aria-label="Close" onClick={() => setMoreOpen(false)}><X size={16} /></button>
            </div>
            <nav className="birds-nav" aria-label="Manage" onClick={() => setMoreOpen(false)}>
              {MANAGE_NAV.map((s) => <NavLink key={s.path} {...s} />)}
            </nav>
          </div>
        </div>
      )}
    </>
  );
}

// The scope pickers (Location · Device · Time + Reset), shared by the sidebar
// (stacked) and the mobile top bar (inline row). Reads/writes the persistent
// scope; editing a place/device jumps to its manage page.
function ScopeControls({ layout }: { layout: "stack" | "row" }) {
  const nav = useNavigate();
  const { sources, selected, deviceTok, devices, days, setSource, setDevice, setDays, reset } = useBirdsScope();
  if (sources.length === 0) return null;
  return (
    <div className={`birds-scope birds-scope--${layout}`}>
      <InlineSelect
        ariaLabel="Location"
        icon={<MapPin size={15} />}
        value={selected?.key || ""}
        options={[
          { value: ALL_LOCS, label: "All locations", listAll: true },
          ...sources.map((s) => ({ value: s.key, label: s.name, editable: s.kind === "loc" })),
        ]}
        onChange={(v) => { if (v === ALL_LOCS) nav("/apps/birds/locations"); else setSource(v); }}
        onEdit={(v) => { const s = sources.find((x) => x.key === v); if (s?.id) nav(`/apps/birds/locations?id=${s.id}`); }}
        editTitle="Edit this place (name, coordinates, devices)"
        onListAll={() => nav("/apps/birds/locations")}
        listAllTitle="See all locations"
      />
      {selected?.kind === "loc" && devices.length > 0 && (
        <InlineSelect
          ariaLabel="Device"
          icon={<Smartphone size={15} />}
          value={deviceTok}
          options={[{ value: "", label: "All devices", listAll: true }, ...devices.map((dv) => ({ value: dv.token, label: dv.name || "Untitled device", editable: true }))]}
          onChange={(v) => setDevice(v)}
          onEdit={(v) => nav(`/apps/birds/devices?token=${encodeURIComponent(v)}`)}
          editTitle="Edit this device"
          onListAll={() => nav("/apps/birds/devices")}
          listAllTitle="See all devices"
        />
      )}
      <InlineSelect
        ariaLabel="Time"
        icon={<Clock size={15} />}
        value={String(days)}
        options={TIME_OPTIONS.map((o) => ({ value: String(o.value), label: o.label }))}
        onChange={(v) => setDays(Number(v))}
      />
      <button type="button" className="birds-scope__reset" title="Reset scope" aria-label="Reset scope" onClick={reset}>
        <RotateCcw size={14} />
      </button>
    </div>
  );
}
