// Types for the Live Aviary engine (aviary-engine.js).
export interface AviaryOpts {
  stage: HTMLElement;
  scenery: SVGSVGElement | HTMLElement;
  field: HTMLElement;
  /** Optional in-stage "N in the yard" overlay element. */
  countchip?: HTMLElement | null;
  /** Optional standalone-demo control elements, by id. Unused by the dashboard. */
  elements?: Record<string, HTMLElement | null>;
  /** Called each panel tick with the current ground state. */
  onState?: (s: { count: number; species: number }) => void;
}

export interface AviaryApi {
  /** Reconcile the scene to exactly this set of present species ids. */
  setPresent(ids: string[]): void;
  /** One detection -> one bird of that species flies in. */
  heard(id: string): void;
  /** Toggle the random demo spawner (off in production). */
  simulate(on: boolean): void;
  /** The dashboard element's single control. */
  setNameTags(on: boolean): void;
  /** Merge generated color profiles into the registry. */
  registerSpecies(map: Record<string, unknown>): void;
  /** Ids the renderer currently knows about. */
  species(): string[];
  /** Recompute scene layout (call on container resize). */
  relayout(): void;
  /** Tear down: stop the loop, drop listeners, clear the scene. */
  destroy(): void;
}

export function createAviary(opts: AviaryOpts): AviaryApi;
