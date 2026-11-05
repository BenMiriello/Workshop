// Settings > Copy and paste, on this device: which copies are saved (none,
// the Workshop's, or, where the native app can watch, anything copied on the
// device), and whether a Workshop copy also goes to the system clipboard.
// View preferences, so they commit on change.
import { useEffect, useState } from "react";
import { ClipboardCopy } from "lucide-react";
import { Notice, Select, SettingsRow as Row, SettingsSection as Section } from "@workshop/ui";
import { copyPrefs, useCopyPrefs, type CopyDestination } from "../../../lib/copyPrefs";
import { nativeClipboard, type ClipboardWatchMode } from "../../../lib/nativeClipboard";

type Save = "off" | ClipboardWatchMode;

export function CopySection() {
  const prefs = useCopyPrefs();
  const canWatch = nativeClipboard.canWatch();
  const [watch, setWatch] = useState<ClipboardWatchMode | null>(canWatch ? null : "workshop");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!canWatch) return;
    let live = true;
    nativeClipboard.getWatch().then((m) => { if (live) setWatch(m ?? "workshop"); });
    return () => { live = false; };
  }, [canWatch]);

  const save: Save | null = !prefs.save ? "off" : watch;

  async function chooseSave(next: Save) {
    copyPrefs.set({ save: next !== "off" });
    if (!canWatch) return;
    const mode: ClipboardWatchMode = next === "everything" ? "everything" : "workshop";
    const before = watch;
    setWatch(mode);
    setFailed(false);
    try {
      setWatch(await nativeClipboard.setWatch(mode));
    } catch {
      setWatch(before);
      setFailed(true);
    }
  }

  return (
    <Section icon={ClipboardCopy} title="Copy and paste">
      {save !== null && (
        <Row label="save copies from">
          <Select value={save} onChange={(e) => chooseSave(e.target.value as Save)} aria-label="Save copies from">
            <option value="off">Nowhere</option>
            <option value="workshop">The Workshop</option>
            {canWatch && <option value="everything">Anything I copy on this device</option>}
          </Select>
          {failed && <Notice tone="error">That change did not save.</Notice>}
        </Row>
      )}
      <Row label="workshop copies">
        <Select value={prefs.destination} aria-label="Workshop copies"
          onChange={(e) => copyPrefs.set({ destination: e.target.value as CopyDestination })}>
          <option value="system">Go to the system clipboard too</option>
          <option value="workshop">Stay in the Workshop</option>
        </Select>
      </Row>
    </Section>
  );
}
