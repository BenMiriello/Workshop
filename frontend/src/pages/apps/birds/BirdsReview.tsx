import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Button, Notice, Segmented, Skeleton, StateBlock } from "@workshop/ui";
import BirdsHeader from "./BirdsHeader";
import { useBirdsScope } from "./scope";
import { pct, SpeciesLink, BirdThumb } from "./ui";
import {
  birdsKeys, fetchLocationSpecies, fetchLocationSummary, fetchSpecies,
} from "./queries";
import SpeciesRow, { type SpeciesAgg, type TrustStatus } from "./SpeciesRow";

// The review surface: every species for the current scope, segmented by trust
// status (Confirmed / Suggested / Rejected, default Confirmed). This is the ONLY
// place place-level status review happens — Home shows confirmed-only "Top birds".
// Uses the global scope; status review applies to a location (the yard auto-files,
// so a yard scope just lists species).
//
// THE STATUS TAB NO LONGER EMPTIES THE PAGE. This read was a branching
// `Promise.all` behind an 8s `usePoll` with `immediate: false`, plus
// `useEffect(() => { setSp(null); load(); }, [load])` to make the first read
// happen at all -- so every tab switch, every place change and every non-bird
// toggle threw the list away and showed "Loading…" for a full round trip. The
// status is in the KEY now (`queries.ts`), which is also what stops a slow
// `suggested` response from painting over a `confirmed` list the operator has
// already switched back to, and the previous list stays up while the next one
// loads. The companion effect is deleted rather than ported: a query re-reads
// when its key changes, so porting it would have meant two requests per switch.

const REVIEW_MS = 8000;

// Stable empty, so "no rows" keeps one identity across renders.
const NO_SPECIES: SpeciesAgg[] = [];

export default function BirdsReview() {
  const { selected, deviceTok, includeNonBird } = useBirdsScope();
  const [status, setStatus] = useState<TrustStatus>("confirmed");
  const isLoc = selected?.kind === "loc" && !!selected.id;
  const locId = isLoc ? selected!.id! : undefined;

  // A place's reads. Same three-part params as the dashboard and the location
  // page, so the summary here is the entry those already filled.
  const lp = { locationId: locId ?? 0, includeNonBird, device: deviceTok };
  const scoped = { ...lp, status };
  const live = { refetchInterval: REVIEW_MS, staleTime: 0, retry: false, placeholderData: keepPreviousData };

  const locSpeciesQ = useQuery({
    queryKey: birdsKeys.locationSpecies(scoped),
    queryFn: () => fetchLocationSpecies<{ species: SpeciesAgg[] }>(scoped),
    enabled: isLoc, ...live,
  });

  // The coordinates, and ONLY the coordinates: they are what decides whether the
  // species-level actions can be offered at all. Its own read with its own
  // lifetime, where a failure of it used to take the species list down with it
  // (one `Promise.all`, one `.catch`, one error).
  //
  // AND NO CADENCE. It was on the same 8s loop as the list, re-reading a
  // location's latitude every eight seconds for the whole time this page was
  // open. A place's coordinates change when someone edits them, which
  // invalidates this entry from the page that did it.
  const locSummaryQ = useQuery({
    queryKey: birdsKeys.locationSummary(lp),
    queryFn: () => fetchLocationSummary<{ lat?: number | null; lon?: number | null }>(lp),
    enabled: isLoc,
    retry: false,
    placeholderData: keepPreviousData,
  });

  const yp = { includeNonBird };
  const yardSpeciesQ = useQuery({
    queryKey: birdsKeys.species(yp),
    queryFn: () => fetchSpecies<{ species: SpeciesAgg[] }>(yp),
    enabled: !isLoc, ...live,
  });

  const speciesQ = isLoc ? locSpeciesQ : yardSpeciesQ;
  const sp = speciesQ.data?.species ?? NO_SPECIES;
  const err = speciesQ.isError ? String(speciesQ.error) : null;
  const loc = locSummaryQ.data;

  const label = status[0].toUpperCase() + status.slice(1);
  const emptyMsg = !isLoc ? "Nothing yet."
    : status === "suggested" ? "No suggestions right now."
    : status === "rejected" ? "Nothing rejected."
    : "Nothing confirmed yet.";

  // NOTE: nothing here reloads the list after a row writes. `onChanged={load}`
  // used to be threaded into every row; a row now invalidates what it changed
  // (`SpeciesRow.tsx`), and this list re-reads because it is mounted on the key
  // that was invalidated.

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

      {/* A dropped read with the list still up is a line beside it. */}
      {err && speciesQ.data && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void speciesQ.refetch()}>Retry</Button>}>
            This list is not refreshing.
          </Notice>
        </div>
      )}

      {err && !speciesQ.data ? (
        <StateBlock
          variant="error"
          title="Couldn't load species"
          hint={<code>{err}</code>}
          action={<Button size="sm" onClick={() => void speciesQ.refetch()}>Retry</Button>}
        />
      ) : speciesQ.isPending ? (
        <Skeleton style={{ height: 200 }} />
      ) : (
        <section className="card" style={{ padding: "1rem" }}>
          <h3 style={{ margin: "0 0 .4rem", fontSize: "1rem" }}>{isLoc ? label : "Species"} ({sp.length})</h3>
          {sp.length === 0 ? (
            <p className="muted">{emptyMsg}</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {isLoc
                ? sp.map((s) => (
                    <SpeciesRow key={s.scientific_name} s={s} locId={locId} status={status}
                      lat={loc?.lat} lon={loc?.lon} />
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
