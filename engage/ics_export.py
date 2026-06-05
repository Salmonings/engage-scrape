"""Export timetable + homework from the local DB to a .ics file.

Timetable entries become timed VEVENTs (floating time — no TZID, so the
calendar app uses the device's local timezone, which is correct for a
student always in the same location).

Homework entries become all-day VEVENTs on the lesson date, tagged "HW:".
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from icalendar import Calendar, Event

from . import config, store

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _week_day_to_date(week_start: str, day_name: str) -> date | None:
    """Return the concrete date for day_name within the week starting on week_start."""
    try:
        ws = date.fromisoformat(week_start)
    except (ValueError, TypeError):
        return None
    target = _WEEKDAYS.get(day_name.lower())
    if target is None:
        return None
    # modulo ensures we always go forward (max +6 days) regardless of week_start weekday
    delta = (target - ws.weekday()) % 7
    return ws + timedelta(days=delta)


def _parse_hm(t: str) -> time | None:
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return time(int(m.group(1)), int(m.group(2))) if m else None


def _parse_dmy(raw: str) -> date | None:
    """DD/MM/YYYY anywhere in the string -> date."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", raw or "")
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _uid(s: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, s)) + "@engagescrape"


def build_calendar() -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//engagescrape//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "School Timetable")
    cal.add("x-wr-timezone", "")  # floating — inherit from device

    skipped_tt = skipped_hw = 0

    for row in store.fetch_all("timetable"):
        d = _week_day_to_date(row.get("week_start", ""), row.get("day", ""))
        t_start = _parse_hm(row.get("start", ""))
        t_end = _parse_hm(row.get("end", ""))
        if not (d and t_start and t_end):
            skipped_tt += 1
            continue

        ev = Event()
        subj = row.get("subject") or "Lesson"
        period = row.get("period") or ""
        ev.add("summary", f"{subj} (P{period})" if period else subj)
        ev.add("dtstart", datetime.combine(d, t_start))
        ev.add("dtend", datetime.combine(d, t_end))
        if row.get("room"):
            ev.add("location", row["room"])
        if row.get("teacher"):
            ev.add("description", row["teacher"])
        ev.add("uid", _uid(f"{row.get('week_start')}-{row.get('day')}-{period}-{subj}"))
        cal.add_component(ev)

    for row in store.fetch_all("homework"):
        # Prefer the hand-in due date; fall back to set-on date if missing.
        d = _parse_dmy(row.get("due_date") or row.get("date", ""))
        if not d:
            skipped_hw += 1
            continue

        ev = Event()
        subj = row.get("subject") or "Homework"
        topic = row.get("topic") or ""
        label = "HW Due" if row.get("due_date") else "HW"
        ev.add("summary", f"{label}: {subj} – {topic}" if topic else f"{label}: {subj}")
        ev.add("dtstart", d)
        ev.add("dtend", d + timedelta(days=1))
        desc_parts = [p for p in (row.get("details"), row.get("attachments") and f"Attachments: {row['attachments']}") if p]
        if desc_parts:
            ev.add("description", "\n\n".join(desc_parts))
        ev.add("uid", _uid(f"hw-{row.get('lesson_plan_id', row.get('date', ''))}"))
        cal.add_component(ev)

    if skipped_tt or skipped_hw:
        import sys
        print(f"  (skipped {skipped_tt} timetable + {skipped_hw} homework rows with missing date/time data)", file=sys.stderr)

    return cal


def export_ics(path: Path | None = None) -> Path:
    if path is None:
        path = config.ROOT / "timetable.ics"
    cal = build_calendar()
    path.write_bytes(cal.to_ical())
    return path
