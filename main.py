#!/usr/bin/env python3
"""CLI for the Engage scraper.

    python main.py login              # log in once, save session
    python main.py probe              # verify auth (prints unread-mail count)
    python main.py discover           # map the portal -> discovery/report.md
    python main.py scrape             # fetch timetable + lessons + homework -> DB
    python main.py scrape 2           # scrape two weeks (covers A/B rotations)
    python main.py scrape_hw          # scrape ALL homework across all academic years
    python main.py scrape_hw current  # scrape only current year (2025/2026)
    python main.py scrape_hw 2024     # scrape a specific year
    python main.py scrape_notices     # scrape all notices from the mailbox
    python main.py scrape_reports     # scrape all assessment report periods -> DB + HTML files
    python main.py export             # write export.json
    python main.py ics                # write timetable.ics (Google/Apple Calendar)
    python main.py show               # print what's in the DB
"""
from __future__ import annotations

import sys

from engage import client, config, discover, parsers, store, tools


def cmd_login() -> None:
    client.login_and_save()


def cmd_probe() -> None:
    res = client.call_service("/Services/PupilMailService.asmx/GetUnreadCount")
    print("GetUnreadCount ->", res)
    print("Auth OK." if isinstance(res, dict) and "d" in res
          else "Unexpected response — run `python main.py login`.")


def cmd_discover() -> None:
    discover.run()


def cmd_scrape() -> None:
    """One browser session: timetable (N weeks), then lesson list -> details -> homework.

    `python main.py scrape`     -> current week
    `python main.py scrape 2`   -> this week + next (covers A/B rotations)
    """
    weeks = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
    with client.session_page() as page:
        # --- Timetable (one or more weeks) ---
        tt_rows = []
        for label, html in client.iterate_timetable_weeks(page, parsers.TIMETABLE_PATH, weeks):
            week_start = parsers.week_start_from_label(label)
            for e in parsers.parse_timetable(html):
                row = e.dict()
                row["week_start"] = week_start
                tt_rows.append(row)
            print(f"  timetable: {label or '(week)'} — {len(tt_rows)} rows so far")
        n_tt = store.save_timetable(tt_rows)

        # --- Collect lesson-detail links from list views ---
        detail_urls: list[str] = []
        for list_path in [parsers.LESSON_LIST_PATH, *parsers.HOMEWORK_LIST_PATHS]:
            client.goto_authenticated(page, f"{config.BASE_URL}/{list_path}")
            for u in parsers.extract_detail_links(page.content(), config.BASE_URL):
                if u not in detail_urls:
                    detail_urls.append(u)

        # --- Visit each lesson detail ---
        lessons, homeworks = [], []
        for i, url in enumerate(detail_urls, 1):
            client.goto_authenticated(page, url)
            lesson, hw = parsers.parse_lesson_detail(page.content())
            if lesson.subject:
                lessons.append(lesson.dict())
            if hw:
                homeworks.append(hw.dict())
            print(f"\r  lessons {i}/{len(detail_urls)}", end="", flush=True)
        print()

        n_le = store.save_lessons(lessons)
        n_hw = store.save_homework(homeworks)

    print(f"Stored: timetable={n_tt}, lessons={n_le}, homework={n_hw}")
    if n_tt == n_le == 0:
        print("Nothing parsed — re-run `discover` and check the *_PATH values in parsers.py.")


def cmd_scrape_hw() -> None:
    """Scrape homework. Optional arg: 'current', a year value (e.g. '2024'), or omit for all years."""
    from engage.hw_scraper import scrape_all_homework
    year_arg = sys.argv[2] if len(sys.argv) > 2 else None
    counts = scrape_all_homework(verbose=True, year_filter=year_arg)
    print(f"\nDone. lessons={counts['lessons']}, homework={counts['homework']}")


def cmd_scrape_notices() -> None:
    """Scrape all notices from the pupil mailbox."""
    from engage.notices_scraper import scrape_notices
    n = scrape_notices(verbose=True)
    print(f"\nDone. {n} notice(s) saved.")


def cmd_scrape_reports() -> None:
    """Scrape all assessment reporting periods -> DB + HTML files in reports/."""
    from engage.reports_scraper import scrape_reports
    n = scrape_reports(verbose=True)
    print(f"\nDone. {n} report period(s) saved.")


def cmd_export() -> None:
    store.export_json()


def cmd_ics() -> None:
    from engage.ics_export import export_ics
    from engage import store as _store
    path = export_ics()
    n_tt = len(_store.fetch_all("timetable"))
    n_hw = len(_store.fetch_all("homework"))
    print(f"Exported {n_tt} timetable + {n_hw} homework events -> {path}")


def cmd_show() -> None:
    tt = tools.get_timetable()
    hw = tools.get_homework()
    weeks = tools.list_weeks()
    print(f"Timetable ({len(tt)} entries across {len(weeks)} week(s)):")
    cur_week = cur_day = None
    for e in tt:
        if e.get("week_start") != cur_week:
            cur_week = e.get("week_start"); cur_day = None
            print(f"\n== Week of {cur_week or '?'} ==")
        if e["day"] != cur_day:
            cur_day = e["day"]
            print(f"  {cur_day}")
        print(f"    P{e['period']} {e['start']}-{e['end']}  {e['subject']:24} "
              f"{e['room'] or '—':22} {e['teacher']}")
    print(f"\nHomework ({len(hw)}):")
    for h in hw:
        extra = f"  [{h['attachments']}]" if h["attachments"] else ""
        print(f"  {h['date']:32} {h['subject']:16} {h['topic']}{extra}")
    if not hw:
        print("  (none outstanding)")


COMMANDS = {
    "login": cmd_login, "probe": cmd_probe, "discover": cmd_discover,
    "scrape": cmd_scrape, "scrape_hw": cmd_scrape_hw,
    "scrape_notices": cmd_scrape_notices, "scrape_reports": cmd_scrape_reports,
    "export": cmd_export, "ics": cmd_ics, "show": cmd_show,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        raise SystemExit(1)
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
