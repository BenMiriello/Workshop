// Where an app's page views may go, part of the page-view contract
// (./native-pages.ts, which re-exports this). The app declares the rules;
// the shell applies them to every navigation of the app's pages and reports
// what it cancelled. Every rule is off unless the app turns it on.

/**
 * Set with `pages.navigationRules.set`, replacing the app's earlier rules.
 * They apply to every page view of the app, open or opened later, pop-ups
 * included, and are kept on the device, so they are in force at the next
 * launch before anything sets them again.
 *
 * Never judged as jumps: loads the app starts (`open`, `navigate`), server
 * redirects of a navigation already allowed, form submissions, back and
 * forward, and reloads. A load the app starts also counts as the person's
 * tap. Hops and cleaning apply to every load of a whole page by GET, the
 * app's own and redirects included.
 */
export interface PageNavigationRules {
  /** A pop-up opens only from a tap, click or key in the page; others are
   *  refused by WebKit, unseen, or by the shell, reported. */
  popupsNeedTap?: boolean;
  /**
   * The whole page jumping to another site without a recent tap is
   * cancelled, when the page's own script started it, or when a frame from
   * another site started it (wherever it goes). Reported on
   * "pages:navigationBlocked".
   */
  blockUntappedJumps?: boolean;
  /** How long a tap, click or key counts as recent. Default 5000. */
  tapWindowMs?: number;
  /**
   * The public suffix list, one rule per entry in its own syntax ("co.uk",
   * "*.ck", "!www.ck"; hosts in ASCII), so `a.example.co.uk` and
   * `example.co.uk` are one site. Without it a site is a host's last two
   * labels.
   */
  publicSuffixes?: string[];
  /** Tracking hops to skip: the page goes straight to where they forward. */
  hops?: PageHopRule[];
  /** Tracking parameters to take out of addresses. */
  stripParams?: PageStripRule[];
  /**
   * A load of the whole page whose response is a file, an attachment or a
   * type the page view cannot show, is cancelled and handed to the app on
   * "pages:download" (see `PageDownloadEvent` in ./native-page-downloads).
   * The page stays where it was. Without this the page view does nothing
   * with such a response.
   */
  downloadsToApp?: boolean;
}

/**
 * An address that only forwards to another. Exactly one of `param` and
 * `pathPattern` says where the destination is. A destination on the same
 * site as the hop is not taken.
 */
export interface PageHopRule {
  /** Match patterns as browser extensions write them: `*://*.example.com/out?*`. */
  include: string[];
  exclude?: string[];
  /** The query parameter holding the destination. */
  param?: string;
  /** With `param`: its value is base64 of the destination. */
  base64?: boolean;
  /** A regular expression on the path, as written in the address; its
   *  captures, joined, make the destination, percent-decoded. */
  pathPattern?: string;
  /** With `pathPattern`: the destination with `$1`, `$2`... for captures. */
  template?: string;
  /** Put before a destination with no scheme: "https". */
  prependScheme?: string;
}

/**
 * Parameters to take out: on every address `urlPattern` matches (every
 * address when left out) that no `exceptions` pattern matches, each query
 * parameter whose name one of `params` matches whole. Patterns are regular
 * expressions, matched ignoring case; names as written in the address.
 */
export interface PageStripRule {
  urlPattern?: string;
  params: string[];
  exceptions?: string[];
}

/** Pushed on topic "pages:navigationBlocked" for each navigation the rules
 *  cancelled; the page stays where it was. */
export interface PageNavigationBlockedEvent {
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`). */
  app?: string;
  /** Where it would have gone. */
  url: string;
  /** The page it would have left. */
  from: string;
  /** "script": the page's own script, in its main frame or a frame of its
   *  own site; "frame": a frame from another site; "popup": a pop-up
   *  without a tap. */
  reason: "script" | "frame" | "popup";
  /** The address of the frame that started it, when not the main frame. */
  frameUrl?: string;
}
