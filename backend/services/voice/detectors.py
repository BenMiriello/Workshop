"""Perception: is someone speaking, and have they finished?

Two questions, two detectors, one interface each. Both have an ONNX
implementation that is the right answer and a pure-numpy implementation that is
honest about being a fallback. The ONNX one activates by itself the moment its
weights appear on disk; nothing has to be reconfigured.

WHY THIS IS SPLIT OUT AT ALL. The old code answered both questions with one RMS
threshold and a stopwatch:

  * "is there speech" was `rms > startThreshold`, with a calibration pass, a
    sensitivity knob, and a nudge that silently raised the knob mid-session and
    never told the settings sheet. A threshold over an RMS is not trained on
    noise; Silero is, which is why it needs none of that machinery.
  * "have they finished" was `silence > endpoint_silence_ms`. There is no value
    that works: short splits a thought, long makes every turn sluggish. The
    operator's stored value was 700ms, below a normal breath.

Model weights go in `<runtime>/voice/models/`:
    silero_vad.onnx        ~2 MB   https://github.com/snakers4/silero-vad
    smart-turn-v3.onnx     ~8 MB   https://huggingface.co/pipecat-ai/smart-turn-v3
Neither can be fetched from this process (egress is blocked from the shell by
policy), and neither is required to run. `status()` reports which tier is live
so the settings sheet can say so rather than implying accuracy it does not have.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from services import paths

from .timeline import SAMPLE_RATE

log = logging.getLogger("workshop.voice.detectors")

_MODELS_DIR = Path(
    os.environ.get("VOICE_MODELS_DIR", str(paths.runtime_dir() / "voice" / "models"))
)
SILERO_PATH = Path(os.environ.get("VOICE_VAD_ONNX", str(_MODELS_DIR / "silero_vad.onnx")))
SMART_TURN_PATH = Path(
    os.environ.get("VOICE_TURN_ONNX", str(_MODELS_DIR / "smart-turn-v3.onnx"))
)


def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    # These models are tiny and run per frame; one thread each keeps them out of
    # the way of Whisper and Kokoro, which are the jobs that actually want cores.
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


# ============================================================================
# Speech detection
# ============================================================================
# Frame size Silero v4/v5 expect at 16 kHz. The energy detector uses the same
# size so the two are interchangeable frame-for-frame.
VAD_WINDOW = 512


@dataclass
class SpeechFrame:
    """One VAD decision. `p` is a probability from Silero and a calibrated
    pseudo-probability from the energy detector, so callers can treat them
    alike."""

    speech: bool
    p: float


class _SileroVad:
    """Silero VAD v5 via onnxruntime. Stateful across frames (it is an RNN), so
    one instance per session and `reset()` between utterances."""

    def __init__(self) -> None:
        self._sess = None
        self._state = None
        self._lock = threading.Lock()
        self._err: str | None = None
        self._ok = False

    @property
    def available(self) -> bool:
        if self._ok:
            return True
        if self._err is not None:
            return False
        if not SILERO_PATH.exists():
            self._err = f"weights missing ({SILERO_PATH})"
            return False
        try:
            with self._lock:
                if self._sess is None:
                    self._sess = _session(SILERO_PATH)
            names = {i.name for i in self._sess.get_inputs()}
            # v5 takes (input, state, sr); v4 took (input, h, c, sr). Only v5 is
            # supported, and an unexpected graph must degrade rather than throw
            # a shape error on the first frame of a real conversation.
            if not {"input", "state", "sr"} <= names:
                self._err = f"unexpected graph inputs {sorted(names)}"
                return False
            self.reset()
            self._ok = True
            log.info("silero VAD loaded from %s", SILERO_PATH)
            return True
        except Exception as e:                       # noqa: BLE001 - degrade, never crash
            self._err = f"{type(e).__name__}: {e}"
            log.warning("silero VAD unavailable: %s", self._err)
            return False

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        out = self._sess.run(
            None,
            {
                "input": frame.reshape(1, -1).astype(np.float32),
                "state": self._state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._state = out[1]
        return float(out[0].reshape(-1)[0])

    @property
    def error(self) -> str | None:
        return self._err


class _EnergyVad:
    """Fallback speech detection: an adaptive noise floor with hysteresis.

    Not a threshold on a level, which is what the old client did and what needed
    a calibration pass, a user-facing sensitivity knob and a self-adjusting
    nudge. Three things make this behave without any of them:

      * the floor tracks continuously and asymmetrically (fast to rise with the
        room, slow to fall), so a fan switching on does not deafen the session,
      * onset and offset thresholds differ, so a level hovering at the boundary
        cannot chatter speech on and off mid-word, and
      * onset needs two consecutive frames, so a click is not a word.

    It is genuinely worse than Silero in noise. `status()` says which is running.
    """

    def __init__(self) -> None:
        self.reset()

    @property
    def available(self) -> bool:
        return True

    # Absolute floor under the adaptive one. Nothing quieter than this counts as
    # speech however quiet the room is, so a silent room cannot make the
    # detector infinitely sensitive to its own noise.
    ABS_ONSET = 0.006
    ABS_OFFSET = 0.004

    def reset(self) -> None:
        # STARTS AT ZERO, DELIBERATELY, AND RISES SLOWLY.
        #
        # The obvious design is to measure the room first and then decide. That
        # is what the old client did (a 1200ms calibration pass) and it has a
        # failure mode that is fatal and silent: if the operator is ALREADY
        # TALKING when the session arms, the measurement is taken on their
        # voice, the threshold lands above it, and the microphone is deaf for
        # the rest of the session with no error and nothing to retry.
        #
        # Starting low inverts the error. Before the floor has risen, everything
        # over the absolute minimum is treated as speech, so the worst case is
        # that a noisy room gets one over-eager utterance rather than a session
        # that never hears anything. "It heard something it shouldn't" is
        # recoverable. "It cannot hear you" is not.
        self._floor = 0.0
        self._hot = False
        self._onset_run = 0

    def __call__(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame))) + 1e-9)
        on = max(self._floor * 3.2, self.ABS_ONSET)
        off = max(self._floor * 2.0, self.ABS_OFFSET)
        if self._hot:
            if rms < off:
                self._hot = False
                self._onset_run = 0
        else:
            # Two consecutive frames, so a click or a keystroke is not a word.
            if rms > on:
                self._onset_run += 1
                if self._onset_run >= 2:
                    self._hot = True
            else:
                self._onset_run = 0
        if not self._hot:
            # The floor only ever tracks while quiet, so speech never raises it.
            # Asymmetric: quick to follow a room getting quieter, slow to follow
            # it getting louder, because the expensive mistake is going deaf.
            alpha = 0.2 if rms < self._floor else 0.01
            self._floor += (rms - self._floor) * alpha
        return 1.0 if self._hot else 0.0


class SpeechDetector:
    """Frame-by-frame speech detection with the better model when it is there."""

    THRESHOLD = 0.5

    def __init__(self) -> None:
        self._silero = _SileroVad()
        self._energy = _EnergyVad()
        self._using_silero = self._silero.available

    @property
    def tier(self) -> str:
        return "silero" if self._using_silero else "energy"

    def reset(self) -> None:
        self._silero.reset() if self._using_silero else self._energy.reset()

    def __call__(self, frame: np.ndarray) -> SpeechFrame:
        if self._using_silero:
            try:
                p = self._silero(frame)
                return SpeechFrame(p >= self.THRESHOLD, p)
            except Exception as e:                 # noqa: BLE001
                log.warning("silero failed mid-session, falling back: %s", e)
                self._using_silero = False
                self._energy.reset()
        p = self._energy(frame)
        return SpeechFrame(p >= self.THRESHOLD, p)


# ============================================================================
# Turn-end detection
# ============================================================================
# smart-turn takes the tail of the utterance, not all of it: what makes an
# ending an ending is local.
TURN_CONTEXT_MS = 8000


@dataclass
class TurnVerdict:
    complete: bool
    p: float
    tier: str


class _SmartTurn:
    """pipecat-ai/smart-turn-v3 via onnxruntime, if the weights are present.

    Whisper-tiny encoder plus a linear head, 8M parameters, ~12ms on CPU,
    BSD-2-Clause. It answers from the waveform, so it hears the intonation of a
    trailing "and..." that a transcript-only rule cannot.

    The graph is INTROSPECTED rather than assumed: this code has never been run
    against the real file (it cannot be fetched from here), so a shape it does
    not recognise must mark itself unavailable and let the heuristic run, not
    throw on the first utterance of a real conversation.
    """

    def __init__(self) -> None:
        self._sess = None
        self._input: str | None = None
        self._want: int | None = None
        self._err: str | None = None
        self._ok = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        if self._ok:
            return True
        if self._err is not None:
            return False
        if not SMART_TURN_PATH.exists():
            self._err = f"weights missing ({SMART_TURN_PATH})"
            return False
        try:
            with self._lock:
                if self._sess is None:
                    self._sess = _session(SMART_TURN_PATH)
            inputs = self._sess.get_inputs()
            if len(inputs) != 1:
                self._err = f"expected 1 input, got {[i.name for i in inputs]}"
                return False
            spec = inputs[0]
            self._input = spec.name
            # A fixed final dimension means the model wants exactly that many
            # samples; a symbolic one means it will take what it is given.
            last = spec.shape[-1] if spec.shape else None
            self._want = last if isinstance(last, int) and last > 0 else None
            self._ok = True
            log.info(
                "smart-turn loaded from %s (input=%s shape=%s)",
                SMART_TURN_PATH, spec.name, spec.shape,
            )
            return True
        except Exception as e:                       # noqa: BLE001
            self._err = f"{type(e).__name__}: {e}"
            log.warning("smart-turn unavailable: %s", self._err)
            return False

    def __call__(self, audio: np.ndarray) -> float:
        x = audio.astype(np.float32)
        if self._want:
            # Right-align: the end of the utterance is the part that decides.
            if x.shape[0] >= self._want:
                x = x[-self._want:]
            else:
                x = np.concatenate([np.zeros(self._want - x.shape[0], np.float32), x])
        out = self._sess.run(None, {self._input: x.reshape(1, -1)})[0]
        v = np.asarray(out, dtype=np.float32).reshape(-1)
        if v.size == 1:
            p = float(v[0])
            # A single logit rather than a probability.
            return p if 0.0 <= p <= 1.0 else float(1.0 / (1.0 + np.exp(-p)))
        # Two-class output: softmax, class 1 == complete.
        e = np.exp(v - v.max())
        return float((e / e.sum())[-1])

    @property
    def error(self) -> str | None:
        return self._err


# Words that, ending an utterance, mean it is not finished. Used only by the
# fallback. This is a hand-written 1% version of what smart-turn does properly,
# and it is kept honest about that.
_CONTINUATIONS = frozenset("""
and but or so because although though which that if when while the a an to of
for with as um uh er like then my your is are was were i we at in on by from
into about not no yes just really very my our their his her its this these
""".split())

_OPEN_PUNCT = tuple(",;:([{\"'-")


class TurnEndDetector:
    """Has the operator finished their turn?

    Asked at every VAD pause. The contract is deliberately narrow: given the
    utterance audio and its transcript so far, and how long the current silence
    has run, return whether to endpoint now.
    """

    # Ask as soon as a pause is plausibly an ending. Below this it is a breath.
    MIN_SILENCE_MS = 160
    # Nobody waits forever. A pause this long ends the turn whatever any model
    # thinks, so a wrong verdict can cost patience but never the conversation.
    MAX_SILENCE_MS = 4000
    THRESHOLD = 0.5

    def __init__(self) -> None:
        self._model = _SmartTurn()
        self._using_model = self._model.available

    @property
    def tier(self) -> str:
        return "smart-turn" if self._using_model else "heuristic"

    def __call__(
        self, audio: np.ndarray, text: str, silence_ms: int, *, patience: float = 1.0
    ) -> TurnVerdict:
        if silence_ms >= self.MAX_SILENCE_MS * patience:
            return TurnVerdict(True, 1.0, "ceiling")
        if silence_ms < self.MIN_SILENCE_MS:
            return TurnVerdict(False, 0.0, self.tier)
        if self._using_model:
            try:
                p = self._model(audio)
                # `patience` biases the threshold: the "how soon it replies"
                # setting is one number, applied here, instead of four timers.
                return TurnVerdict(p >= self.THRESHOLD * patience, p, "smart-turn")
            except Exception as e:                 # noqa: BLE001
                log.warning("smart-turn failed mid-session, falling back: %s", e)
                self._using_model = False
        return self._heuristic(text, silence_ms, patience)

    def _heuristic(self, text: str, silence_ms: int, patience: float) -> TurnVerdict:
        """Silence ladder crossed with linguistic completeness.

        The ladder is the point: an utterance that LOOKS finished endpoints fast,
        one that looks unfinished is given real time before it is cut off. That
        is the behaviour a single `endpoint_silence_ms` could never produce, and
        it is most of what the model buys, without the model.
        """
        t = (text or "").strip()
        if not t:
            return TurnVerdict(silence_ms >= int(900 * patience), 0.0, "heuristic")
        low = t.lower()
        unfinished = low.endswith(_OPEN_PUNCT) or (low.split()[-1] if low.split() else "") in _CONTINUATIONS
        terminal = t.endswith((".", "!", "?", "…"))
        if unfinished:
            need = 2200                            # they are mid-clause; wait
            p = 0.15
        elif terminal:
            need = 380                             # a finished sentence; go
            p = 0.9
        else:
            need = 900                             # plausible, unpunctuated
            p = 0.55
        need = int(need * patience)
        return TurnVerdict(silence_ms >= need, p, "heuristic")


# ============================================================================
def status() -> dict:
    """What is actually running, for the health route and the settings sheet.

    Constructing the detectors is what probes them, and both are cheap to build
    (an unavailable one never loads a session), so this reports the truth rather
    than a configured intent.
    """
    sd = SpeechDetector()
    td = TurnEndDetector()
    return {
        "vad": {
            "tier": sd.tier,
            "model": str(SILERO_PATH),
            "present": SILERO_PATH.exists(),
            "error": sd._silero.error,
        },
        "turn_end": {
            "tier": td.tier,
            "model": str(SMART_TURN_PATH),
            "present": SMART_TURN_PATH.exists(),
            "error": td._model.error,
        },
    }
