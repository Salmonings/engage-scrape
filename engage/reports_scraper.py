"""Scrape assessment reports from MyReportComments.aspx.

Each reporting period's full rendered HTML is fetched via RenderSimpleSection
and saved both to the DB (reports table) and to reports/<year>_<id>.html on
disk so it can be opened in a browser.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from . import config, store
from .client import requests_session

_BASE = config.BASE_URL
_PERIODS_SVC = "/Services/PupilAssessmentServices.asmx/GetReportingAssessmentReportingPeriods"
_SUBJECTS_SVC = "/Services/PupilAssessmentServices.asmx/GetPupilSubjects"
_RENDER_SVC = "/Services/PupilAssessmentServices.asmx/RenderSimpleSection"
_REPORTS_DIR = config.ROOT / "reports"


def _make_session():
    s = requests_session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Referer": _BASE + "/VLE/MyReportComments.aspx",
    })
    return s


def _get_encrypted_pupil_id(s) -> str:
    """Extract encryptedPupilID from hidden field ctl00_PageContent_hdnPupilID."""
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


def scrape_reports(verbose: bool = True) -> int:
    """Scrape all assessment reporting periods. Returns count of periods saved."""
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
        raise RuntimeError("Could not find encryptedPupilID on MyReportComments.aspx — is the session valid?")
    log(f"encryptedPupilID: {enc_id}")

    # Get all reporting periods
    periods_r = _post(s, _PERIODS_SVC, {"encryptedPupilID": enc_id})
    periods = json.loads(periods_r["d"]) if "d" in periods_r else []
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

        # Get subjects to know the count
        subj_r = _post(s, _SUBJECTS_SVC, {
            "encryptedPupilId": enc_id,
            "reportingPeriod": pid,
            "academicYear": year,
        })
        subjects = json.loads(subj_r["d"]) if "d" in subj_r else []

        # Render all subjects for this period in one call
        render_r = _post(s, _RENDER_SVC, {
            "encryptedPupilID": enc_id,
            "academicYear": year,
            "reportingPeriodId": pid,
            "subjectIds": "",
            "sectionType": "PupilAssessments",
        })
        html = render_r.get("d", "") or ""

        if not html or len(html) < 50:
            log(f"  [{pid}] {name!r}: empty/error response, skipping")
            continue

        # Save HTML file
        safe_name = name.replace("/", "-").replace(" ", "_").replace("(", "").replace(")", "")
        filename = f"{year}_{pid}_{safe_name[:60]}.html"
        html_path = _REPORTS_DIR / filename
        # Wrap in a minimal HTML skeleton so the file is self-contained
        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{name}</title>
<style>body{{font-family:Arial,sans-serif;margin:2em;}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:6px 10px;text-align:left}}
th{{background:#f0f0f0}} tr:nth-child(even){{background:#fafafa}}</style>
</head>
<body>
<h2>{name} ({year}/{int(year)+1})</h2>
{html}
</body>
</html>"""
        html_path.write_text(full_html, encoding="utf-8")

        rows.append({
            "period_id": pid,
            "period_name": name,
            "academic_year": year,
            "n_subjects": len(subjects),
            "html": html,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
        log(f"  [{pid}] {name!r}: {len(subjects)} subjects, {len(html)} chars -> {html_path.name}")

    store.save_reports(rows)
    log(f"\nSaved {len(rows)} report(s) to DB + HTML files in {_REPORTS_DIR}")
    return len(rows)
