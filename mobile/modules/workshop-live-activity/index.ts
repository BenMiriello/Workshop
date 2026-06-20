// Local Expo module entry. The native side registers as "WorkshopLiveActivity";
// app code accesses it through native/liveActivity.ts (requireOptionalNativeModule),
// which degrades gracefully when this module isn't compiled into the build yet.
import { requireOptionalNativeModule } from "expo-modules-core";

export default requireOptionalNativeModule("WorkshopLiveActivity");
