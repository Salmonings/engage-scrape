"""Map your portal so you can locate timetable / homework / lesson data.

Fixes vs the first version:
  - NEVER visits logout/signout links (that's what was killing the session).
  - Crawls TWO levels deep, so it reaches pages behind the main menu.
  - Uses the auto-relogin session, so a timeout mid-crawl just re-authenticates.
  - Also probes a list of likely Engage pupil-portal page names.

Writes discovery/: report.md, pages/*.html, json/*.json
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import client, config

KEYWORDS = ("timetable", "homework", "lesson", "calendar", "diary", "assignment",
            "prep", "schedule", "vle", "planner", "report")

# Links we must never follow (they log us out or leave the app).
BLOCK = ("logout", "signout", "sign-out", "action=logout", "mailto:", "tel:")

# Educated guesses to probe even if not linked from the menu.
CANDIDATES = [
    "default.aspx",
    "VLE/PupilTimetable.aspx", "VLE/Timetable.aspx", "Timetable.aspx",
    "VLE/PupilHomework.aspx", "VLE/Homework.aspx", "Homework.aspx",
    "VLE/PupilDiary.aspx", "VLE/Diary.aspx",
    "VLE/PupilPlanner.aspx", "VLE/Planner.aspx",
    "VLE/PupilMailbox.aspx", "VLE/Default.aspx", "vle",
    "PupilDetails.aspx", "Calendar.aspx", "VLE/Calendar.aspx",
]


def _safe_name(url: str) -> str:
    path = urlparse(url).path or "index"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.strip("/")) or "index"
    return name[:80] + ".html"


def _blocked(url: str) -> bool:
    u = url.lower()
    return any(b in u for b in BLOCK)


def _links_from(html: str, base: str, domain: str) -> set[str]:
    out = set()
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "#")):
            continue
        absu = urljoin(base, href).split("#")[0]
        if urlparse(absu).netloc == domain and not _blocked(absu):
            out.add(absu)
    return out


def run() -> None:
    config.DISCOVERY_DIR.mkdir(exist_ok=True)
    (config.DISCOVERY_DIR / "pages").mkdir(exist_ok=True)
    (config.DISCOVERY_DIR / "json").mkdir(exist_ok=True)

    visited: dict[str, dict] = {}

    with client.session_page(capture_json=True) as page:
        start = page.url  # already authenticated by session_page()
        domain = urlparse(start).netloc

        # Build the frontier: menu links (2 levels) + candidate probes.
        level1 = _links_from(page.content(), start, domain)
        frontier = [start] + sorted(level1) + [
            f"{config.BASE_URL}/{c}" for c in CANDIDATES
        ]
        seen: set[str] = set()
        queue = list(dict.fromkeys(frontier))  # dedupe, keep order
        depth = {u: (0 if u == start else 1) for u in queue}

        while queue:
            url = queue.pop(0)
            if url in seen or _blocked(url):
                continue
            seen.add(url)
            try:
                client.goto_authenticated(page, url)
            except SystemExit:
                raise
            except Exception as e:
                visited[url] = {"error": str(e)}
                continue

            html = page.content()
            (config.DISCOVERY_DIR / "pages" / _safe_name(url)).write_text(html, encoding="utf-8")
            s = BeautifulSoup(html, "lxml")
            text = s.get_text(" ", strip=True).lower()
            is_login = "txtpun" in html.lower()  # page is the login screen
            visited[url] = {
                "title": (s.title.string.strip() if s.title and s.title.string else ""),
                "login_wall": is_login,
                "matches": sorted({k for k in KEYWORDS if k in text or k in url.lower()}),
            }

            # Expand one more level from real (non-login) menu pages.
            if depth.get(url, 1) < 2 and not is_login:
                for nxt in _links_from(html, url, domain):
                    if nxt not in seen and nxt not in queue:
                        depth[nxt] = depth.get(url, 1) + 1
                        queue.append(nxt)

        json_hits = list(getattr(page, "_captured_json", []))

    for i, hit in enumerate(json_hits):
        (config.DISCOVERY_DIR / "json" / f"{i:03d}.json").write_text(
            json.dumps(hit, ensure_ascii=False, indent=2), encoding="utf-8")

    _report(visited, json_hits)
    print(f"Discovery written to {config.DISCOVERY_DIR}/  — read report.md")


def _report(visited: dict, json_hits: list[dict]) -> None:
    L = ["# Engage portal discovery\n"]

    hot = {u: d for u, d in visited.items() if d.get("matches") and not d.get("login_wall")}
    L.append("## Pages likely holding your data (authenticated)\n")
    if hot:
        for u, d in hot.items():
            L.append(f"- **{', '.join(d['matches'])}** — [{d.get('title') or u}]({u})")
    else:
        L.append("_None matched keywords — scan discovery/pages/ manually._")
    L.append("")

    svc = sorted({h["url"] for h in json_hits})
    L.append("## JSON / .asmx service endpoints captured\n")
    if svc:
        for u in svc:
            L.append(f"- `{u}`")
        L.append("\n_These return JSON — prefer them over HTML scraping. "
                 "Look for timetable/homework services here._")
    else:
        L.append("_None captured this run._")
    L.append("")

    walls = [u for u, d in visited.items() if d.get("login_wall")]
    if walls:
        L.append(f"## Pages that still hit the login wall ({len(walls)})\n")
        L += [f"- {u}" for u in walls]
        L.append("")

    L.append("## Every page visited\n")
    for u, d in visited.items():
        if "error" in d:
            L.append(f"- {u} — ERROR {d['error']}")
        else:
            tag = " [LOGIN WALL]" if d.get("login_wall") else ""
            L.append(f"- [{d.get('title') or u}]({u}){tag}")

    (config.DISCOVERY_DIR / "report.md").write_text("\n".join(L), encoding="utf-8")
