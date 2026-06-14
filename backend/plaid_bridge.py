import asyncio
import json
import os
from pathlib import Path


PLAID_MCP_DIR = Path(os.environ.get("PLAID_MCP_DIR", "/home/clawd-a/clawdbot/plaid-mcp"))

# These vars come from plaid-auth.env and are loaded by the node scripts via
# dotenv. dotenv does NOT override existing env vars, so if uvicorn was started
# with empty PLAID_* vars (e.g. from a stale systemd/nohup env), those empty
# strings shadow the file values. Strip them from the subprocess env so dotenv
# can load the correct values from the auth file.
_STRIP_FROM_NODE_ENV = {"PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV"}


async def _run_node(script: str, *args: str, timeout: float = 60.0) -> dict:
    node_env = {k: v for k, v in os.environ.items() if k not in _STRIP_FROM_NODE_ENV}
    proc = await asyncio.create_subprocess_exec(
        "node", str(PLAID_MCP_DIR / script), *args,
        cwd=str(PLAID_MCP_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=node_env,
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
