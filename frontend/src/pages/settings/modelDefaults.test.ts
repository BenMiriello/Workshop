import { describe, expect, it } from "vitest";
import {
  SCOPES_SHOWN, compactionFallbackLabel, fallbackLabel, nameOf, optionLabel, visibleScopes, windowLabel,
  withStored, type ModelOption, type ScopeModelDefault,
} from "./modelDefaults";

const MODELS: ModelOption[] = [
  { id: "big", label: "Big", provider_id: "local_http", window: 98304 },
  { id: "cloud", label: "Cloud", provider_id: "claude_cli", window: 0 },
];

const scope = (source: string, fallback = "big"): ScopeModelDefault => ({
  key: "app:x", label: "X", model: null, fallback_model: fallback, fallback_source: source,
});

describe("model default labels", () => {
  it("names a window in thousands, and nothing when unknown", () => {
    expect(windowLabel(98304)).toBe("98k");
    expect(windowLabel(0)).toBe("");
    expect(optionLabel(MODELS[0])).toBe("Big (98k window)");
    expect(optionLabel(MODELS[1])).toBe("Cloud");
  });

  it("names a model by its served label, else its id", () => {
    expect(nameOf("cloud", MODELS)).toBe("Cloud");
    expect(nameOf("gone", MODELS)).toBe("gone");
  });

  it("names the fallback for where it comes from", () => {
    expect(fallbackLabel(scope("scope"), MODELS)).toBe("App default (Big)");
    expect(fallbackLabel(scope("principal", "cloud"), MODELS)).toBe("Global default (Cloud)");
    expect(fallbackLabel(scope("deployment", "cloud"), MODELS)).toBe("System default (Cloud)");
  });
});

describe("compactionFallbackLabel", () => {
  it("names the global compaction model, else the chat's own", () => {
    expect(compactionFallbackLabel({ ...scope("scope"), compaction_fallback: "big" }, MODELS))
      .toBe("Global (Big)");
    expect(compactionFallbackLabel({ ...scope("scope"), compaction_fallback: "gone" }, MODELS))
      .toBe("Global (gone)");
    expect(compactionFallbackLabel(scope("scope"), MODELS)).toBe("Same as chat model");
    expect(compactionFallbackLabel({ ...scope("scope"), compaction_fallback: null }, MODELS))
      .toBe("Same as chat model");
  });
});

describe("withStored", () => {
  it("leaves the list alone when the stored id is in it or nothing is stored", () => {
    expect(withStored(MODELS, "big")).toEqual(MODELS);
    expect(withStored(MODELS, null)).toEqual(MODELS);
  });

  it("carries a stored id the list no longer holds, first", () => {
    const got = withStored(MODELS, "gone");
    expect(got[0].id).toBe("gone");
    expect(got.slice(1)).toEqual(MODELS);
  });
});

describe("visibleScopes", () => {
  const many = Array.from({ length: SCOPES_SHOWN + 3 }, (_, i) => i);

  it("folds a long list and unfolds it on request", () => {
    expect(visibleScopes(many, false)).toHaveLength(SCOPES_SHOWN);
    expect(visibleScopes(many, true)).toHaveLength(SCOPES_SHOWN + 3);
  });
});
