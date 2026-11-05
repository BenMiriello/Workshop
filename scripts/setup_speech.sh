#!/usr/bin/env bash
# setup_speech.sh -- fetch the speech models, and optionally build the GPU
# environment speech borrows the card with.
#
#   bash scripts/setup_speech.sh          models for the CPU engine
#   bash scripts/setup_speech.sh --gpu    also the GPU environment and models
#
# Not required: the engine fetches its CPU models on first use. Running this
# ahead of time means the first dictation does not wait on a download.
#
#   Parakeet TDT 0.6B v3 (CC-BY-4.0)  speech to text, ONNX via onnx-asr
#   Kokoro-82M (Apache-2.0)           text to speech, ONNX via kokoro-onnx
#   Silero VAD v5 (MIT)               voice mode's speech detector
#   EmbeddingGemma 300M (Gemma terms) commands worded freely, ONNX 4-bit
#
# The GPU environment is its own venv under the runtime dir because
# onnxruntime-gpu and the CPU onnxruntime the backend uses cannot share one.
# Safe to run again: anything already in place is kept.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/backend/.venv/bin/python"
RUNTIME="${WORKSHOP_RUNTIME_DIR:-$ROOT/.runtime}"
MODELS="$RUNTIME/speech/models"
GPU_ENV="$RUNTIME/speech/gpu-env"
mkdir -p "$MODELS" "$RUNTIME/voice/models"
cd "$ROOT/backend"

"$PY" -m services.speech.models --fetch "$MODELS" --quantization int8 \
  || { echo "speech: CPU model fetch failed" >&2; exit 1; }

VAD="$RUNTIME/voice/models/silero_vad.onnx"
if [[ ! -s "$VAD" ]]; then
  curl -fsSL -o "$VAD.part" \
    https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx
  mv "$VAD.part" "$VAD"
fi

"$PY" -c "from services.speech.meaning import shared as m; m.embedder.load(); raise SystemExit(bool(m.embedder.failed))" \
  || { echo "speech: command model fetch failed" >&2; exit 1; }

[[ "${1:-}" == "--gpu" ]] || exit 0

if ! "$GPU_ENV/bin/python" -c "import onnx_asr, kokoro_onnx, onnxruntime, huggingface_hub" 2>/dev/null; then
  BASE_PY="$(command -v python3.12 || command -v python3)"
  [[ -x "$GPU_ENV/bin/python" ]] || "$BASE_PY" -m venv "$GPU_ENV"
  "$GPU_ENV/bin/python" -m pip install -q --upgrade pip
  "$GPU_ENV/bin/python" -m pip install -q "onnx-asr==0.12.0" "kokoro-onnx==0.6.1" \
    "onnxruntime-gpu[cuda,cudnn]==1.23.2" "numpy==2.5.1" huggingface_hub
  # kokoro-onnx depends on the CPU onnxruntime; both install the same module,
  # so drop it and reinstall the GPU build over what it left behind.
  "$GPU_ENV/bin/python" -m pip uninstall -q -y onnxruntime || true
  "$GPU_ENV/bin/python" -m pip install -q --force-reinstall --no-deps \
    "onnxruntime-gpu==1.23.2"
fi
"$GPU_ENV/bin/python" -m services.speech.models --fetch "$MODELS" --quantization none
echo "speech: GPU environment ready at $GPU_ENV"
