"""Notices scraper — PupilMailbox.aspx.

Public API
----------
fetch_notices(limit)   Live fetch, returns list[dict]. Used by the API server.
scrape_notices()       Fetch + save to DB. Used by `python main.py scrape_notices`.
"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from . import client, config, store
from .client import requests_session

_BASE = config.BASE_URL
_INBOX_SVC = "/Services/PupilMailService.asmx/GetPupilInbox"
_DETAIL_SVC = "/Services/PupilMailService.asmx/GetNoticeDetail"


def _make_session(state: dict | None = None):
    s = client.requests_session_from_state(state) if state else requests_session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Referer": _BASE + "/VLE/PupilMailbox.aspx",
    })
    return s


def _collect_ids(s, limit: int | None = None) -> list[str]:
    """Paginate the inbox and return notice IDs, newest first."""
    ids: list[str] = []
    pg = 0
    while True:
        if limit and len(ids) >= limit:
            break
        r = s.post(_BASE + _INBOX_SVC,
                   data=json.dumps({"pagesize": 10, "page": pg, "sort": "D", "search": ""}))
        data = r.json()
        if "d" not in data:
            break
        soup = BeautifulSoup(data["d"], "html.parser")
        page_ids = list(dict.fromkeys(
            el.get("data-notice")
            for el in soup.find_all(attrs={"data-notice": True})
            if el.get("data-notice")
        ))
        if not page_ids:
            break
        ids.extend(nid for nid in page_ids if nid not in ids)
        pg += 1
    return ids


def _fetch_detail(s, nid: str) -> dict | None:
    """Fetch one notice's full detail. Returns dict or None on failure."""
    r = s.post(_BASE + _DETAIL_SVC,
               data=json.dumps({
                   "noticeId": int(nid),
                   "isforward": False,
                   "isreply": False,
                   "isreplyall": False,
               }))
    data = r.json()
    if "d" not in data:
        return None
    d = json.loads(data["d"]) if isinstance(data["d"], str) else data["d"]
    body_html = d.get("Body", "")
    body_text = BeautifulSoup(body_html, "html.parser").get_text("\n", strip=True) if body_html else ""
    return {
        "notice_id": nid,
        "subject": d.get("Subject", ""),
        "sent_by": d.get("SentByName", ""),
        "sent_date": d.get("SentDate", ""),
        "body": body_text,
        "is_priority": d.get("IsPriority", "False"),
    }


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def fetch_notices(limit: int = 10, state: dict | None = None) -> list[dict]:
    """Fetch the most recent `limit` notices live from the portal."""
    s = _make_session(state)
    ids = _collect_ids(s, limit=limit)
    return [n for nid in ids[:limit] if (n := _fetch_detail(s, nid))]


def scrape_notices(verbose: bool = True) -> int:
    """Fetch all notices and save to DB. Returns count saved."""
    s = _make_session()

    def log(msg: str) -> None:
        if verbose:
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)

    ids = _collect_ids(s)
    log(f"Total unique notices: {len(ids)}")

    notices = []
    for i, nid in enumerate(ids, 1):
        n = _fetch_detail(s, nid)
        if n:
            notices.append(n)
            log(f"  [{i}/{len(ids)}] {n['sent_date']!r}  {n['subject']!r}  from {n['sent_by']!r}")

    store.save_notices(notices)
    return len(notices)
