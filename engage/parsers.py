"""Parsers tailored to the El Gouna Engage pupil portal.

Confirmed structure (from discovery):
  Timetable : VLE/WeeklyTimetable.aspx  -> table#tblTimeTable_...weeklyTimetable
              rows = days, cells = lessons; each lesson cell .ttLessonText is
              <strong>PERIOD</strong> then <br>-separated: subject, time, room, teacher
  Lessons   : vle/lessoninfo.aspx?aview=all  -> list of <a ...lessonPlanID=...>
  Detail    : lessoninfo.aspx?load=1&lessonPlanID=...  -> labelled fields + homeworkDiv
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Page paths (relative to BASE_URL).
TIMETABLE_PATH = "VLE/WeeklyTimetable.aspx"
LESSON_LIST_PATH = "vle/lessoninfo.aspx?aview=all"
HOMEWORK_LIST_PATHS = ["vle/lessoninfo.aspx?aview=withAssignments",
                       "vle/lessoninfo.aspx?aview=overdue"]

_TT_TABLE_ID = "tblTimeTable_ctl00_PageContent_weeklyTimetable"
_PFX = "ctl00_PageContent_"

# Cells that aren't real lessons.
_SKIP = ("registration", "break", "lunch", "half-ter", "half term", "assembly")


@dataclass
class TimetableEntry:
    day: str
    period: str
    start: str
    end: str
    subject: str
    room: str
    teacher: str
    def dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class LessonEntry:
    date: str
    day: str
    period: str
    subject: str
    klass: str
    topic: str
    lesson_plan_id: str
    def dict(self) -> dict[str, Any]: return asdict(self)


@dataclass
class HomeworkEntry:
    subject: str
    date: str       # set-on: lbAssignmentStartDate  e.g. "19/05/2026, Period 2"
    due_date: str   # hand-in: lbAssignmentHandInDate e.g. "Wednesday, Period 1, 20/05/2026"
    topic: str
    details: str
    attachments: str          # comma-joined filenames
    lesson_plan_id: str
    def dict(self) -> dict[str, Any]: return asdict(self)


def _label(soup: BeautifulSoup, suffix: str) -> str:
    el = soup.find(id=_PFX + suffix)
    return el.get_text(" ", strip=True) if el else ""


# ---------------- Timetable ----------------

def _split_cell(cell) -> dict[str, str] | None:
    """A .ttLessonText cell -> {period, subject, start, end, room, teacher}."""
    lt = cell.find(class_="ttLessonText")
    if not lt:
        return None
    period = (lt.find("strong").get_text(strip=True) if lt.find("strong") else "")
    # Text segments separated by <br>.
    parts, buf = [], []
    for node in lt.children:
        if getattr(node, "name", None) == "br":
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
        elif getattr(node, "name", None) == "strong":
            continue  # already captured as period
        else:
            buf.append(node.get_text() if hasattr(node, "get_text") else str(node))
    seg = "".join(buf).strip()
    if seg:
        parts.append(seg)
    # parts ~ [subject, "HH:MM - HH:MM", room, teacher]
    subject = parts[0] if parts else ""
    low = subject.lower()
    if not subject or any(s in low for s in _SKIP):
        return None
    start = end = room = teacher = ""
    rest = parts[1:]
    for p in rest:
        m = re.match(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", p)
        if m and not start:
            start, end = m.group(1), m.group(2)
            break
    leftovers = [p for p in rest if not re.match(r"\d{1,2}:\d{2}\s*-", p)]
    # Teachers carry a title (Mr/Mrs/Ms/Miss/Dr/Fr/Mme); rooms don't.
    title = re.compile(r"^(mr|mrs|ms|miss|dr|fr|mme)\b", re.I)
    for p in leftovers:
        if title.match(p) and not teacher:
            teacher = p
        elif not room:
            room = p
    return {"period": period, "subject": subject, "start": start,
            "end": end, "room": room, "teacher": teacher}


def parse_timetable(html: str) -> list[TimetableEntry]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=_TT_TABLE_ID)
    if not table:
        return []
    out: list[TimetableEntry] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        day = cells[0].get_text(" ", strip=True)
        if not day or day.lower() in ("",):
            continue
        # Header row has time ranges in cell 0? day cell would be empty -> skip
        if re.match(r"\d{1,2}:\d{2}", day):
            continue
        for cell in cells[1:]:
            parsed = _split_cell(cell)
            if parsed:
                out.append(TimetableEntry(day=day, **parsed))
    return out


# ---------------- Lessons / Homework ----------------

def extract_detail_links(html: str, base_url: str) -> list[str]:
    """All lesson-detail URLs (those carrying a lessonPlanID), deduped."""
    soup = BeautifulSoup(html, "html.parser")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "lessonplanid" in href.lower():
            absu = urljoin(base_url + "/", href)
            key = re.search(r"lessonPlanID=([^&]+)", absu, re.I)
            k = key.group(1) if key else absu
            if k not in seen:
                seen.add(k)
                out.append(absu)
    return out


def _parse_date_field(raw: str) -> tuple[str, str]:
    """'Tuesday, Period 1, 02/06/2026' -> (day, period)."""
    day = raw.split(",")[0].strip() if raw else ""
    m = re.search(r"period\s*(\w+)", raw, re.I)
    return day, (m.group(1) if m else "")


def parse_lesson_detail(html: str) -> tuple[LessonEntry, HomeworkEntry | None]:
    soup = BeautifulSoup(html, "html.parser")
    subject = _label(soup, "lbLessonDetailsSubject")
    klass = _label(soup, "lbLessonDetailsClass")
    topic = _label(soup, "lbLessonDetailsLesson")

    # Assignment pages use lbAssignmentStartDate; plain lesson pages use lbLessonDetailsDate.
    date_raw = _label(soup, "lbAssignmentStartDate") or _label(soup, "lbLessonDetailsDate")
    day, period = _parse_date_field(date_raw)

    hid = soup.find(id=_PFX + "hdnLessonPlanID")
    lpid = hid.get("value", "") if hid else ""
    # '0' means "no lesson plan ID" for postback-navigation pages.
    if not lpid or lpid == "0":
        aid = soup.find(id=_PFX + "hdnAssignmentID")
        if aid:
            lpid = aid.get("value", "")

    lesson = LessonEntry(date=date_raw, day=day, period=period, subject=subject,
                         klass=klass, topic=topic, lesson_plan_id=lpid)

    # Homework / assignment fields (only present on pages with assignmentID in URL).
    due_date = _label(soup, "lbAssignmentHandInDate")
    hw_text = _label(soup, "rpbAssignment_i0_i0_lbAssignmentAssignmentDetails")
    attach_panel = soup.find(id=_PFX + "rpbAssignment_i0_i0_pnlAssignmentAttachments")
    attach = ""
    if attach_panel:
        names = [a.get_text(strip=True) for a in attach_panel.find_all("a", class_="js-file-uploaded")]
        attach = ", ".join(n for n in names if n)

    homework = None
    if due_date or hw_text or attach:
        homework = HomeworkEntry(subject=subject, date=date_raw, due_date=due_date,
                                 topic=topic, details=hw_text, attachments=attach,
                                 lesson_plan_id=lpid)
    return lesson, homework


def parse_week_label(html: str) -> str:
    """e.g. 'Week Beginning 31/05/2026'."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(id="ctl00_PageContent_weeklyTimetable_wsTimetableDate_lblWeek")
    return el.get_text(" ", strip=True) if el else ""


def week_start_from_label(label: str) -> str:
    """'Week Beginning 31/05/2026' -> ISO '2026-05-31' (sorts correctly)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", label or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (label or "")
