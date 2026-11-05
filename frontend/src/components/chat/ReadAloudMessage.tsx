import { useEffect, useId, useRef, useSyncExternalStore } from "react";
import { Square, Volume2 } from "lucide-react";
import { reader } from "../shell/speech/reader";
import { placeRead } from "../shell/speech/readPlaces";

// Read one message aloud, from the same corner cluster as Copy, immediately
// left of it, at the same size and resting tone (.chat-read beside .chat-copy
// in styles.css). While this message is the one being read, the same button
// stops it. The message's body is where the read is on the page, so it can be
// highlighted, and a tap in the body while reading reads it from there.
export default function ReadAloudMessage({ value }: { value: string }) {
  const id = useId();
  const ref = useRef<HTMLButtonElement>(null);
  const text = useRef(value);
  text.current = value;
  const state = useSyncExternalStore(reader.subscribe, reader.getState, reader.getState);
  const reading = state.source === id && (state.phase === "loading" || state.phase === "playing");
  const label = reading ? "Stop reading" : "Read aloud";
  const body = () => ref.current?.parentElement?.querySelector(":scope > .chat-bubble__body") ?? null;

  useEffect(() => {
    const el = body();
    return el ? placeRead(el, id, () => text.current) : undefined;
  }, [id]);

  return (
    <button
      ref={ref}
      type="button"
      className="chat-read"
      onClick={() => (reading ? reader.stop() : reader.read(value, id, body()))}
      aria-label={label}
      aria-pressed={reading}
      title={label}
    >
      {reading
        ? <Square size={13} strokeWidth={1.8} aria-hidden />
        : <Volume2 size={13} strokeWidth={1.8} aria-hidden />}
    </button>
  );
}
