from __future__ import annotations

from app.time_utils import KST, parse_datetime


def test_parse_datetime_assumes_kst_for_naive_values() -> None:
    parsed = parse_datetime("2026-06-20T10:00:00")

    assert parsed.tzinfo == KST


def test_parse_datetime_preserves_aware_offset() -> None:
    parsed = parse_datetime("2026-06-20T10:00:00+09:00")

    assert parsed.utcoffset().total_seconds() == 9 * 3600
