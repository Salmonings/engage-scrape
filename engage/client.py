"""Browser session for the Engage pupil portal (ASP.NET Web Forms).

session_page() logs in fresh on entry and saves session_state.json on exit.
goto_authenticated() re-logs transparently if a navigation is bounced to
Login.aspx mid-command, so server-side session timeouts stop mattering.
Credentials come from .env (ENGAGE_USERNAME / ENGAGE_PASSWORD).
"""
from __future__ import annotations

import json
import time as _time
from contextlib import contextmanager
from typing import Iterator

import requests
from playwright.sync_api import Page, sync_playwright

from . import config

# Real control IDs for this portal (discovered from the login page).
SEL_USER = "#ctl00_PageContent_loginControl_txtUN"
SEL_PASS = "#ctl00_PageContent_loginControl_txtPwd"
SEL_MFA = "#ctl00_PageContent_loginControl_txtMFA"
SEL_REMEMBER = "#ctl00_PageContent_loginControl_cbRememberMe"
SEL_SUBMIT = "#ctl00_PageContent_loginControl_btnLogin"


def _on_login_page(page: Page) -> bool:
    return "login.aspx" in page.url.lower()


def _do_login(page: Page) -> None:
    """Fill + submit the login form. Assumes we're on (or get redirected to) Login.aspx."""
    # Wait for any post-load JS to settle before checking URL.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass

    if not _on_login_page(page):
        # Not on login page — already logged in or redirected. We're done.
        return

    page.fill(SEL_USER, config.USERNAME)
    page.fill(SEL_PASS, config.PASSWORD)
    if config.SECURITY_CODE:
        try:
            page.fill(SEL_MFA, config.SECURITY_CODE)
        except Exception:
            pass
    # Persist the login as long as the portal allows.
    try:
        page.check(SEL_REMEMBER)
    except Exception:
        pass

    page.click(SEL_SUBMIT)
    try:
        page.wait_for_url(lambda u: "login.aspx" not in u.lower(), timeout=30_000)
    except Exception:
        pass

    if _on_login_page(page):
        raise SystemExit(
            "Login failed — check ENGAGE_USERNAME / ENGAGE_PASSWORD in .env. "
            "If you can log in by hand but not here, run with ENGAGE_HEADED=1 to watch."
        )


def login_with_credentials(username: str, password: str, security_code: str = "") -> dict:
    """Log in to the Engage portal with explicit credentials.

    Returns the Playwright storage state as a dict (cookies etc.).
    Raises ValueError if login fails.
    Does NOT touch session_state.json.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.LOGIN_URL, wait_until="load")
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        page.fill(SEL_USER, username)
        page.fill(SEL_PASS, password)
        if security_code:
            try:
                page.fill(SEL_MFA, security_code)
            except Exception:
                pass
        try:
            page.check(SEL_REMEMBER)
        except Exception:
            pass
        page.click(SEL_SUBMIT)
        try:
            page.wait_for_url(lambda u: "login.aspx" not in u.lower(), timeout=30_000)
        except Exception:
            pass
        if _on_login_page(page):
            browser.close()
            raise ValueError("Login failed — check Engage username and password")
        state = context.storage_state()
        browser.close()
        return state


@contextmanager
def session_page_from_state(state: dict) -> Iterator[Page]:
    """Open a Playwright page using a pre-captured session state dict.

    Raises RuntimeError if the session has expired (redirected to login.aspx).
    Callers must handle this and return a 401 to the API client.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not config.HEADED)
        context = browser.new_context(storage_state=state)
        page = context.new_page()
        page.goto(config.BASE_URL + "/vle/default.aspx", wait_until="networkidle")
        if _on_login_page(page):
            browser.close()
            raise RuntimeError("Session expired — call /auth/login again")
        try:
            yield page
        finally:
            browser.close()


def requests_session_from_state(state: dict) -> requests.Session:
    """Build a requests.Session from a Playwright storage state dict."""
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    for c in state.get("cookies", []):
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s


def login_and_save() -> None:
    if not config.USERNAME or not config.PASSWORD:
        raise SystemExit("Set ENGAGE_USERNAME and ENGAGE_PASSWORD in .env")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not config.HEADED)
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.LOGIN_URL, wait_until="load")
        _do_login(page)
        context.storage_state(path=str(config.SESSION_FILE))
        print(f"Logged in -> {page.url}\nSession saved -> {config.SESSION_FILE}")
        browser.close()


