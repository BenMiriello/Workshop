"""UUIDv7 generation (RFC 9562).

WHY APP-SIDE. PostgreSQL 18 ships a native `uuidv7()` and the container this
deployment runs (infra/postgres) is 18.6, so a column default would work here.
It is deliberately not used: a DDL default of `uuidv7()` pins the schema to
Postgres 18+, and a customer on RDS or an older managed instance could not run
the same migrations. Generating in Python keeps the schema portable to 13+,
which is requirement R3 in the data-model plan.

WHY v7 AND NOT v4. v4 is random, so inserts land at random points in the
primary key's B-tree, which causes page splits and index bloat under load. v7
puts a millisecond timestamp in the high bits, so new rows append to the right
edge of the index the way a bigserial would, while keeping the 128-bit keyspace
and client-side generation that a sequence cannot give.

Layout, most significant bit first:

    48 bits   unix_ts_ms, big-endian
     4 bits   version (0b0111)
    12 bits   rand_a
     2 bits   variant (0b10)
    62 bits   rand_b
"""
from __future__ import annotations

import os
import time
import uuid

__all__ = ["uuid7"]


def uuid7(*, _ms: int | None = None) -> uuid.UUID:
    """A new UUIDv7. `_ms` is for tests that need a fixed timestamp."""
    ms = int(time.time() * 1000) if _ms is None else int(_ms)
    if not 0 <= ms < (1 << 48):
        raise ValueError(f"timestamp out of range for uuidv7: {ms}")

    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits, 74 of them used
    rand_a = (rand >> 62) & 0x0FFF                # 12 bits
    rand_b = rand & ((1 << 62) - 1)               # 62 bits

    value = (
        (ms << 80)
        | (0x7 << 76)          # version 7
        | (rand_a << 64)
        | (0b10 << 62)         # RFC 4122 variant
        | rand_b
    )
    return uuid.UUID(int=value)
