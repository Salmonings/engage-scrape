"""Chatbot-friendly accessors over the stored data. Read the local DB (offline);
run `main.py scrape` to refresh. Register these as LLM tools if you like."""
from __future__ import annotations

from . import store

_DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


def get_timetable(day: str | None = None, week: str | None = None) -> list[dict]:
    """Timetable rows. `day` filters by weekday; `week` filters by week_start
    (ISO 'YYYY-MM-DD' or any substring). With no week filter, returns all stored
    weeks, each row tagged with its week_start."""
    rows = store.fetch_all("timetable")
    if day:
        rows = [r for r in rows if r["day"].lower() == day.lower()]
    if week:
        rows = [r for r in rows if week in (r.get("week_start") or "")]
    rows.sort(key=lambda r: (r.get("week_start") or "",
                             _DAYS.index(r["day"].lower()) if r["day"].lower() in _DAYS else 9,
                             r["start"]))
    return rows


def list_weeks() -> list[str]:
    return sorted({r.get("week_start") or "" for r in store.fetch_all("timetable")})


def get_homework() -> list[dict]:
    return sorted(store.fetch_all("homework"), key=lambda r: r["date"])


def get_lessons(subject: str | None = None) -> list[dict]:
    rows = store.fetch_all("lessons")
    if subject:
        rows = [r for r in rows if subject.lower() in r["subject"].lower()]
    return sorted(rows, key=lambda r: r["date"])


TOOL_SCHEMAS = [
    {"name": "get_timetable",
     "description": "School timetable; optional day filter (e.g. 'Monday').",
     "input_schema": {"type": "object", "properties": {"day": {"type": "string"}}}},
    {"name": "get_homework",
     "description": "Homework/assignments with details and attachment names.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_lessons",
     "description": "Lesson records (topic, subject, date); optional subject filter.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}}}},
]
