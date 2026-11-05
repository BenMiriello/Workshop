"""A local server's refusal of an over-long prompt, in the Anthropic API's words.

llama.cpp's server refuses a prompt longer than the model's window with its own
error: a 400 of type `exceed_context_size_error` carrying `n_prompt_tokens` and
`n_ctx`. The CLI does not read that as a full window: it retries the request and
ends the turn with "API Error: 400", and a compaction that is itself too long
fails the same way, so the chat cannot go on. It does read the API's own
wording, `prompt is too long: N tokens > M maximum`, as one: it compacts and
sends the request again, and a compaction that is itself too long is sent again
with its oldest messages dropped. Measured against CLI 2.1.299 with a scripted
server on 2026-10-14.

The hop in front of the local server (`routes/local_llm`) answers with
`anthropic_error` in place of the server's own.
"""
from __future__ import annotations

import json
import re

_TYPE = "exceed_context_size_error"
_SAID = re.compile(r"\((\d+) tokens\) exceeds the available context size "
                   r"\((\d+) tokens\)")


def parse(status: int, raw: bytes) -> tuple[int, int] | None:
    """`(prompt tokens, window)` when `raw` is a server's refusal of a prompt
    longer than the model's window, else None."""
    if status != 400:
        return None
    try:
        err = json.loads(raw or b"{}").get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        n, m = err.get("n_prompt_tokens"), err.get("n_ctx")
        if err.get("type") == _TYPE and isinstance(n, int) and isinstance(m, int):
            return n, m
        said = str(err.get("message") or "")
    else:
        said = raw.decode("utf-8", "replace")
    got = _SAID.search(said)
    return (int(got.group(1)), int(got.group(2))) if got else None


def anthropic_error(status: int, raw: bytes) -> dict | None:
    """The 400 body the CLI compacts on, or None when `raw` is not a refusal
    of an over-long prompt."""
    got = parse(status, raw)
    if got is None:
        return None
    return {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": f"prompt is too long: {got[0]} tokens > {got[1]} maximum"}}
