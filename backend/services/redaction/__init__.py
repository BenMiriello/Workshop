"""Redaction service — Ollama-driven PII detection + replacement, with a
regex pre-pass for high-confidence patterns and a string-substitute restore.

Two-stage redaction:
    1. Regex pre-pass (deterministic, fast): SSN, credit-card, bank-account,
       email, phone, common address forms. Each match gets a placeholder
       like `<<PII_NAME_0>>` and the mapping is preserved.
    2. Ollama pass (qwen2.5:32b): given the partially-redacted text from
       step 1, identify remaining contextual PII (operator names, locations,
       company-specific identifiers) and emit a JSON {redacted_text, mapping}.

The two mappings merge. The combined mapping is small enough to be passed
around with the redacted text — typical ratio 5-50 entries per 1KB of text.

Restore is straightforward string substitution. We use uppercase-bracketed
placeholders so they survive Claude's response unchanged (Claude doesn't
typically rewrite text in that form).

If Ollama is down or GPU-busy, the regex pre-pass still runs — partial
redaction is better than none for the obvious patterns. The caller can
inspect `result["ollama_used"]` to decide whether to proceed with cloud
escalation or wait for Ollama recovery.
"""
from .redactor import redact, restore, RedactionResult
from .patterns import REGEX_PATTERNS, regex_redact

__all__ = ["redact", "restore", "RedactionResult", "REGEX_PATTERNS", "regex_redact"]
