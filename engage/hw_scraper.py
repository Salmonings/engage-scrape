"""Scrape all homework assignments across all academic years.

Navigation hierarchy:
  Subject list (hdnLevel=Subject)
    → click subject (by ID, via JS to bypass viewport)
  Assignment list (hdnLevel=Lessons)  -- when subject has multiple assignments
    → click assignment (by ID, via JS to bypass viewport)
  Assignment detail (hdnLevel=Detail)
    → parse + back via imgBackDetailsLevel (exists only at Detail level)

Strategy: navigate fresh per subject (avoids Lessons→Subject back issue).
Within a subject, use JS clicks + imgBackDetailsLevel for Detail→Lessons cycling.
"""
from __future__ import annotations

from playwright.sync_api import Page

from . import client, config, parsers, store

YEAR_VALUES = ["2017", "2019", "2020", "2021", "2022", "2023", "2024", "2025"]
_CURRENT_YEAR = "2025"  # 2025/2026 academic year

_BASE = config.BASE_URL + "/vle/lessoninfo.aspx?aview=withAssignments"
_YEAR_SEL = "select#ctl00_PageContent_rcbAcademicYear"
_ALL_TILE = "#ctl00_PageContent_pnlPurpleTile"
_BACK = "#ctl00_PageContent_imgBackDetailsLevel"
_SUBJ = 'a[id*="rptSubjects"][id*="lnkSubject"]'
_ASSIGN = 'a[id*="rptLessons"][id*="lnkAssignment"]'
_LEVEL = "ctl00_PageContent_hdnLevel"
_MODE = "ctl00_PageContent_hdnMode"


def _get(page: Page, field_id: str) -> str:
    try:
        return (page.locator(f"#{field_id}").get_attribute("value") or "").strip()
    except Exception:
        return ""


def _wait_level(page: Page, level: str, timeout: int = 20_000) -> None:
    page.wait_for_function(
        f"() => (document.getElementById('{_LEVEL}')?.value ?? '') === '{level}'",
        timeout=timeout,
    )


def _wait_not_level(page: Page, level: str, timeout: int = 20_000) -> None:
    page.wait_for_function(
        f"() => (document.getElementById('{_LEVEL}')?.value ?? '') !== '{level}'",
        timeout=timeout,
    )


def _nav_to_year(page: Page, year: str) -> None:
    """Navigate to the subject list for the given year in All mode."""
    client.goto_authenticated(page, _BASE)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(800)

    page.select_option(_YEAR_SEL, value=year)
    page.wait_for_load_state("networkidle", timeout=25_000)
    page.wait_for_timeout(800)
    page.wait_for_load_state("networkidle", timeout=15_000)

    # Switch to All mode if the portal defaulted to WithAssignments (current year).
    if _get(page, _MODE) != "All":
        page.click(_ALL_TILE)
        page.wait_for_load_state("networkidle", timeout=20_000)
        page.wait_for_timeout(800)


def _js_click(page: Page, element_id: str) -> None:
    """Trigger a click via JS — bypasses viewport/visibility constraints."""
    page.evaluate(f"() => document.getElementById('{element_id}')?.click()")


def _parse_detail(page: Page) -> tuple[dict | None, dict | None]:
    html = page.content()
    lesson, hw = parsers.parse_lesson_detail(html)
    return (lesson.dict() if lesson.subject else None, hw.dict() if hw else None)


def scrape_all_homework(verbose: bool = True, year_filter: str | None = None) -> dict:
    """Scrape homework from all (or one) academic year(s) and persist to DB.

    Args:
        year_filter: If provided, scrape only this year value (e.g. "2025").
                     Pass "current" for the current year (2025/2026).
    Returns {'lessons': N, 'homework': N}.
    """
    if year_filter == "current":
        year_filter = _CURRENT_YEAR
    years = [year_filter] if year_filter else YEAR_VALUES

    lessons_total = 0
    hw_total = 0

    def log(msg: str) -> None:
        if verbose:
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)

    with client.session_page() as page:
        for year in years:
            # Initial navigation to get subject IDs for this year.
            _nav_to_year(page, year)

            n_subj = page.locator(_SUBJ).count()
            log(f"Year {year}: {n_subj} subject(s)")
            if n_subj == 0:
                continue

            subject_names = [
                page.locator(_SUBJ).nth(i).inner_text().strip()
                for i in range(n_subj)
            ]

            for subj_name in subject_names:
                if not subj_name:
                    continue

                # Fresh navigation per subject avoids Lessons→Subject back issues.
                # Re-locate by name (not ID) since positional IDs vary between loads.
                _nav_to_year(page, year)
                subj_el = page.locator(_SUBJ).filter(has_text=subj_name).first
                subj_el.evaluate("el => el.click()")
                page.wait_for_load_state("networkidle", timeout=20_000)
                page.wait_for_timeout(600)
                try:
                    _wait_not_level(page, "Subject", timeout=15_000)
                except Exception:
                    pass

                level = _get(page, _LEVEL)

                if level == "Lessons":
                    n_assign = page.locator(_ASSIGN).count()
                    assign_ids = [
                        page.locator(_ASSIGN).nth(ai).get_attribute("id") or ""
                        for ai in range(n_assign)
                    ]
                    log(f"  {subj_name}: {n_assign} assignment(s)")

                    for ai, assign_id in enumerate(assign_ids):
                        if not assign_id:
                            continue

                        _js_click(page, assign_id)
                        page.wait_for_load_state("networkidle", timeout=20_000)
                        page.wait_for_timeout(600)
                        try:
                            _wait_level(page, "Detail", timeout=15_000)
                        except Exception:
                            pass

                        ld, hd = _parse_detail(page)
                        if ld:
                            store.save_lessons([ld])
                            lessons_total += 1
                        if hd:
                            store.save_homework([hd])
                            hw_total += 1
                        log(
                            f"    [{ai + 1}/{n_assign}] {(ld or {}).get('topic', '?')!r}"
                            f"{' -> HW' if hd else ''}"
                        )

                        # Back to Lessons (imgBackDetailsLevel exists at Detail level).
                        _js_click(page, "ctl00_PageContent_imgBackDetailsLevel")
                        page.wait_for_load_state("networkidle", timeout=15_000)
                        page.wait_for_timeout(600)
                        try:
                            _wait_level(page, "Lessons", timeout=15_000)
                        except Exception:
                            pass

                elif level == "Detail":
                    ld, hd = _parse_detail(page)
                    log(
                        f"  {subj_name}: single detail"
                        f"{' -> HW' if hd else ' (no HW)'}"
                    )
                    if ld:
                        store.save_lessons([ld])
                        lessons_total += 1
                    if hd:
                        store.save_homework([hd])
                        hw_total += 1

                else:
                    log(f"  {subj_name}: unexpected level {level!r}, skipping")

    return {"lessons": lessons_total, "homework": hw_total}
