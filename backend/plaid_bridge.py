import asyncio
import json
import os
from pathlib import Path


PLAID_MCP_DIR = Path(os.environ.get("PLAID_MCP_DIR", "/home/clawd-a/clawdbot/plaid-mcp"))


async def _run_node(script: str, *args: str, timeout: float = 60.0) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "node", str(PLAID_MCP_DIR / script), *args,
        cwd=str(PLAID_MCP_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{script} timed out after {timeout}s")
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip() or f"{script} exited {proc.returncode}")
    return json.loads(stdout.decode())


async def balances(realtime: bool = False) -> dict:
    """Default (realtime=False) uses Plaid's cached `accountsGet` — sub-second.
    realtime=True forces a live per-bank `accountsBalanceGet` (slower; for an
    explicit refresh)."""
    return await _run_node("balances.js", *(["--realtime"] if realtime else []))


async def transactions(days: int = 30) -> dict:
    return await _run_node("transactions.js", "--days", str(days))
