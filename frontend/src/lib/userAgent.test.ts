import { describe, expect, it } from "vitest";
import { describeUserAgent } from "./userAgent";

describe("describeUserAgent", () => {
  it("names an iPhone in Safari", () => {
    const ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
    expect(describeUserAgent(ua)).toBe("iPhone · Safari");
  });

  it("names an iPhone home-screen app, which drops the Safari token", () => {
    const ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148";
    expect(describeUserAgent(ua)).toBe("iPhone · Home screen app");
  });

  it("prefers Chrome over the Safari token Chrome also carries", () => {
    const ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36";
    expect(describeUserAgent(ua)).toBe("Mac · Chrome");
  });

  it("prefers Edge over Chrome", () => {
    const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0";
    expect(describeUserAgent(ua)).toBe("Windows · Edge");
  });

  it("falls back when there is nothing to read", () => {
    expect(describeUserAgent(null)).toBe("Unknown browser");
    expect(describeUserAgent("")).toBe("Unknown browser");
    expect(describeUserAgent("curl/8.0")).toBe("Unknown browser");
  });
});
