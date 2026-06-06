# Engage Portal API Reference

All data is fetched **live from the portal** on every request — no database, always fresh.

Interactive docs (Swagger UI) at `http://localhost:8000/docs` when the server is running.

---

## Running the server

```bash
python main.py serve
# or with hot-reload during development:
uvicorn engage.api:app --reload
```

---

## Authentication

The server stores **no Engage credentials**. You log in with your own school account and get back a short-lived Bearer token.

### Step 1 — Get a token

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your.name", "password": "yourpassword"}'
```

```json
{
  "token": "a3f8c2...",
  "token_type": "Bearer",
  "expires_in": 72000
}
```

Token is valid for **20 hours**. Store it and reuse it.

### Step 2 — Pass the token on every request

```
Authorization: Bearer a3f8c2...
```

All endpoints (including timetable and homework) require this header. A missing or expired token returns `401`.

### Token expiry

When a token expires the API returns:
```json
{"detail": "Session expired — call POST /auth/login again"}
```

Just call `/auth/login` again to get a fresh token. The Discord bot handles this automatically — each Discord user has their own token, refreshed transparently when it expires.

---

## Endpoints

### Health check

#### `GET /ping`
No auth required. Use this to verify the server is up.

```bash
curl http://localhost:8000/ping
```
```json
{"status": "ok"}
```

---

### Auth

#### `POST /auth/login`
Exchange Engage credentials for a Bearer token.

**Body:**
| Field | Type | Required | Description |
|---|---|---|---|
| `username` | string | Yes | Engage portal username |
| `password` | string | Yes | Engage portal password |
| `security_code` | string | No | MFA/security code — omit or pass `""` if not used |

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "john.doe", "password": "secret"}'
```

```json
{
  "token": "a3f8c2d1e9b7...",
  "token_type": "Bearer",
  "expires_in": 72000
}
```

**Errors:**
- `401` — wrong username or password

---

### Timetable

> Playwright-based. Expect ~5–10s response time.

All timetable endpoints sort days Sunday → Saturday, then by start time.

#### `GET /timetable`
Full timetable for the current week.

```bash
curl http://localhost:8000/timetable \
  -H "Authorization: Bearer <token>"
```

```json
[
  {
    "week_start": "2026-05-31",
    "day": "Monday",
    "period": "1",
    "start": "08:00",
    "end": "08:55",
    "subject": "Mathematics",
    "room": "Math2",
    "teacher": "Ms Salma Yehia"
  }
]
```

#### `GET /timetable/today`
Only today's lessons, sorted by start time.

```bash
curl http://localhost:8000/timetable/today \
  -H "Authorization: Bearer <token>"
```

#### `GET /timetable/week`
Alias for `/timetable`.

---

### Homework

> Playwright-based. Fetches what's currently visible in the portal's tiles (current, upcoming, overdue, handed-in). Expect ~10–20s response time.

#### `GET /homework`
All homework currently listed in the portal, sorted by due date.

```bash
curl http://localhost:8000/homework \
  -H "Authorization: Bearer <token>"
```

```json
[
  {
    "lesson_plan_id": "12345",
    "subject": "Mathematics",
    "date": "2026-05-28",
    "due_date": "2026-06-10",
    "topic": "Paper 2 Calculator practice",
    "details": "Complete questions 1-15 from the worksheet.",
    "attachments": ""
  }
]
```

#### `GET /homework/upcoming?days=7`
Homework due within the next N days (default: 7).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | integer | `7` | How many days ahead to look |

```bash
curl "http://localhost:8000/homework/upcoming?days=14" \
  -H "Authorization: Bearer <token>"
```

#### `GET /homework/overdue`
Homework past its due date, most overdue first.

```bash
curl http://localhost:8000/homework/overdue \
  -H "Authorization: Bearer <token>"
```

---

### Notices

> HTTP-based (fast). Returns personal teacher messages — use **ephemeral** responses in Discord. Expect ~2–5s.

#### `GET /notices?limit=10`
Most recent N notices, newest first.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | `10` | How many notices to return |

```bash
curl "http://localhost:8000/notices?limit=5" \
  -H "Authorization: Bearer <token>"
```

```json
[
  {
    "notice_id": "336857",
    "subject": "New Lesson Plan",
    "sent_by": "Ms Salma Yehia",
    "sent_date": "01 June 2026 05:57:56",
    "body": "New Lesson Plan for Mathematics\n\nPaper 2 Calculator practice paper has been created...",
    "is_priority": "0"
  }
]
```

---

### Assessment Reports

> HTTP-based. Returns personal grades and teacher comments — use **ephemeral** responses in Discord.

#### `GET /reports`
List of all reporting periods (no HTML, just metadata). Expect ~3–5s.

```bash
curl http://localhost:8000/reports \
  -H "Authorization: Bearer <token>"
```

```json
[
  {
    "period_id": "181",
    "period_name": "Curriculum overview 2024-2025 (Locked)",
    "academic_year": "2024",
    "is_locked": true
  },
  {
    "period_id": "168",
    "period_name": "Curriculum overview 1st term 2023-2024 (Locked)",
    "academic_year": "2023",
    "is_locked": true
  }
]
```

#### `GET /reports/{period_id}`
Full rendered HTML for one period plus subject count. Expect ~8–15s.

```bash
curl http://localhost:8000/reports/181 \
  -H "Authorization: Bearer <token>"
```

```json
{
  "period_id": "181",
  "period_name": "Curriculum overview 2024-2025 (Locked)",
  "academic_year": "2024",
  "n_subjects": 13,
  "html": "<div class=\"report-section\">...</div>"
}
```

The `html` field is the portal's rendered output. The Discord bot strips tags for display. For a proper formatted view open the saved `reports/*.html` files in a browser.

---

## Privacy reference

All endpoints require a Bearer token tied to a specific Engage account, so each user only ever sees their own data. The Discord bot maps each Discord user to their own token — users link their account once with `/login`. On top of that:

| Endpoint | Discord usage |
|---|---|
| `/timetable*` | Any channel |
| `/homework*` | Any channel |
| `/notices` | **Ephemeral only** — personal teacher messages |
| `/reports*` | **Ephemeral only** — personal grades and comments |

Ephemeral = Discord's "only you can see this" — invisible to everyone else in the channel, dismissed on close.

---

## Response times

| Endpoint group | Method | Typical time |
|---|---|---|
| `/auth/login` | Playwright (browser) | 10–20s |
| `/timetable*` | Playwright (browser) | 5–10s |
| `/homework*` | Playwright (browser) | 10–20s |
| `/notices` | HTTP | 2–5s |
| `/reports` | HTTP | 3–5s |
| `/reports/{id}` | HTTP | 8–15s |

Playwright endpoints launch a headless Chromium browser, so they're inherently slower. For Discord, the bot uses `defer()` before fetching, giving a 15-minute window before Discord times out.

---

## Using from Python (httpx)

```python
import httpx

BASE = "http://localhost:8000"

async def get_token(username: str, password: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/auth/login",
                         json={"username": username, "password": password})
        # security_code can be added to the dict above if your portal requires it
        r.raise_for_status()
        return r.json()["token"]

async def get_timetable(token: str) -> list:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/timetable/today",
                        headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()

async def get_notices(token: str, limit: int = 5) -> list:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/notices", params={"limit": limit},
                        headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()
```

---

## Error responses

| Status | Meaning |
|---|---|
| `401` | Missing token, invalid token, expired token, or wrong Engage credentials |
| `404` | Resource not found (e.g. unknown `period_id`) |
| `502` | Portal unreachable or returned an unexpected error |
