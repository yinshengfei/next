"""Chat timestamps, including legacy SQLite UTC values."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CHAT_TIMEZONE = ZoneInfo("Asia/Shanghai")


def chat_now():
    return datetime.now(CHAT_TIMEZONE).isoformat(timespec="seconds")


def chat_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CHAT_TIMEZONE).isoformat(timespec="seconds")
