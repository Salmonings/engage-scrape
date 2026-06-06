"""Assessment reports scraper — MyReportComments.aspx.

Public API
----------
fetch_reports_meta()         Live fetch period list (no HTML). Used by API server.
fetch_report_detail(pid)     Live fetch one period's full HTML. Used by API server.
scrape_reports()             Fetch all + save to DB + write HTML files. Used by main.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from . import client, config, store
from .client import requests_session

_BASE = config.BASE_URL
_PERIODS_SVC = "/Services/PupilAssessmentServices.asmx/GetReportingAssessmentReportingPeriods"
_SUBJECTS_SVC = "/Services/PupilAssessmentServices.asmx/GetPupilSubjects"
_RENDER_SVC = "/Services/PupilAssessmentServices.asmx/RenderSimpleSection"
_REPORTS_DIR = config.ROOT / "reports"


def _make_session(state: dict | None = None):
    s = client.requests_session_from_state(state) if state else requests_session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Referer": _BASE + "/VLE/MyReportComments.aspx",
    })
    return s


def _get_encrypted_pupil_id(s) -> str:
    r = s.get(_BASE + "/VLE/MyReportComments.aspx")
    soup = BeautifulSoup(r.text, "html.parser")
    hid = soup.find(id="ctl00_PageContent_hdnPupilID")
    return hid.get("value", "") if hid else ""


def _post(s, endpoint: str, payload: dict):
    r = s.post(_BASE + endpoint, data=json.dumps(payload))
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:200], "_status": r.status_code}


def _get_periods(s, enc_id: str) -> list[dict]:
    r = _post(s, _PERIODS_SVC, {"encryptedPupilID": enc_id})
    return json.loads(r["d"]) if "d" in r else []


def _get_subjects(s, enc_id: str, pid: str, year: str) -> list[dict]:
    r = _post(s, _SUBJECTS_SVC, {
        "encryptedPupilId": enc_id,
        "reportingPeriod": pid,
        "academicYear": year,
    })
    return json.loads(r["d"]) if "d" in r else []


def _render_html(s, enc_id: str, pid: str, year: str) -> str:
    r = _post(s, _RENDER_SVC, {
        "encryptedPupilID": enc_id,
        "academicYear": year,
        "reportingPeriodId": pid,
        "subjectIds": "",
        "sectionType": "PupilAssessments",
    })
    return r.get("d", "") or ""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def fetch_reports_meta(state: dict | None = None) -> list[dict]:
    """Live fetch all reporting periods (metadata only, no HTML)."""
    s = _make_session(state)
    enc_id = _get_encrypted_pupil_id(s)
    if not enc_id:
        return []
    periods = _get_periods(s, enc_id)
    return [
        {
            "period_id": str(p["ReportingPeriodId"]),
            "period_name": p.get("Name", ""),
            "academic_year": str(p.get("AcademicYear", "")),
            "is_locked": p.get("IsLocked", True),
        }
        for p in sorted(periods, key=lambda p: p.get("AcademicYear", 0), reverse=True)
    ]


def fetch_report_detail(period_id: str, state: dict | None = None) -> dict | None:
    """Live fetch one period's full rendered HTML + subject count."""
    s = _make_session(state)
    enc_id = _get_encrypted_pupil_id(s)
    if not enc_id:
        return None
    periods = _get_periods(s, enc_id)
    period = next((p for p in periods if str(p["ReportingPeriodId"]) == period_id), None)
    if not period:
        return None
    pid = str(period["ReportingPeriodId"])
    year = str(period["AcademicYear"])
    subjects = _get_subjects(s, enc_id, pid, year)
    html = _render_html(s, enc_id, pid, year)
    if not html or len(html) < 50:
        return None
    return {
        "period_id": pid,
        "period_name": period.get("Name", ""),
        "academic_year": year,
        "n_subjects": len(subjects),
        "html": html,
    }


def scrape_reports(verbose: bool = True) -> int:
    """Fetch all reporting periods, save to DB and HTML files. Returns count saved."""
    s = _make_session()
    _REPORTS_DIR.mkdir(exist_ok=True)

    def log(msg: str) -> None:
        if verbose:
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)

    enc_id = _get_encrypted_pupil_id(s)
    if not enc_id:
        raise RuntimeError("Could not find encryptedPupilID — is the session valid?")

    periods = _get_periods(s, enc_id)
    if not periods:
        log("No reporting periods found.")
        return 0

    log(f"Found {len(periods)} reporting period(s):")
    for p in periods:
        log(f"  {p['ReportingPeriodId']} ({p['AcademicYear']}) — {p['Name']}")

    rows = []
    for p in periods:
        pid = str(p["ReportingPeriodId"])
        year = str(p["AcademicYear"])
        name = p.get("Name", "")
        subjects = _get_subjects(s, enc_id, pid, year)
        html = _render_html(s, enc_id, pid, year)

        if not html or len(html) < 50:
            log(f"  [{pid}] {name!r}: empty/error, skipping")
            continue

        safe_name = name.replace("/", "-").replace(" ", "_").replace("(", "").replace(")", "")
        html_path = _REPORTS_DIR / f"{year}_{pid}_{safe_name[:60]}.html"
        full_html = (
            f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{name}</title>'
            f"<style>body{{font-family:Arial,sans-serif;margin:2em}}"
            f"table{{border-collapse:collapse;width:100%}}"
            f"th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left}}"
            f"th{{background:#f0f0f0}}tr:nth-child(even){{background:#fafafa}}</style></head>"
            f"<body><h2>{name} ({year}/{int(year)+1})</h2>{html}</body></html>"
        )
        html_path.write_text(full_html, encoding="utf-8")

        rows.append({
            "period_id": pid,
            "period_name": name,
            "academic_year": year,
            "n_subjects": len(subjects),
            "html": html,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
        log(f"  [{pid}] {name!r}: {len(subjects)} subjects -> {html_path.name}")

    store.save_reports(rows)
    return len(rows)
