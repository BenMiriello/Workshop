// Local mirror of shared/native-page-navigation.ts (see types.ts for why the
// kit mirrors the bridge contract instead of importing it).

/** Where an app's page views may go; every rule is off unless turned on.
 *  Kept on the device and applied to every page view of the app. */
export interface PageNavigationRules {
  /** A pop-up opens only from a tap, click or key in the page. */
  popupsNeedTap?: boolean;
  /** The whole page jumping to another site without a recent tap is
   *  cancelled when the page's script or a frame from another site started
   *  it; reported on "pages:navigationBlocked". */
  blockUntappedJumps?: boolean;
  /** How long a tap counts as recent. Default 5000. */
  tapWindowMs?: number;
  /** The public suffix list, one rule per entry ("co.uk", "*.ck", "!www.ck"). */
  publicSuffixes?: string[];
  hops?: PageHopRule[];
  stripParams?: PageStripRule[];
  /** A load of the whole page whose response is an attachment or a type the
   *  page view cannot show is cancelled and handed to the app on
   *  "pages:download"; the page stays where it was. */
  downloadsToApp?: boolean;
}

/** A tracking hop to skip; exactly one of `param` and `pathPattern`. */
export interface PageHopRule {
  include: string[];
  exclude?: string[];
  param?: string;
  base64?: boolean;
  pathPattern?: string;
  template?: string;
  prependScheme?: string;
}

/** Query parameters to take out of addresses `urlPattern` matches. */
export interface PageStripRule {
  urlPattern?: string;
  params: string[];
  exceptions?: string[];
}

/** Pushed on topic "pages:navigationBlocked"; the page stays where it was. */
export interface PageNavigationBlockedEvent {
  id: string;
  app?: string;
  /** Where it would have gone. */
  url: string;
  /** The page it would have left. */
  from: string;
  reason: "script" | "frame" | "popup";
  /** The frame that started it, when not the main frame. */
  frameUrl?: string;
}
