"""Hybrid logical clock (HLC) stamps for synced changes.

Format: "<ms:013d>:<counter:05d>:<node_id>" - wall-clock milliseconds, a
counter, then the node that made the change. Fixed-width numbers make plain
string comparison the clock order, so stamps sort correctly in Python and in
SQL (ORDER BY hlc), and the node id breaks ties deterministically.

Local changes are stamped inside PostgreSQL by sdai_hlc_now() (see
sync.capture) - this module is the Python side of the same format: parsing,
comparison and the "receive" rule. A node that applies a remote change
stamped ahead of its own clock raises its floor (sdai_sync_state.
hlc_floor_ms) past it, so every later local change sorts after everything
it has seen, even with a skewed wall clock.
"""

from __future__ import annotations

import re
import threading
import time
from typing import NamedTuple

_HLC_RE = re.compile(r"^(\d{13}):(\d{5}):([a-z0-9][a-z0-9_-]{0,39})$")

COUNTER_MAX = 99_999


class Hlc(NamedTuple):
    ms: int
    counter: int
    node: str

    def __str__(self) -> str:
        return format_hlc(self.ms, self.counter, self.node)


def format_hlc(ms: int, counter: int, node: str) -> str:
    if not 0 <= counter <= COUNTER_MAX:
        raise ValueError(f"HLC counter out of range: {counter}")
    if not 0 <= ms < 10**13:
        raise ValueError(f"HLC milliseconds out of range: {ms}")
    return f"{ms:013d}:{counter:05d}:{node}"


def parse_hlc(value: str) -> Hlc:
    match = _HLC_RE.match(value or "")
    if not match:
        raise ValueError(f"Not an HLC stamp: {value!r}")
    return Hlc(int(match[1]), int(match[2]), match[3])


def is_hlc(value: object) -> bool:
    return isinstance(value, str) and bool(_HLC_RE.match(value))


def hlc_ms(value: str) -> int:
    return parse_hlc(value).ms


def max_hlc(*values: str | None) -> str | None:
    present = [v for v in values if v]
    return max(present) if present else None


def floor_after(remote: str) -> int:
    """The floor (ms) a node must raise its clock to after seeing `remote`,
    so its next local stamp sorts after it."""
    return parse_hlc(remote).ms + 1


class HybridClock:
    """In-process HLC with the same format as sdai_hlc_now(). Used where
    Python itself stamps something (tests, tools); monotonic per instance."""

    def __init__(self, node: str, wall_ms=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", node):
            raise ValueError(f"Invalid node id: {node!r}")
        self.node = node
        self._wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self._ms = 0
        self._counter = 0
        self._lock = threading.Lock()

    def now(self) -> str:
        with self._lock:
            wall = self._wall_ms()
            if wall > self._ms:
                self._ms, self._counter = wall, 0
            else:
                self._advance_counter()
            return format_hlc(self._ms, self._counter, self.node)

    def observe(self, remote: str) -> None:
        """Receive rule: never stamp at or before a change already seen."""
        stamp = parse_hlc(remote)
        with self._lock:
            if (stamp.ms, stamp.counter) >= (self._ms, self._counter):
                self._ms, self._counter = stamp.ms, stamp.counter

    def _advance_counter(self) -> None:
        if self._counter >= COUNTER_MAX:
            self._ms, self._counter = self._ms + 1, 0
        else:
            self._counter += 1
