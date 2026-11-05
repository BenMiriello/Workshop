// Local Expo module entry. The native side registers as "WorkshopPages"; app
// code reaches it through native/pages.ts (requireOptionalNativeModule), which
// degrades cleanly when this module is not compiled into the build.
import { requireOptionalNativeModule } from "expo-modules-core";

export default requireOptionalNativeModule("WorkshopPages");
