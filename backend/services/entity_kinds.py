"""The platform's own entity kinds, declared once.

WHY THIS MODULE EXISTS. The tuple ("note", "doc", "chat", "ticket") was written
out three times -- `refs.READABLE_KINDS`, `semantic_index.PLATFORM_KINDS` and
`semantic_index.PLATFORM_DEFAULT_KINDS` -- and two of those spellings had
DIFFERENT element order, which is the exact drift `check_one_definition.py` was
written to catch after `CHAT_MODES` was found declared twice in disagreeing
orders. `refs.READABLE_KINDS` even carried a comment saying it was "exactly the
kinds the semantic index holds", which is a dependency stated in prose and
enforced by nobody.

WHY IT IS A LEAF. `refs` and `semantic_index` already import each other, each
from inside a function. A function-local import hides a cycle rather than
breaking one, so neither could own the constant without the other importing it
at module load. The shared thing moves DOWN instead: this module imports
nothing, so both can take it at module level and no cycle is possible.

THE THREE NAMES ARE NOT ALL THE SAME CONCEPT, which is why they keep their own
names where they are used. "The platform's corpora", "the corpora indexed
without being asked for" and "the kinds whose body can be read" are three
questions that happen to have one answer today. When one of them diverges it
gets its own literal back, and the drift is a deliberate edit rather than a copy
someone forgot.
"""
from __future__ import annotations

# The platform's own corpora. An app contributes more through
# `AppSpec.index_collectors`.
PLATFORM_KINDS: tuple[str, ...] = ("note", "doc", "chat", "ticket")
