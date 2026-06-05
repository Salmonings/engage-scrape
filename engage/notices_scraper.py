"""Scrape all notices from the pupil mailbox (PupilMailbox.aspx)."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from . import config, store
from .client import requests_session

_BASE = config.BASE_URL
_INBOX_SVC = "/Services/PupilMailService.asmx/GetPupilInbox"
_DETAIL_SVC = "/Services/PupilMailService.asmx/GetNoticeDetail"


def _make_session():
    s = requests_session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "Referer": _BASE + "/VLE/PupilMailbox.aspx",
    })
    return s


def scrape_notices(verbose: bool = True) -> int:
    """Scrape all notices from the mailbox inbox. Returns count saved."""
    s = _make_session()

    def log(msg: str) -> None:
        if verbose:
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)

    # Collect notice IDs across all inbox pages
    notice_ids: list[str] = []
    pg = 0
    while True:
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
        new = [nid for nid in page_ids if nid not in notice_ids]
        notice_ids.extend(new)
        log(f"  Inbox page {pg}: {len(page_ids)} notices ({len(new)} new)")
        pg += 1

    log(f"Total unique notices: {len(notice_ids)}")

    # Fetch each notice's detail
    notices = []
    for i, nid in enumerate(notice_ids, 1):
        r = s.post(_BASE + _DETAIL_SVC,
                   data=json.dumps({
                       "noticeId": int(nid),
                       "isforward": False,
                       "isreply": False,
                       "isreplyall": False,
                   }))
        data = r.json()
        if "d" not in data:
            continue
        d = json.loads(data["d"]) if isinstance(data["d"], str) else data["d"]

        body_html = d.get("Body", "")
        body_text = BeautifulSoup(body_html, "html.parser").get_text("\n", strip=True) if body_html else ""

        notice = {
            "notice_id": nid,
            "subject": d.get("Subject", ""),
            "sent_by": d.get("SentByName", ""),
            "sent_date": d.get("SentDate", ""),
            "body": body_text,
            "is_priority": d.get("IsPriority", "False"),
        }
        notices.append(notice)
        log(f"  [{i}/{len(notice_ids)}] {notice['sent_date']!r}  {notice['subject']!r}  from {notice['sent_by']!r}")

    store.save_notices(notices)
    return len(notices)
