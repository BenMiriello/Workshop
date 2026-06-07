"""System health tool — self-contained for Letta sandbox."""
from __future__ import annotations


def system_health() -> str:
    """Report current system health for tier-routing decisions.

    Returns:
        Multi-line summary: Ollama up?, GPU util%, GPU free MB,
        subscription auth state. The agent uses this to decide whether
        to call local tools vs escalate to cloud.
    """
    import os
    import shutil
    import subprocess
    import httpx

    # Ollama up?
    ollama_up = False
    try:
        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3)
        ollama_up = r.status_code == 200
    except Exception:
        pass

    # GPU
    gpu_util = "?"
    gpu_free_mb = "?"
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                util_s, free_s = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
                gpu_util = util_s
                gpu_free_mb = free_s
        except Exception:
            pass

    # Subscription auth
    sub_ok = "ANTHROPIC_API_KEY" not in os.environ

    return (
        f"Ollama: up={ollama_up} model=qwen2.5:32b gpu_util={gpu_util}% gpu_free={gpu_free_mb}MB\n"
        f"Cloud: subscription_auth_ok={sub_ok} (haiku/sonnet/opus via claude -p)"
    )
