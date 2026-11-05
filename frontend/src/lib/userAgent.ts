// A short name for a signed-in browser, from the user-agent string the server
// stored when the session was made: "iPhone · Safari", "Mac · Chrome". Only for
// display beside a session the operator has not named; it is a guess from a
// string the client chose, never an identity.

const DEVICES: [RegExp, string][] = [
  [/iPhone/, "iPhone"],
  [/iPad/, "iPad"],
  [/Android/, "Android"],
  [/Macintosh|Mac OS X/, "Mac"],
  [/Windows/, "Windows"],
  [/CrOS/, "Chromebook"],
  [/Linux/, "Linux"],
];

// Order matters: Edge and Opera also say Chrome, Chrome also says Safari, and
// every iOS browser is WebKit, so the specific names are tested first.
const BROWSERS: [RegExp, string][] = [
  [/Edg\//, "Edge"],
  [/OPR\/|Opera/, "Opera"],
  [/Firefox\/|FxiOS\//, "Firefox"],
  [/Chrome\/|CriOS\//, "Chrome"],
  [/Safari\//, "Safari"],
];

export function describeUserAgent(ua: string | null | undefined): string {
  if (!ua) return "Unknown browser";
  const device = DEVICES.find(([re]) => re.test(ua))?.[1];
  const browser = BROWSERS.find(([re]) => re.test(ua))?.[1];
  // An iOS home-screen app runs in WebKit without the "Safari/" token.
  const ios = device === "iPhone" || device === "iPad";
  const name = browser ?? (ios && /AppleWebKit/.test(ua) ? "Home screen app" : undefined);
  return [device, name].filter(Boolean).join(" · ") || "Unknown browser";
}
