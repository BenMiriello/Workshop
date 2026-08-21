import { useCallback, useEffect, useState } from "react";
import { Segmented, usePoll } from "@workshop/ui";
import BirdsHeader from "./BirdsHeader";
import { useBirdsScope } from "./scope";
import { jget, pct, SpeciesLink, BirdThumb } from "./ui";
import SpeciesRow, { type SpeciesAgg, type TrustStatus } from "./SpeciesRow";

// The review surface: every species for the current scope, segmented by trust
// status (Confirmed / Suggested / Rejected, default Confirmed). This is the ONLY
// place place-level status review happens — Home shows confirmed-only "Top birds".
// Uses the global scope; status review applies to a location (the yard auto-files,
// so a yard scope just lists species).

export default function BirdsReview() {
  const { selected, deviceTok, nb } = useBirdsScope();
  const [status, setStatus] = useState<TrustStatus>("confirmed");
  const [sp, setSp] = useState<SpeciesAgg[] | null>(null);
  const [loc, setLoc] = useState<{ lat?: number | null; lon?: number | null }>({});
  const [err, setErr] = useState<string | null>(null);
  const isLoc = selected?.kind === "loc";
  const locId = isLoc ? selected!.id : undefined;
  const dev = deviceTok ? `&device=${encodeURIComponent(deviceTok)}` : "";

  const load = useCallback(() => {
    if (isLoc && locId) {
      Promise.all([
        jget<{ species: SpeciesAgg[] }>(`/location/species?location_id=${locId}${nb}${dev}&status=${status}`),
        jget<{ lat?: number | null; lon?: number | null }>(`/location/summary?location_id=${locId}${nb}${dev}`),
      ]).then(([s, sm]) => { setSp(s.species); setLoc({ lat: sm.lat, lon: sm.lon }); }).catch((e) => setErr(String(e)));
    } else {
      jget<{ species: SpeciesAgg[] }>(`/species?_=1${nb}`).then((s) => setSp(s.species)).catch((e) => setErr(String(e)));
    }
  }, [isLoc, locId, nb, dev, status]);
  useEffect(() => { setSp(null); setErr(null); void load(); }, [load]);
  usePoll(load, 8000, { immediate: false });

  const label = status[0].toUpperCase() + status.slice(1);
  const emptyMsg = !isLoc ? "Nothing yet."
    : status === "suggested" ? "No suggestions right now."
    : status === "rejected" ? "Nothing rejected."
    : "Nothing confirmed yet.";

  return (
    <div>
      <BirdsHeader title="Birds" />

      {isLoc && (
        <div style={{ marginBottom: ".75rem" }}>
          <Segmented<TrustStatus> ariaLabel="Review status" value={status} onChange={setStatus}
            options={[
              { value: "confirmed", label: "Confirmed" },
              { value: "suggested", label: "Suggested" },
              { value: "rejected", label: "Rejected" },
            ]} />
        </div>
      )}

      {err && !sp && <p className="error">Could not load: {err}</p>}
      {!sp && !err && <p className="muted">Loading…</p>}

      {sp && (
        <section className="card" style={{ padding: "1rem" }}>
          <h3 style={{ margin: "0 0 .4rem", fontSize: "1rem" }}>{isLoc ? label : "Species"} ({sp.length})</h3>
          {sp.length === 0 ? (
            <p className="muted">{emptyMsg}</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {isLoc
                ? sp.map((s) => (
                    <SpeciesRow key={s.scientific_name} s={s} locId={locId}
                      status={status}
                      lat={loc.lat} lon={loc.lon} onChanged={load} />
                  ))
                : sp.map((s) => (
                    <SpeciesLink key={s.scientific_name} sci={s.scientific_name} common={s.common_name}>
                      <div style={{ display: "flex", alignItems: "center", gap: ".7rem", padding: ".5rem 0", borderTop: "1px solid var(--border)" }}>
                        <BirdThumb sci={s.scientific_name} common={s.common_name} />
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <span style={{ display: "block" }}>{s.common_name}</span>
                          <span className="muted" style={{ fontStyle: "italic", fontSize: ".78rem" }}>{s.scientific_name}</span>
                        </span>
                        <span className="muted" style={{ fontSize: ".8rem", textAlign: "right" }}>{s.count}×<br />best {pct(s.max_conf)}</span>
                      </div>
                    </SpeciesLink>
                  ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
