import { Stack } from "expo-router";

// Import for side effect: registers the background-location TaskManager task at
// startup, so iOS can relaunch the app into the task even before any screen
// mounts. Must happen at module load (TaskManager requirement).
import "../native/location";

// The status bar is styled per screen: Info.plist turns on view-controller-based
// status bar appearance, because iOS 26 ignores the app-wide style that
// expo-status-bar sets. The shell's own screens are dark; the WebView screen
// follows its page (app/index.tsx).
export default function Layout() {
  return <Stack screenOptions={{ headerShown: false, statusBarStyle: "light" }} />;
}
