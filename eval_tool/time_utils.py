from __future__ import annotations

from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _saigon_timezone() -> tzinfo:
    try:
        return ZoneInfo("Asia/Ho_Chi_Minh")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=7), name="Asia/Saigon")


SAIGON_TZ = _saigon_timezone()
