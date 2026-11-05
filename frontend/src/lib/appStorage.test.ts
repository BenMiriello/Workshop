import { describe, expect, it } from "vitest";

import { appStorageName, staleNames } from "./appStorage";

describe("staleNames", () => {
  const names = [appStorageName("photos", "edits"), appStorageName("notes", "drafts"),
    "ws-theme", "ws-appish.x"];

  it("takes the storage of apps outside the list and nothing else", () => {
    expect(staleNames(names, new Set(["photos"]))).toEqual(["ws-app.notes.drafts"]);
  });

  it("takes every app's on sign-out", () => {
    expect(staleNames(names, new Set())).toEqual(["ws-app.photos.edits", "ws-app.notes.drafts"]);
  });
});