def _session_is_fresh(max_age_hours: float = 20) -> bool:
    """True if session_state.json exists and was written within max_age_hours."""
    if not config.SESSION_FILE.exists():
        return False
    return (_time.time() - config.SESSION_FILE.stat().st_mtime) / 3600 < max_age_hours


def goto_authenticated(page: Page, url: str) -> None:
    """Navigate to url; if bounced to login, re-authenticate and retry once."""
    page.goto(url, wait_until="networkidle")
    if _on_login_page(page):
        _do_login(page)
        page.goto(url, wait_until="networkidle")


@contextmanager
def session_page(capture_json: bool = False) -> Iterator[Page]:
    """Reuse a recent session if available; re-login if missing or >20 h old.
    goto_authenticated() handles mid-command re-auth transparently."""
    if not _session_is_fresh():
        login_and_save()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not config.HEADED)
        context = browser.new_context(storage_state=str(config.SESSION_FILE))
        page = context.new_page()
        captured: list[dict] = []
        page._captured_json = captured  # type: ignore[attr-defined]

        if capture_json:
            def _on_response(resp):
                ctype = (resp.headers or {}).get("content-type", "")
                if "json" in ctype.lower():
                    try:
                        captured.append({"url": resp.url, "body": resp.text()})
                    except Exception:
                        captured.append({"url": resp.url, "body": "<unreadable>"})
            page.on("response", _on_response)

        # Verify session is live; re-login transparently if it expired mid-age-window.
        goto_authenticated(page, config.BASE_URL + "/vle/default.aspx")
        try:
            yield page
        finally:
            try:
                context.storage_state(path=str(config.SESSION_FILE))
            except Exception:
                pass
            browser.close()


def fetch_html(path_or_url: str) -> str:
    url = path_or_url if path_or_url.startswith("http") else f"{config.BASE_URL}/{path_or_url.lstrip('/')}"
    with session_page() as page:
        goto_authenticated(page, url)
        return page.content()


def call_service(asmx_method_url: str, payload: dict | None = None) -> dict:
    """POST to an Engage .asmx web method and return parsed JSON.

    Engage services take JSON and return {"d": ...}. Example:
        call_service("/Services/PupilMailService.asmx/GetUnreadCount")
    """
    url = asmx_method_url if asmx_method_url.startswith("http") else f"{config.BASE_URL}/{asmx_method_url.lstrip('/')}"
    with session_page() as page:
        result = page.evaluate(
            """async ([u, body]) => {
                const r = await fetch(u, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json; charset=utf-8'},
                    body: body,
                    credentials: 'include'
                });
                const text = await r.text();
                return {status: r.status, text};
            }""",
            [url, json.dumps(payload or {})],
        )
    try:
        return json.loads(result["text"])
    except Exception:
        return {"_status": result["status"], "_raw": result["text"]}


def requests_session() -> requests.Session:
    if not config.SESSION_FILE.exists():
        login_and_save()
    state = json.loads(config.SESSION_FILE.read_text())
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    for c in state.get("cookies", []):
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s


# --- Weekly timetable navigation -------------------------------------------
TT_TABLE_SEL = "#tblTimeTable_ctl00_PageContent_weeklyTimetable"
TT_NEXT_SEL = "#ctl00_PageContent_weeklyTimetable_wsTimetableDate_btnNextWeek"
TT_LABEL_SEL = "#ctl00_PageContent_weeklyTimetable_wsTimetableDate_lblWeek"


def iterate_timetable_weeks(page, base_path: str, weeks: int = 1):
    """Yield (week_label, html) for `weeks` consecutive weeks, starting at the
    default (current) week, clicking Next Week between each and waiting for the
    async postback to refresh."""
    goto_authenticated(page, f"{config.BASE_URL}/{base_path}")
    for i in range(weeks):
        page.wait_for_selector(TT_TABLE_SEL, timeout=15_000)
        label = ""
        try:
            label = page.locator(TT_LABEL_SEL).inner_text().strip()
        except Exception:
            pass
        yield label, page.content()
        if i < weeks - 1:
            try:
                page.click(TT_NEXT_SEL)
                page.wait_for_function(
                    "o => { const e=document.querySelector(o.sel);"
                    " return e && e.innerText.trim() && e.innerText.trim() !== o.prev; }",
                    arg={"sel": TT_LABEL_SEL, "prev": label}, timeout=15_000)
            except Exception:
                page.wait_for_timeout(2500)
