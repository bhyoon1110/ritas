from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def isoformat_kst(value: datetime | None = None) -> str:
    return (value or now_kst()).isoformat(timespec="milliseconds")


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def timestamp_folder(value: datetime) -> str:
    milliseconds = value.microsecond // 1000
    offset = value.strftime("%z")
    return f"{value:%Y%m%dT%H%M%S}{milliseconds:03d}{offset}"

