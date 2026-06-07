"""Two-stage redactor: regex pre-pass + Ollama contextual pass.

The Ollama pass runs only if Ollama is available and the text is non-trivial
(>50 chars of unredacted body). For short snippets the regex pass alone is
usually enough — the Ollama call adds ~5s latency we want to avoid when it
won't add value.

The Ollama prompt is intentionally narrow: short input, strict JSON output,
single tool. This matches the operator's design rule that Ollama is used for
"small tasks without too much asked of it."
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from services.llm_router import call_local, OllamaUnavailable
from .patterns import regex_redact


log = logging.getLogger("workshop.redaction")


@dataclass
class RedactionResult:
    redacted_text: str
    mapping: dict[str, str]
    regex_hits: int
    ollama_hits: int
    ollama_used: bool
    elapsed_s: float

    def summary(self) -> str:
        return (
            f"redacted: regex={self.regex_hits} ollama={self.ollama_hits} "
            f"elapsed={self.elapsed_s:.2f}s ollama_used={self.ollama_used}"
        )


_OLLAMA_REDACTION_PROMPT = """You are a privacy redactor. Identify PII in the TEXT below that I missed, and
replace it with bracketed placeholders. Categories to detect:
- PERSON: people's first/last names (but NOT public-figure references like "Apple", "Anthropic")
- LOCATION: street addresses, neighborhoods, specific landmarks (NOT cities/states/countries)
- COMPANY: small-private-business names (NOT public companies like "Stripe", "Plaid")
- ID: opaque identifiers that look like account IDs, customer IDs, internal refs

Already-redacted placeholders like `<<PII_*_N>>` must be preserved unchanged.

Rules:
- Output ONLY a JSON object: {"redacted":"...","mapping":{"<<PII_X_N>>":"original"}}
- Start placeholder counter from the number I give you as START_INDEX.
- If you find nothing new, output {"redacted":"<text unchanged>","mapping":{}}.
- Do NOT explain. Do NOT add prose. JSON only.

START_INDEX: {start_idx}

TEXT:
{text}

JSON:"""


def _ollama_pass(text: str, start_idx: int) -> tuple[str, dict[str, str]]:
    """Run the Ollama redaction call. On any failure (down, parse-fail), return
    the input unchanged + empty mapping. Caller checks ollama_used flag."""
    prompt = _OLLAMA_REDACTION_PROMPT.replace("{start_idx}", str(start_idx)).replace("{text}", text)
    try:
        r = call_local(prompt, json_mode=True, timeout_s=180, purpose="redaction")
    except OllamaUnavailable as e:
        log.info("redaction: ollama unavailable, skipping contextual pass: %s", e)
        return text, {}

    raw = r["text"].strip()
    # Try to find a JSON object even if wrapped in prose
    if not raw.startswith("{"):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        raw = m.group(0) if m else "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("redaction: ollama returned malformed JSON, length=%d", len(raw))
        return text, {}

    redacted = data.get("redacted") or text
    mapping = data.get("mapping") or {}
    # Validate mapping shape — keys must be `<<PII_*_N>>` strings
    clean_mapping: dict[str, str] = {}
    for k, v in mapping.items():
        if isinstance(k, str) and isinstance(v, str) and k.startswith("<<PII_") and k.endswith(">>"):
            clean_mapping[k] = v
    return redacted, clean_mapping


def _rebuild_from_mapping(text: str, mapping: dict[str, str]) -> str:
    """Replace every value-string in the original text with its placeholder.
    Longest values first so substring overlaps don't cause partial matches.
    This is the AUTHORITATIVE redaction: we don't trust whatever Ollama wrote
    as `redacted` — we use only the mapping it produced (after dedupe/repair)
    and apply it deterministically to the original text."""
    # Sort by descending length so "1247 Oak Street, Mountain View" gets
    # replaced before "Mountain View" alone.
    items = sorted(mapping.items(), key=lambda kv: len(kv[1]), reverse=True)
    out = text
    for ph, value in items:
        if value and value in out:
            out = out.replace(value, ph)
    return out


def redact(text: str, *, skip_ollama: bool = False) -> RedactionResult:
    """Two-stage redact. Pass `skip_ollama=True` to do regex only.

    Caller usually wants the default. If you know the text is short / trivial,
    skipping the Ollama pass shaves ~3-5s.
    """
    import time
    start = time.time()
    _, regex_mapping = regex_redact(text, start_idx=0)
    regex_hits = len(regex_mapping)

    ollama_hits = 0
    ollama_used = False
    ollama_mapping: dict[str, str] = {}
    if not skip_ollama and len(text) > 50:
        # Build the partially-redacted text from regex_mapping, feed it to Ollama
        partial = _rebuild_from_mapping(text, regex_mapping)
        _, raw_ollama_mapping = _ollama_pass(partial, start_idx=regex_hits)
        ollama_used = bool(raw_ollama_mapping)

        # Repair: drop any key Ollama tried to recycle from regex_mapping.
        # Drop any value that's already in regex_mapping's values (Ollama
        # double-counting). Drop any value that doesn't actually occur in
        # the original text (Ollama hallucination).
        regex_keys = set(regex_mapping.keys())
        regex_values = set(regex_mapping.values())
        for k, v in raw_ollama_mapping.items():
            if k in regex_keys:
                continue
            if v in regex_values:
                continue
            if v not in text:
                continue
            ollama_mapping[k] = v
        ollama_hits = len(ollama_mapping)

    # Final mapping = regex ∪ repaired-ollama
    final_mapping = {**regex_mapping, **ollama_mapping}

    # Rebuild the redacted text deterministically from the original + final mapping.
    final_redacted = _rebuild_from_mapping(text, final_mapping)

    return RedactionResult(
        redacted_text=final_redacted,
        mapping=final_mapping,
        regex_hits=regex_hits,
        ollama_hits=ollama_hits,
        ollama_used=ollama_used,
        elapsed_s=round(time.time() - start, 3),
    )


def restore(text: str, mapping: dict[str, str]) -> str:
    """Substitute placeholders back. Idempotent — if a placeholder isn't in
    the text, nothing happens. If a placeholder appears multiple times in
    `text`, all copies get restored.

    Order matters: substitute longer placeholders first so substring overlaps
    don't cause partial restores.
    """
    if not mapping:
        return text
    keys = sorted(mapping.keys(), key=len, reverse=True)
    for k in keys:
        text = text.replace(k, mapping[k])
    return text
