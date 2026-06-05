"""SQLite persistence + JSON export."""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS timetable (
    week_start TEXT, day TEXT, period TEXT, start TEXT, "end" TEXT,
    subject TEXT, room TEXT, teacher TEXT,
    UNIQUE(week_start, day, period, subject)
);
CREATE TABLE IF NOT EXISTS lessons (
    lesson_plan_id TEXT PRIMARY KEY,
    date TEXT, day TEXT, period TEXT, subject TEXT, klass TEXT, topic TEXT
);
CREATE TABLE IF NOT EXISTS homework (
    lesson_plan_id TEXT PRIMARY KEY,
    subject TEXT, date TEXT, due_date TEXT, topic TEXT, details TEXT, attachments TEXT
);
CREATE TABLE IF NOT EXISTS notices (
    notice_id TEXT PRIMARY KEY,
    subject TEXT, sent_by TEXT, sent_date TEXT, body TEXT, is_priority TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    period_id TEXT PRIMARY KEY,
    period_name TEXT, academic_year TEXT, n_subjects INTEGER,
    html TEXT, scraped_at TEXT
);
"""


_MIGRATIONS = [
    "ALTER TABLE homework ADD COLUMN due_date TEXT",
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Apply additive migrations idempotently.
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn


def _upsert(conn: sqlite3.Connection, table: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    quoted = ",".join(f'"{c}"' for c in cols)
    sql = f'INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({",".join("?" for _ in cols)})'
    conn.executemany(sql, [[r[c] for c in cols] for r in rows])
    conn.commit()
    return len(rows)


def save_timetable(rows):
    with connect() as c: return _upsert(c, "timetable", rows)
def save_lessons(rows):
    with connect() as c: return _upsert(c, "lessons", rows)
def save_homework(rows):
    with connect() as c: return _upsert(c, "homework", rows)
def save_notices(rows):
    with connect() as c: return _upsert(c, "notices", rows)
def save_reports(rows):
    with connect() as c: return _upsert(c, "reports", rows)


def fetch_all(table: str) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]


def export_json() -> None:
    data = {t: fetch_all(t) for t in ("timetable", "lessons", "homework", "notices", "reports")}
    config.EXPORT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported -> {config.EXPORT_FILE}")
