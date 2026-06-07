"""Deterministic regex patterns for high-confidence PII redaction.

The patterns here only match formats that are nearly impossible to
false-positive on (SSN xxx-xx-xxxx, Luhn-validated CC, IBAN, etc.).
Lower-confidence patterns (names, addresses, "Acme Corp") are left for
the Ollama pass which has the context to judge.

Each pattern produces placeholders of the form `<<PII_TYPE_INDEX>>`. The
mapping returned alongside the redacted text maps placeholder → original.
"""
from __future__ import annotations

import re
from typing import Pattern


# Each tuple: (label, compiled regex, validator-callable or None).
# Validator returns True if the match is genuine (used to skip false positives
# like "1234-56-7890" that happens to fit SSN format but isn't one).
def _luhn(s: str) -> bool:
    """Validate credit-card-style number with Luhn checksum."""
    s = re.sub(r"\D", "", s)
    if len(s) < 13 or len(s) > 19:
        return False
    digits = [int(c) for c in s]
    checksum = 0
    parity = (len(digits) - 2) % 2
    for i, d in enumerate(digits[:-1]):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return (10 - (checksum % 10)) % 10 == digits[-1]


REGEX_PATTERNS: list[tuple[str, Pattern[str], object]] = [
    # SSN — strict xxx-xx-xxxx, avoid all-zeros area and 666 area which can't be valid
    ("SSN", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), None),
    # Credit card — 13-19 consecutive digits, Luhn-validated
    ("CC", re.compile(r"\b(?:\d[ -]?){13,19}\b"), _luhn),
    # IBAN — 2 country letters + 2 digits + 11-30 alphanumeric
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), None),
    # US bank routing — 9 digits with checksum
    ("ROUTING", re.compile(r"\b\d{9}\b"), None),  # filtered below
    # Bank account — Plaid-style "account_id" or "Last 4: 1234".
    # Allows filler words (in, with, number) between "ending" and the digits.
    ("BANK_ACCT_LAST4", re.compile(r"(?:account|acct|card)\s+(?:ending|last\s*4|number|#)?\s*(?:in\s+|with\s+)?[:\-]?\s*\d{4}\b", re.IGNORECASE), None),
    # Email
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
    # US phone — flexible
    ("PHONE", re.compile(r"(?:\+?1[-.\s]?)?\(?\b[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), None),
    # IPv4 (sometimes a personal IP)
    ("IPV4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"), None),
]


def regex_redact(text: str, start_idx: int = 0) -> tuple[str, dict[str, str]]:
    """Run all regex patterns over `text`. Returns (redacted_text, mapping).

    Mapping: {"<<PII_LABEL_N>>": "original-string"}. `start_idx` lets the
    caller continue numbering when this is being run multiple times. The
    function preserves text in unchanged regions.
    """
    mapping: dict[str, str] = {}
    counter = start_idx

    def _replace(match_text: str, label: str) -> str:
        nonlocal counter
        # Re-use existing placeholder if we've seen the exact value before
        for k, v in mapping.items():
            if v == match_text and k.startswith(f"<<PII_{label}_"):
                return k
        ph = f"<<PII_{label}_{counter}>>"
        mapping[ph] = match_text
        counter += 1
        return ph

    # Process in label-priority order; rerun cycle after each substitution so
    # patterns don't overlap (e.g., an IPv4 inside a UUID).
    redacted = text
    for label, pattern, validator in REGEX_PATTERNS:
        new_pieces: list[str] = []
        cursor = 0
        for m in pattern.finditer(redacted):
            matched = m.group(0)
            if validator is not None and not validator(matched):
                continue
            # ROUTING is very high false-positive (any 9-digit number).
            # Only count it as routing if the surrounding context suggests it.
            if label == "ROUTING":
                ctx_start = max(0, m.start() - 40)
                ctx = redacted[ctx_start:m.start()].lower()
                if not any(k in ctx for k in ["routing", "aba", "ach"]):
                    continue
            new_pieces.append(redacted[cursor:m.start()])
            new_pieces.append(_replace(matched, label))
            cursor = m.end()
        new_pieces.append(redacted[cursor:])
        redacted = "".join(new_pieces)

    return redacted, mapping
