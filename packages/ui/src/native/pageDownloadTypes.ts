// Local mirror of shared/native-page-downloads.ts (see types.ts for why the
// kit mirrors the bridge contract instead of importing it).

/** Pushed on topic "pages:download" when a page's main frame is given a file
 *  and the app's navigation rules set `downloadsToApp`; the load is
 *  cancelled and the page stays where it was. */
export interface PageDownloadEvent {
  id: string;
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
  /** The name the response suggests, when it suggests one. */
  filename?: string;
  mime?: string;
  /** The size in bytes, when the response states it. */
  bytes?: number;
  /** "attachment": the response asks to be saved. "unshowable": the page
   *  view cannot display its type. */
  reason: "attachment" | "unshowable";
}
