# 0017. Spoken text is recorded at capture, and unsent speech is a draft

Date: 2026-08-12
Status: Accepted

## Context

Voice mode lost a dictated message. The forensics on the thread showed fourteen
`run` events between two typed messages and not one user message: the words the
operator watched appear on screen had never been persisted anywhere.

Two independent causes, both structural.

**1. Voice inverted the persistence order relative to typing.** The text path
appends the user message and then runs the model (`routes/chat._record_user_turn`
→ `chat_store.append_message`, before the turn starts). Voice handed the
utterance to `voice_worker` and `_persist_turn` wrote the user and assistant
messages *together*, at the end of a completed, non-discarded turn. Everything
between "he stopped speaking" and "the reply finished" was a window in which his
words existed only in process memory: a cancel, a barge, a failed reply, a
dropped socket, or a turn that never ended each destroyed them.

**2. The transcript had no owner and no identity.** The state carried three loose
strings (`segments`, `partial`, `last_user`) with no id, and the log rendered the
operator's own words through three sibling components plus a held copy:

| component | source | mounted while |
|---|---|---|
| `SettlingTurn` | a snapshot copy taken at the finish transition | up to 8 × 250ms poll |
| `VoiceLiveTurn` | `vsnap.lastUser` | thinking or speaking |
| `VoicePendingTurn` | `vsnap.partial \|\| vsnap.pendingUser` | listening |

Four copies of one string counting the real message, handed between slots by
timing rather than by identity. Each reported symptom falls out of that: the
handoff blinked (flicker); `lastUser` rendered directly above the assistant
bubble, so a new utterance appeared attached to the previous exchange (words
moving into an earlier message); the held copy could be on screen beside the live
one and the persisted one (duplication); and all of it was socket state, so
`VoiceClient.stop()` emptying the snapshot deleted every copy at once.

A third, smaller cause made it worse hourly: the endpoint built the message from
`segments` alone, dropping `partial`. Stopping talking is what makes the endpoint
fire, so the last words are always in `partial`. The tail of every utterance was
discarded, and when the endpoint beat the first result back the whole utterance
was deleted and reported as "Didn't catch that".

## Decision

**A spoken utterance is written to the thread when it is captured, before
generation starts, by the same store call typing uses.** The reducer emits
`RecordUtterance` ahead of `StartGeneration`; the runtime awaits the write. Both
the runtime and the worker pass the turn id as `chat_store`'s idempotency key, so
neither ordering nor a retry can double it, and `_thread_seed` excludes that key
so the utterance does not reach the model as both history and prompt.

**The operator's words are never rendered from voice state.** Before they are
sent they are a draft in the composer, which is already mirrored to the server
and already the home of unsent text. After they are sent they are an ordinary
message in the log. Voice renders only the reply. Voice owns a prefix of the
composer and relinquishes it if he edits inside it.

**A committed message is not taken back.** A cancel drops the reply, not the
utterance. A continuation is its own message rather than being folded into the
previous one.

## Consequences

- The loss is structurally impossible rather than handled: text that has been
  shown is either a server-mirrored draft or a persisted message, never React
  state derived from a socket snapshot.
- Duplication is impossible: one owner, one identity, and the store dedups.
- `useVoiceSettle` and two of the three render slots are deleted. They existed
  only to bridge the late persistence.
- **A fragment can now stand as its own message.** If the endpoint fires early
  and the operator continues, the log holds two messages instead of one merged
  one. This is the deliberate trade: folding forward would record the same words
  twice, once alone and again inside a longer message. A fragment is visible and
  deletable; silent duplication is neither.
- A cancel can no longer erase a mis-heard utterance. It is deleted like any
  other message.

## Alternatives rejected

- **Keep merging continuations, and mutate or delete the earlier message.**
  Rejected: every production system treats committed transcript text as
  append-only and immutable (Apple SpeechTranscriber, AssemblyAI, LiveKit all
  say replace the volatile buffer, never edit what is committed), and mutating
  committed messages is the same mechanism as the "text moved into the previous
  bubble" symptom.
- **Persist only on a confirmed endpoint.** Rejected: it reinstates the window
  the whole change exists to close.
- **Keep the pending bubble and flush to a draft on teardown.** Rejected: it
  relies on a cleanup handler running, which a killed tab does not guarantee,
  and keeps a second renderer of the same text.

## References

- `backend/services/voice/machine.py` (`RecordUtterance`, `_abandon`)
- `backend/services/voice/runtime.py` (`_record_utterance`, `force_endpoint`)
- `frontend/src/components/chat/voiceDictation.ts`
- `backend/tests/test_voice_records_what_was_said.py`,
  `test_voice_stream_route.py::TestTheSocketRecordsWhatWasSaid`
- `docs/voice-mode/architecture-2026-08-12.md` (the transport/perception design
  this sits on top of)
