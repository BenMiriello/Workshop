// A file a page offers, handed to the app, part of the page-view contract
// (./native-pages.ts, which re-exports this). It is pushed only for an app
// whose navigation rules set `downloadsToApp` (./native-page-navigation).

/**
 * Pushed on topic "pages:download" when a page view's main frame is given a
 * response that is a file: the load is cancelled and the page stays where it
 * was. Only http and https addresses are reported. The shell downloads
 * nothing itself; what to do with the file is the app's.
 */
export interface PageDownloadEvent {
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`). */
  app?: string;
  /** Where the file is. */
  url: string;
  /** The page that was showing when it was offered; empty when the view
   *  had shown none. */
  from: string;
  /** The address that was asked for, when a redirect led from it to `url`.
   *  A model site's `url` is often a signed address that expires, while
   *  this one can be asked again. */
  link?: string;
  /** The name the response suggests (its Content-Disposition, else the
   *  address); left out when it suggests none. */
  filename?: string;
  /** The response's content type, when it names one. */
  mime?: string;
  /** The size in bytes, when the response states it. */
  bytes?: number;
  /** "attachment": the response asks to be saved (Content-Disposition
   *  attachment). "unshowable": the page view cannot display its type. */
  reason: "attachment" | "unshowable";
}
