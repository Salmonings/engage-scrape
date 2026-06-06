"""FastAPI server — all data fetched live from the Engage portal.

Auth flow
---------
  1. POST /auth/login  with your Engage username + password
  2. You get back a Bearer token (valid 20 hours)
  3. Pass it as  Authorization: Bearer <token>  on every request

The server stores NO Engage credentials. Each caller authenticates with
their own school account and gets an isolated session.

Run
---
  python main.py serve
  uvicorn engage.api:app --reload
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import client
from .live import fetch_homework, fetch_timetable
from .notices_scraper import fetch_notices
from .reports_scraper import fetch_report_detail, fetch_reports_meta

# ---------------------------------------------------------------------------
# In-memory token store
# ---------------------------------------------------------------------------

_sessions: dict[str, dict] = {}   # token -> {"state": dict, "expires_at": datetime}
_lock = Lock()
_TOKEN_TTL_HOURS = 20


def _store_token(state: dict) -> str:
    token = secrets.token_hex(32)
    with _lock:
        _sessions[token] = {
            "state": state,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=_TOKEN_TTL_HOURS),
        }
    return token


def _lookup_state(token: str) -> dict | None:
    with _lock:
        # Prune expired tokens while we're in here
        expired = [t for t, v in _sessions.items() if v["expires_at"] < datetime.now(timezone.utc)]
        for t in expired:
            del _sessions[t]
        entry = _sessions.get(token)
        return entry["state"] if entry else None


# ---------------------------------------------------------------------------
# Bearer auth dependency
# ---------------------------------------------------------------------------

_bearer = HTTPBearer()


def _require_session(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    """Resolve a Bearer token to a session state dict, or raise 401."""
    state = _lookup_state(credentials.credentials)
    if state is None:
        raise HTTPException(401, "Invalid or expired token — call POST /auth/login")
    return state


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Engage Portal API",
    description=(
        "Live school data fetched directly from the Engage MIS portal. "
        "Call POST /auth/login with your Engage credentials to get a Bearer token."
    ),
    version="2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAY_ORDER = {d: i for i, d in enumerate(
    ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
)}


def _day_key(r: dict) -> tuple:
    return (_DAY_ORDER.get(r.get("day", ""), 99), r.get("start", ""))


def _parse_notice_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%d %B %Y %H:%M:%S").isoformat()
    except Exception:
        return s or ""


def _handle_expired(e: Exception) -> None:
    if "expired" in str(e).lower():
        raise HTTPException(401, "Session expired — call POST /auth/login again")
    raise HTTPException(502, f"Portal error: {e}")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str
    security_code: str = ""


@app.post("/auth/login", summary="Log in with Engage credentials to get a Bearer token", tags=["auth"])
def login(body: LoginRequest):
    """
    Exchange your Engage username + password for a Bearer token.
    The token is valid for 20 hours. Pass it as `Authorization: Bearer <token>` on all requests.
    """
    try:
        state = client.login_with_credentials(body.username, body.password, body.security_code)
    except ValueError as e:
        raise HTTPException(401, str(e))
    token = _store_token(state)
    return {
        "token": token,
        "token_type": "Bearer",
        "expires_in": _TOKEN_TTL_HOURS * 3600,
    }


# ---------------------------------------------------------------------------
# Timetable  (Playwright, ~5-10s)
# ---------------------------------------------------------------------------

@app.get("/timetable", summary="This week's full timetable", tags=["timetable"])
def timetable(state: dict = Depends(_require_session)):
    try:
        rows = fetch_timetable(state, weeks=1)
    except RuntimeError as e:
        _handle_expired(e)
    return sorted(rows, key=lambda r: (r.get("week_start", ""), *_day_key(r)))


@app.get("/timetable/today", summary="Today's lessons", tags=["timetable"])
def timetable_today(state: dict = Depends(_require_session)):
    try:
        rows = fetch_timetable(state, weeks=1)
    except RuntimeError as e:
        _handle_expired(e)
    day_name = date.today().strftime("%A")
    return sorted(
        [r for r in rows if r.get("day") == day_name],
        key=lambda r: r.get("start", ""),
    )


@app.get("/timetable/week", summary="This week's timetable (alias for /timetable)", tags=["timetable"])
def timetable_week(state: dict = Depends(_require_session)):
    try:
        rows = fetch_timetable(state, weeks=1)
    except RuntimeError as e:
        _handle_expired(e)
    return sorted(rows, key=_day_key)


# ---------------------------------------------------------------------------
# Homework  (Playwright, ~10-20s)
# ---------------------------------------------------------------------------

@app.get("/homework", summary="All currently listed homework", tags=["homework"])
def homework(state: dict = Depends(_require_session)):
    try:
        rows = fetch_homework(state)
    except RuntimeError as e:
        _handle_expired(e)
    return sorted(rows, key=lambda r: r.get("due_date") or r.get("date") or "")


@app.get("/homework/upcoming", summary="Homework due within N days", tags=["homework"])
def homework_upcoming(days: int = 7, state: dict = Depends(_require_session)):
    try:
        rows = fetch_homework(state)
    except RuntimeError as e:
        _handle_expired(e)
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return sorted(
        [r for r in rows if r.get("due_date") and today <= r["due_date"] <= cutoff],
        key=lambda r: r["due_date"],
    )


@app.get("/homework/overdue", summary="Homework past its due date", tags=["homework"])
def homework_overdue(state: dict = Depends(_require_session)):
    try:
        rows = fetch_homework(state)
    except RuntimeError as e:
        _handle_expired(e)
    today = date.today().isoformat()
    return sorted(
        [r for r in rows if r.get("due_date") and r["due_date"] < today],
        key=lambda r: r["due_date"],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Notices  (requests, ~2-5s)
# ---------------------------------------------------------------------------

@app.get("/notices", summary="Recent notices from the mailbox", tags=["notices"])
def notices(limit: int = 10, state: dict = Depends(_require_session)):
    rows = fetch_notices(limit=limit, state=state)
    return sorted(rows, key=lambda r: _parse_notice_date(r.get("sent_date", "")), reverse=True)


# ---------------------------------------------------------------------------
# Assessment reports  (requests, ~3-15s)
# ---------------------------------------------------------------------------

@app.get("/reports", summary="Assessment report periods", tags=["reports"])
def reports_list(state: dict = Depends(_require_session)):
    return fetch_reports_meta(state=state)


@app.get("/reports/{period_id}", summary="Full report HTML for one period", tags=["reports"])
def report_detail(period_id: str, state: dict = Depends(_require_session)):
    row = fetch_report_detail(period_id, state=state)
    if not row:
        raise HTTPException(404, "Period not found or no data available")
    return row


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/ping", summary="Health check — no auth required", tags=["meta"])
def ping():
    return {"status": "ok"}
