import { describe, expect, it } from "vitest";
import { micBlockedHelp, micClient, micSettingsLabel } from "./micAccess";

const FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) Gecko/20100101 Firefox/131.0";
const SAFARI_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";
const SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";

describe("micClient", () => {
  it("names the native apps by their platform, whatever the engine says", () => {
    expect(micClient("ios", SAFARI_IPHONE)).toBe("ios-app");
    expect(micClient("macos", SAFARI_MAC)).toBe("mac-app");
  });

  it("tells the browsers apart", () => {
    expect(micClient(undefined, FIREFOX_MAC)).toBe("firefox");
    expect(micClient(undefined, SAFARI_MAC)).toBe("safari");
    expect(micClient(undefined, SAFARI_IPHONE)).toBe("ios-safari");
    expect(micClient(undefined, CHROME)).toBe("chromium");
    expect(micClient(undefined, "curl/8")).toBe("other");
  });

  it("says where the setting is and never uses an em-dash", () => {
    expect(micBlockedHelp("mac-app")).toContain("System Settings");
    expect(micBlockedHelp("ios-app")).toContain("Settings app");
    for (const c of ["ios-app", "mac-app", "ios-safari", "safari", "firefox", "chromium", "other"] as const) {
      expect(micBlockedHelp(c)).not.toContain("—");
    }
  });
});

describe("micSettingsLabel", () => {
  it("names the place the button opens", () => {
    expect(micSettingsLabel("mac-app")).toBe("Open System Settings");
    expect(micSettingsLabel("ios-app")).toBe("Open Settings");
  });
});
