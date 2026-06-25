// Durable offline queue for background location fixes. The shell is the always-
// on replacement for OwnTracks, so a fix dropped during a network blip (tunnel,
// dead zone, backend restart) must not be lost — it is persisted to disk and
// re-sent oldest-first on the next background delivery once connectivity
// returns. Order is preserved end-to-end.
//
// Storage is a single JSON array file in the app's document directory. We avoid
// a DB on purpose: the backlog is tiny, append/drain-only, and a flat file
// survives suspension and relaunch (the bg location task may re-enter the app
// from cold). All access is serialized through a promise chain so overlapping
// background deliveries can't interleave a read-modify-write.

import * as FileSystem from "expo-file-system";

import type { LocationFix } from "@shared/native-bridge";

const QUEUE_FILE = (FileSystem.documentDirectory ?? "") + "location-queue.json";

// Cap the backlog so a multi-day outage can't grow without bound. A week-old
// track is worthless, so we keep the most recent fixes and drop the oldest.
const MAX_QUEUED = 500;

let chain: Promise<unknown> = Promise.resolve();

/** Serialize file access; a thrown task still releases the lock for the next. */
function locked<T>(fn: () => Promise<T>): Promise<T> {
  const run = chain.then(fn, fn);
  chain = run.catch(() => undefined);
  return run;
}

async function readQueue(): Promise<LocationFix[]> {
  try {
    const info = await FileSystem.getInfoAsync(QUEUE_FILE);
    if (!info.exists) return [];
    const raw = await FileSystem.readAsStringAsync(QUEUE_FILE);
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as LocationFix[]) : [];
  } catch {
    // Corrupt/unreadable file: treat as empty rather than wedge the producer.
    return [];
  }
}

async function writeQueue(items: LocationFix[]): Promise<void> {
  const trimmed =
    items.length > MAX_QUEUED ? items.slice(items.length - MAX_QUEUED) : items;
  await FileSystem.writeAsStringAsync(QUEUE_FILE, JSON.stringify(trimmed));
}

/** Append a fix that failed to send. */
export function enqueue(fix: LocationFix): Promise<void> {
  return locked(async () => {
    const q = await readQueue();
    q.push(fix);
    await writeQueue(q);
  });
}

/** Current backlog depth (surfaced in TrackingStatus for on-device verification). */
export function depth(): Promise<number> {
  return locked(async () => (await readQueue()).length);
}

/**
 * Drain the backlog oldest-first via `send`. Stops at the first failure (network
 * still down) and persists the unsent remainder. Returns true only when the
 * queue is fully empty afterwards, so the caller knows it is safe to POST new
 * fixes directly rather than appending them behind the backlog.
 *
 * `send` does network I/O only (no queue access) — calling back into this module
 * from within `send` would deadlock on the lock it already holds.
 */
export function flush(send: (fix: LocationFix) => Promise<boolean>): Promise<boolean> {
  return locked(async () => {
    const q = await readQueue();
    if (q.length === 0) return true;
    let sent = 0;
    for (; sent < q.length; sent++) {
      const ok = await send(q[sent]);
      if (!ok) break;
    }
    const remaining = q.slice(sent);
    await writeQueue(remaining);
    return remaining.length === 0;
  });
}
