"""Text embedding provider: local-first and self-contained.

ONE interface, selected by `WORKSHOP_EMBED_PROVIDER` (default "local"). The local
provider bundles a small quantized ONNX model and runs it IN-PROCESS via
fastembed: no daemon, no API key, no network at inference time, free. It is the
default in every deployment, cloud included, on the operator's call to run
embeddings locally everywhere and defer a hosted provider.

A hosted provider (OpenAI/Voyage/...) can be added behind this same interface
later; none is built yet, so `WORKSHOP_EMBED_PROVIDER` set to anything but
"local" fails loudly rather than pretending.

THE INVARIANT: vectors from different models live in different spaces and are
NOT comparable. Any durable store MUST record `provider().name` alongside its
vectors and refuse to mix models. Changing the model is a full re-embed, never
an in-place conversion. `semantic_index` enforces this by rebuilding when the
recorded name no longer matches.

This replaces the Ollama `/api/embed` and `/api/embeddings` call paths that used
to back semantic search and task dedup. Ollama was a separate server process, a
single machine's daemon that cannot be assumed in a cloud tenant or a desktop
install.
"""
from __future__ import annotations

import os
import threading

# bge-small-en-v1.5: MIT, 384-d, ~32 MB int8 ONNX, retrieval quality above
# ada-002 for its size, and fastembed's own default model. Overridable, but the
# override must name a model fastembed can load; an unknown name fails at load.
DEFAULT_LOCAL_MODEL = os.environ.get("WORKSHOP_LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Known output dimension per local model. bge-small-en-v1.5 and all-MiniLM-L6-v2
# are both 384-d; bge-base and nomic are 768-d. Used only to answer `dim` before
# the model has loaded (e.g. for an empty-index matrix shape); the real width is
# always whatever the model returns.
_LOCAL_DIMS = {
    "BAAI/bge-small-en-v1.5": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "nomic-ai/nomic-embed-text-v1.5": 768,
}


class LocalEmbedder:
    """In-process ONNX embedder via fastembed. The model is loaded lazily on the
    first embed and held for the process lifetime (loading is the expensive
    part, inference is milliseconds)."""

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"local:{self.model_name}"

    @property
    def dim(self) -> int:
        return _LOCAL_DIMS.get(self.model_name, 384)

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure()
        # fastembed returns unit-normalised numpy rows; hand back plain floats so
        # callers never depend on numpy at the boundary.
        return [[float(x) for x in vec] for vec in model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        # Symmetric: the query is embedded exactly like a document, so index and
        # query share one space. bge-small performs well without a query
        # instruction, and adding one to queries only would move them out of the
        # documents' space.
        return self.embed_documents([text])[0]


_provider: LocalEmbedder | None = None
_provider_lock = threading.Lock()


def provider() -> LocalEmbedder:
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                kind = os.environ.get("WORKSHOP_EMBED_PROVIDER", "local").strip().lower()
                if kind in ("", "local"):
                    _provider = LocalEmbedder(DEFAULT_LOCAL_MODEL)
                else:
                    raise RuntimeError(
                        f"WORKSHOP_EMBED_PROVIDER={kind!r} is not built. Only the "
                        "local provider exists; hosted providers were deferred. "
                        "Unset the variable or set it to 'local'."
                    )
    return _provider


def embed_documents(texts: list[str]) -> list[list[float]]:
    return provider().embed_documents(texts)


def embed_query(text: str) -> list[float]:
    return provider().embed_query(text)


def provider_name() -> str:
    return provider().name


def provider_dim() -> int:
    return provider().dim


def available() -> tuple[bool, str]:
    """Whether embedding can run right now. For the local provider this loads the
    model (once) and does a tiny embed, so a missing dependency or a failed
    model download surfaces as a nameable reason rather than an opaque error at
    the first real call."""
    try:
        provider().embed_documents(["ok"])
        return True, ""
    except Exception as e:  # noqa: BLE001 - the reason is the payload
        return False, f"{type(e).__name__}: {e}"
