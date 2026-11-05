// Prints the bridge client the Mac shell injects: the phone's client
// (mobile/native/inject.ts) offering only what the Mac implements, the page
// side of the right-click "Read aloud" (mobile/native/readSelection.ts), and
// the report of the web UI's background color the window paints behind it
// (mobile/native/appearance.ts).
// Bundled and run by scripts/macos-build.sh into the app's bridge.js.
import type { Capability } from "@shared/native-bridge";
import { buildAppearanceWatch } from "../../mobile/native/appearance";
import { buildInjectedBridge } from "../../mobile/native/inject";
import { buildReadSelection } from "../../mobile/native/readSelection";

const MACOS_CAPS: Capability[] = ["pages", "shell", "mic", "clipboard"];

process.stdout.write(buildInjectedBridge("free", "macos", MACOS_CAPS) + buildReadSelection() + buildAppearanceWatch());
