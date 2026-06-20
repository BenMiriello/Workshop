import { Stack } from "expo-router";

// Import for side effect: registers the background-location TaskManager task at
// startup, so iOS can relaunch the app into the task even before any screen
// mounts. Must happen at module load (TaskManager requirement).
import "../native/location";

export default function Layout() {
  return <Stack screenOptions={{ headerShown: false }} />;
}
