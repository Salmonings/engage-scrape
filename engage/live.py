"""Live-fetch functions for the API server (no DB interaction).

All functions take an Engage session state dict (from client.login_with_credentials
or the API's /auth/login token store) and fetch data directly from the portal.
"""
from __future__ import annotations

from . import client, config, parsers


def fetch_timetable(state: dict, weeks: int = 1) -> list[dict]:
    """Fetch timetable live using the provided session state."""
    rows = []
    with client.session_page_from_state(state) as page:
        for label, html in client.iterate_timetable_weeks(page, parsers.TIMETABLE_PATH, weeks):
            week_start = parsers.week_start_from_label(label)
            for entry in parsers.parse_timetable(html):
                row = entry.dict()
                row["week_start"] = week_start
                rows.append(row)
    return rows


def fetch_homework(state: dict) -> list[dict]:
    """Fetch all currently listed homework (all tiles) using the provided session state."""
    homeworks = []
    with client.session_page_from_state(state) as page:
        detail_urls: list[str] = []
        for list_path in parsers.HOMEWORK_LIST_PATHS:
            page.goto(f"{config.BASE_URL}/{list_path}", wait_until="networkidle")
            for url in parsers.extract_detail_links(page.content(), config.BASE_URL):
                if url not in detail_urls:
                    detail_urls.append(url)
        for url in detail_urls:
            page.goto(url, wait_until="networkidle")
            _, hw = parsers.parse_lesson_detail(page.content())
            if hw:
                homeworks.append(hw.dict())
    return homeworks
