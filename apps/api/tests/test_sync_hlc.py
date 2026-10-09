"""Hybrid logical clock format and rules (GED Phase 8.1)."""

import pytest
from sync.hlc import (
    COUNTER_MAX,
    HybridClock,
    floor_after,
    format_hlc,
    is_hlc,
    max_hlc,
    parse_hlc,
)


def test_format_is_fixed_width_and_parses_back():
    stamp = format_hlc(1_700_000_000_123, 42, "local-ndj-01")
    assert stamp == "1700000000123:00042:local-ndj-01"
    assert parse_hlc(stamp) == (1_700_000_000_123, 42, "local-ndj-01")
    assert str(parse_hlc(stamp)) == stamp
    assert is_hlc(stamp)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1700000000123:42:local",
        "170000000012:00042:local",
        "1700000000123:00042:Local",
        None,
    ],
)
def test_rejects_malformed(bad):
    assert not is_hlc(bad)
    with pytest.raises(ValueError):
        parse_hlc(bad)


def test_string_order_is_clock_order():
    stamps = [
        format_hlc(999, 5, "zz"),
        format_hlc(1000, 0, "aa"),
        format_hlc(1000, 1, "aa"),
        format_hlc(1000, 1, "bb"),  # same instant: node id breaks the tie
        format_hlc(10_000_000_000, 0, "aa"),
    ]
    assert sorted(stamps) == stamps
    assert max_hlc(None, stamps[1], stamps[0]) == stamps[1]
    assert max_hlc(None, None) is None


def test_clock_is_monotonic_when_wall_clock_stalls_or_goes_back():
    wall = iter([1000, 1000, 999, 2000])
    clock = HybridClock("local", wall_ms=lambda: next(wall))
    a, b, c, d = (clock.now() for _ in range(4))
    assert a < b < c < d
    assert parse_hlc(c).ms == 1000 and parse_hlc(d) == (2000, 0, "local")


def test_observe_moves_past_a_remote_stamp_ahead_of_us():
    clock = HybridClock("local", wall_ms=lambda: 1000)
    remote = format_hlc(5000, 7, "cloud")
    clock.observe(remote)
    assert clock.now() > remote
    assert floor_after(remote) == 5001


def test_counter_overflow_rolls_into_next_millisecond():
    clock = HybridClock("local", wall_ms=lambda: 1000)
    clock.observe(format_hlc(1000, COUNTER_MAX - 1, "cloud"))
    assert parse_hlc(clock.now()) == (1000, COUNTER_MAX, "local")
    assert parse_hlc(clock.now()) == (1001, 0, "local")


def test_invalid_node_rejected():
    with pytest.raises(ValueError):
        HybridClock("Bad Node")
