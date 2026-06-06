import { Link } from "react-router-dom";
import { ChevronLeft, SlidersHorizontal, Bird, Filter } from "lucide-react";
import { useShowNonBird } from "./settings";

// Settings for the Birds app. The non-bird filter is display-only: BirdNET keeps
// detecting and storing every class (so nothing is lost and the toggle is
// reversible after the fact) — this just decides whether non-bird hits show up
// in the live feed, the per-location species lists, and the activity charts.

const NON_BIRD_EXAMPLES = [
  "Human (voice, whistle, non-vocal)", "Engine", "Siren", "Power tools",
  "Fireworks", "Gun", "Dog", "Coyote / wolf", "Frogs & toads", "Crickets",
  "Environmental / Noise",
];

export default function BirdSettings() {
  const [showNonBird, setShowNonBird] = useShowNonBird();

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".75rem" }}>
        <Link to="/apps/birds" className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Yard Birds
        </Link>
      </div>
      <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
        <SlidersHorizontal size={22} strokeWidth={1.8} /> Bird settings
      </h2>

      <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
          <Filter size={17} strokeWidth={1.8} /> Non-bird detections
        </h3>

        <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
          <span style={{ paddingTop: ".15rem" }}>
            <Switch on={showNonBird} onChange={setShowNonBird} />
          </span>
          <span>
            <span style={{ fontWeight: 500 }}>Show non-bird detections</span>
            <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
              {showNonBird
                ? "Non-bird classes are shown alongside birds, tagged so you can tell them apart."
                : "Only birds are shown. Non-bird classes are still detected and stored — turn this on to reveal them."}
            </span>
          </span>
        </label>

        <div style={{ marginTop: ".9rem" }}>
          <div className="muted" style={{ fontSize: ".75rem", marginBottom: ".35rem", display: "flex", alignItems: "center", gap: ".3rem" }}>
            <Bird size={13} /> Counted as non-bird
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
            {NON_BIRD_EXAMPLES.map((n) => (
              <span key={n} className="pill muted" style={{ fontSize: ".78rem" }}>{n}</span>
            ))}
          </div>
        </div>
      </section>

      <p className="muted" style={{ fontSize: ".82rem" }}>
        Detection always runs on everything the microphone hears — this setting only
        changes what is displayed, and applies everywhere in the Birds app.
      </p>
    </div>
  );
}

// Small accessible toggle switch (no external dep, matches the app's pill style).
function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      style={{
        width: 42, height: 24, borderRadius: 999, position: "relative", cursor: "pointer",
        border: "1px solid var(--border,#2a3344)", padding: 0,
        background: on ? "#1f6feb" : "var(--bg-elev,#1a2230)", transition: "background .15s",
      }}
    >
      <span style={{
        position: "absolute", top: 2, left: on ? 20 : 2, width: 18, height: 18, borderRadius: "50%",
        background: "#fff", transition: "left .15s",
      }} />
    </button>
  );
}
