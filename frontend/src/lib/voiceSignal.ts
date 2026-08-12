// Is the input lane actually broken, or has the operator simply not spoken yet?
//
// SILENCE IS NOT A FAULT, and treating it as one is the bug this module exists to
// make impossible. The watchdog used to compare the loudest sample in a 4 second
// window against a fixed floor and, if nothing cleared it, print "No signal from
// the microphone. Check the input device, or that another app isn't holding it."
//
// A person thinking before they speak produces exactly that. So the message fired
// whenever the operator did not talk fast enough, over a perfectly good
// microphone, telling them to go and debug their hardware. Reported 2026-08-12 in
// those words: "it still prints out no signal from the microphone if i dont speak
// fast enough".
//
// A LEVEL CANNOT ANSWER THIS QUESTION. Loudness distinguishes loud from quiet; it
// does not distinguish a dead device from a quiet room, and no threshold ever
// will, because both produce the same samples. Only the MediaStreamTrack knows:
// `ended` means the device went away, `muted` means something else took it. That
// is what "no signal" is allowed to mean, and it is the caller's job to pass it
// in rather than infer it.
//
// The remaining level check is deliberately narrow: it fires only when there IS
// sound above the measured room floor that never reaches the speech threshold,
// which is a real, actionable condition (a mic turned down). Sound AT the floor
// is the room, and the room is not a problem.

export type SignalVerdict =
  // Nothing to say. Includes silence, which is the common case.
  | "ok"
  // The device stopped producing. The only genuine "no signal".
  | "device-lost"
  // Audible, consistently, but never loud enough to count as speech.
  | "too-quiet";

export type SignalReading = {
  /** The track's own state: live and not muted. Authoritative. */
  trackLive: boolean;
  /** Has the noise floor measurement finished? */
  calibrated: boolean;
  /** Has anything ever cleared the speech threshold this session? */
  heardSpeechEver: boolean;
  /** Loudest sample in the window just elapsed. */
  peak: number;
  /** Measured ambient room level. */
  floor: number;
  /** Level a sample must clear to count as speech. */
  threshold: number;
};

// How far above the measured room floor a peak has to sit before it counts as
// "something is making noise here" rather than "this is the room". Generous, so
// ordinary room variation never reads as a quiet talker.
export const AUDIBLE_OVER_FLOOR = 1.8;

export function signalVerdict(r: SignalReading): SignalVerdict {
  // The device is gone. This is the one case that warrants telling someone to go
  // and look at their hardware.
  if (!r.trackLive) return "device-lost";
  // Already proven to work this session. Nothing measured afterwards can make it
  // broken, and a pause between turns certainly cannot.
  if (r.heardSpeechEver) return "ok";
  // Without a measured floor there is nothing to compare against, so say nothing
  // rather than guess.
  if (!r.calibrated) return "ok";
  // Sound that is clearly above the room but never reaches speech: a real,
  // fixable condition.
  if (r.peak > r.floor * AUDIBLE_OVER_FLOOR && r.peak < r.threshold) return "too-quiet";
  // Everything else, including a silent room, is fine.
  return "ok";
}
