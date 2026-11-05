"""Understanding a command worded freely (docs/dictation-and-clipboard.md,
"Commands in any wording"): in commands mode, and after the trigger word, an
utterance that is none of the phrases is matched by what it means.

Every utterance is turned into an embedding by EmbeddingGemma 300M (4-bit ONNX,
on the processor, about 15 ms; the int8 export runs at about 100), and a small classifier over those embeddings,
trained in well under a second on each command's phrases plus the wordings in
services/speech/command_examples.py, gives the probability of each command
and of "none". The classifier is retrained whenever the commands change,
including the actions the page on screen declares (services/speech/
app_actions.py), and the last few are kept. The
client keeps only the commands that make sense on the page and decides whether
to act, ask, or let it go (frontend/src/components/shell/speech/guess.ts).

Chosen over a small language model and over the "decision" models that answer
typed questions (Jev and its open counterparts) by measurement on this kind of
utterance: docs/proposals/voice-control-research-2026-10-31.md, section 6.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from services.speech.command_examples import EXAMPLES, NONE
from services.speech.commands import TYPE, Table
from services.speech.modes import heard_in, when_of
from wsplatform import paths

log = logging.getLogger("workshop.speech.meaning")

REPO = "onnx-community/embeddinggemma-300m-ONNX"
FILES = ("onnx/model_q4.onnx", "onnx/model_q4.onnx_data", "tokenizer.json")
TEMPLATE = "task: classification | query: {}"
THREADS = 2
MAX_TOKENS = 64
SHOWN = 5
# Classifiers kept, one per distinct set of commands: each page's declared
# actions make their own, and going back to a page should not retrain.
KEEP_MODELS = 8
# New example phrases a guess reads itself (a page's few declared actions);
# more than this is learned in the background instead.
LEARN_INLINE = 24
# Never said loosely: resume is the one command heard while paused, and
# guesses are only made while listening.
NOT_GUESSED = frozenset({"resume"})


def model_dir() -> Path:
    return paths.runtime_dir() / "speech" / "models" / "embeddinggemma-300m"


class Embedder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._session = None
        self._tok = None
        self._lock = threading.Lock()
        self.failed: str | None = None

    @property
    def ready(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Fetch the model the first time, then load it. Safe to call often."""
        with self._lock:
            if self._session is not None:
                return
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                if not all((self.root / f).exists() for f in FILES):
                    from huggingface_hub import snapshot_download

                    snapshot_download(REPO, allow_patterns=list(FILES), local_dir=self.root)
                tok = Tokenizer.from_file(str(self.root / "tokenizer.json"))
                tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 0, pad_token="<pad>")
                tok.enable_truncation(MAX_TOKENS)
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = THREADS
                opts.inter_op_num_threads = 1
                self._session = ort.InferenceSession(str(self.root / FILES[0]), opts,
                                                     providers=["CPUExecutionProvider"])
                self._tok = tok
                self.failed = None
            except Exception as e:                        # noqa: BLE001
                self.failed = str(e)
                log.warning("command model unavailable: %s", e)

    def embed(self, texts: list[str], template: str = TEMPLATE) -> np.ndarray:
        """Unit vectors for `texts`, each put in `template` (the model's prompt
        for the task) first."""
        assert self._session is not None and self._tok is not None
        enc = self._tok.encode_batch([template.format(t) for t in texts])
        feeds = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                 "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
        names = [o.name for o in self._session.get_outputs()]
        out = self._session.run(None, feeds)
        v = out[names.index("sentence_embedding")] if "sentence_embedding" in names else out[-1]
        v = np.asarray(v, dtype=np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def examples_for(table: Table) -> dict[str, tuple[str, ...]]:
    """What each command heard in this table's mode is learned from, by label:
    a built-in's id, "type:<id>" for one of the operator's own, or the id of
    an action declared on screen ("app:<n>", from its phrases, title,
    description and examples)."""
    cmds = table.cmds or {}
    out: dict[str, tuple[str, ...]] = {}
    for b in cmds.get("builtins", []):
        if b["kind"] == "inline" or b["id"] in NOT_GUESSED or when_of(b) == "off" \
                or not heard_in(table.mode, b["id"], False):
            continue
        out[b["id"]] = tuple(b["phrases"]) + EXAMPLES.get(b["id"], ())
    for c in cmds.get("custom", []) if heard_in(table.mode, TYPE, False) else []:
        if when_of(c) != "off" and c.get("phrases"):
            out[f"{TYPE}:{c['id']}"] = tuple(c["phrases"])
    for a in cmds.get("app") or () if heard_in(table.mode, TYPE, False) else ():
        out[a["id"]] = tuple(dict.fromkeys(a["phrases"] + a["examples"]))
    out["none"] = NONE
    return out


def train(X: np.ndarray, y: np.ndarray, n: int, epochs: int = 400) -> tuple[np.ndarray, np.ndarray]:
    """Multinomial logistic regression on unit embeddings (scaled by 10), on
    THREADS threads: the matrices are small, and one thread per core fighting
    a busy machine took 15 s where two take 0.1."""
    with threadpool_limits(THREADS):
        return _train(X, y, n, epochs)


def _train(X: np.ndarray, y: np.ndarray, n: int, epochs: int) -> tuple[np.ndarray, np.ndarray]:
    Y = np.zeros((len(y), n), np.float32)
    Y[np.arange(len(y)), y] = 1
    W = np.zeros((X.shape[1], n), np.float32)
    b = np.zeros(n, np.float32)
    for _ in range(epochs):
        Z = X @ W * 10 + b
        P = np.exp(Z - Z.max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        G = (P - Y) / len(y)
        W -= 0.5 * (X.T @ G * 10 + 1e-3 * W)
        b -= 0.5 * G.sum(0)
    return W, b


class Understander:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._vecs: dict[str, np.ndarray] = {}
        self._models: dict[tuple, tuple[list[str], np.ndarray, np.ndarray]] = {}
        self._lock = threading.Lock()
        self._preparing: set[tuple] = set()

    def warm(self, table: Table | None = None) -> None:
        """Load the model, and learn `table`'s commands, off the caller's
        thread: the first time, that is every example phrase read once,
        several seconds on a busy processor."""
        key = tuple(sorted(examples_for(table).items())) if table is not None else None
        if key in self._preparing or (self.embedder.ready and (key is None or key in self._models)):
            return
        if key is not None:
            self._preparing.add(key)

        def run() -> None:
            try:
                self.embedder.load()
                if table is not None and self.embedder.ready:
                    examples = examples_for(table)
                    self._vectors([t for ts in examples.values() for t in ts])
                    with self._lock:
                        self._model(examples)
            finally:
                if key is not None:
                    self._preparing.discard(key)

        threading.Thread(target=run, name="speech-meaning", daemon=True).start()

    def _vectors(self, texts: list[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in self._vecs]
        if missing:
            for t, v in zip(missing, self.embedder.embed(missing)):
                self._vecs[t] = v
        return np.stack([self._vecs[t] for t in texts])

    def _model(self, examples: dict[str, tuple[str, ...]]) -> tuple[list[str], np.ndarray, np.ndarray]:
        key = tuple(sorted(examples.items()))
        if key not in self._models:
            labels = list(examples)
            texts = [t for lab in labels for t in examples[lab]]
            y = np.array([i for i, lab in enumerate(labels) for _ in examples[lab]])
            W, b = train(self._vectors(texts), y, len(labels))
            self._models[key] = (labels, W, b)
            while len(self._models) > KEEP_MODELS:
                del self._models[next(iter(self._models))]
        else:
            self._models[key] = self._models.pop(key)
        return self._models[key]

    def guess(self, words: str, table: Table) -> dict | None:
        """The commands `words` most likely mean, best first, with their
        probabilities and that of "none"; None while the model is not loaded.
        What does not apply now (`table.off`) is left out before the
        probabilities are taken, so what does apply keeps all of them. Never
        waits on learning a whole set of commands: that goes to warm(), and
        until it is done the answer is None, as while loading."""
        if not self.embedder.ready:
            self.warm(table)
            return None
        examples = examples_for(table)
        key = tuple(sorted(examples.items()))
        missing = {t for ts in examples.values() for t in ts} - self._vecs.keys()
        if key not in self._models and len(missing) > LEARN_INLINE:
            self.warm(table)
            return None
        with self._lock:
            labels, W, b = self._model(examples)
            z = self.embedder.embed([words]) @ W * 10 + b
        z[0, [i for i, lab in enumerate(labels) if lab in table.off]] = -np.inf
        p = np.exp(z[0] - z[0].max())
        p /= p.sum()
        custom = {f"{TYPE}:{c['id']}": c["text"] for c in (table.cmds or {}).get("custom", [])}
        order = [i for i in np.argsort(-p) if labels[i] != "none" and p[i] > 0][:SHOWN]
        guesses = [{"name": TYPE if labels[i] in custom else labels[i], "p": round(float(p[i]), 4),
                    **({"text": custom[labels[i]]} if labels[i] in custom else {})} for i in order]
        return {"guesses": guesses, "none": round(float(p[labels.index("none")]), 4)}


shared = Understander(Embedder(model_dir()))
