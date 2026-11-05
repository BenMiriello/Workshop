// Local Expo module entry. The native side registers as "WorkshopClipboard";
// app code reaches it through native/clipboard.ts. Optional, so a build from
// before the module was added reports the capability as failing, not crashing.
import { requireOptionalNativeModule } from "expo-modules-core";

import type { ClipboardWatchMode } from "@shared/native-bridge";

export type ClipboardModule = {
  setString(text: string): Promise<boolean>;
  watchGet(): Promise<ClipboardWatchMode>;
  watchSet(mode: ClipboardWatchMode): Promise<ClipboardWatchMode>;
  /** Where copies are saved: the API base, e.g. "https://host/api". */
  configure(apiBase: string): Promise<void>;
  /** Counts what is on the system clipboard now as seen. */
  markSeen(): Promise<void>;
  /** Leaves the API base and the sign-in in the app group for the keyboard. */
  share(): Promise<void>;
  /** Saves the copy made since the last look, while the watch is
   *  "everything"; true when one was saved. */
  captureNew(): Promise<boolean>;
};

export default requireOptionalNativeModule<ClipboardModule>("WorkshopClipboard");
