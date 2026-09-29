"""Swiss local time for display and for "today".

The database stores UTC (timezone-aware). Pilots think in local time, and a
flying day is a local calendar day - a flight at 01:30 local on 30.09. must
not show up as 29.09. just because it's 23:30 UTC.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Zurich")


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Timezone-aware UTC. SQLite (tests) drops tzinfo on the way back."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    return as_utc(dt).astimezone(LOCAL_TZ) if dt is not None else None


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC start (inclusive) and end (exclusive) of a local calendar day."""
    start = datetime.combine(day, time(0), tzinfo=LOCAL_TZ)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_end_of_day(dt: datetime) -> datetime:
    """UTC time of the next local midnight after `dt`."""
    return local_day_bounds(to_local(dt).date())[1]


def combine_local(day: date, hhmm: str) -> datetime:
    """"14:05" on a local day -> UTC datetime. Raises ValueError on bad input."""
    hours, minutes = hhmm.strip().replace(".", ":").split(":")
    t = time(int(hours), int(minutes))
    return datetime.combine(day, t, tzinfo=LOCAL_TZ).astimezone(timezone.utc)


# ---- template filters


def fmt_time(dt: Optional[datetime]) -> str:
    local = to_local(dt)
    return local.strftime("%H:%M") if local else ""


def fmt_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = to_local(value).date()
    return value.strftime("%d.%m.%Y")


def fmt_datetime(dt: Optional[datetime]) -> str:
    local = to_local(dt)
    return local.strftime("%d.%m.%Y %H:%M") if local else ""


def fmt_duration(minutes: Optional[float]) -> str:
    """Flight time the way logbooks write it: 1:05 (h:mm)."""
    if minutes is None:
        return ""
    total = int(round(minutes))
    return f"{total // 60}:{total % 60:02d}"


WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def fmt_weekday(day: date) -> str:
    return WEEKDAYS[day.weekday()]
