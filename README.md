# Engage Portal Scraper

Pulls **timetable**, **homework** (full history), **notices**, and **assessment reports**
out of an Engage MIS pupil portal (`*.engagehosted.com`) and stores everything in
SQLite + JSON so it can feed chatbots, AI helpers, calendars, etc.

The portal is **ASP.NET Web Forms** (`Login.aspx`, viewstate, postbacks + Telerik
controls). Strategy: log in once with a real browser, persist the session, then
scrape via Playwright and direct ASMX service calls.

> **Legal:** this logs in *as you* and reads *your own* data — fine for personal use.
> Check your school's acceptable-use policy. The cleaner alternative is asking your
> school's Engage admin to enable the **RESTful API** or **Wonde** access.

## Install

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file:

```
ENGAGE_BASE_URL=https://yourschool.engagehosted.com
ENGAGE_USERNAME=your.username
ENGAGE_PASSWORD=yourpassword
ENGAGE_SECURITY_CODE=       # leave blank if your portal has no MFA field
ENGAGE_HEADED=1             # set to 0 for headless (useful after first login)
```

## Commands

```bash
python main.py login              # log in, save session to session_state.json
python main.py probe              # verify the session is alive

python main.py scrape             # timetable + current lessons/homework (this week)
python main.py scrape 2           # same but two weeks (covers A/B rotations)

python main.py scrape_hw          # full homework history across all academic years
python main.py scrape_hw current  # current year only (2025/2026)
python main.py scrape_hw 2024     # specific year

python main.py scrape_notices     # all notices from the pupil mailbox
python main.py scrape_reports     # all assessment report periods -> DB + reports/*.html

python main.py export             # dump everything to export.json
python main.py ics                # write timetable.ics (import into Google/Apple Calendar)
python main.py show               # print DB contents to terminal
```

## Typical first-run

```bash
python main.py login          # headed browser so you can handle the security code
python main.py scrape_hw      # grab all homework history
python main.py scrape_notices # grab all notices
python main.py scrape_reports # grab all assessment reports
python main.py export         # write export.json
```

After that, just `python main.py scrape` for a weekly refresh.

## What ends up where

| File / folder | Contents |
|---|---|
| `engage.db` | SQLite — tables: `timetable`, `lessons`, `homework`, `notices`, `reports` |
| `export.json` | Full DB dump as JSON |
| `timetable.ics` | Calendar file for all timetable entries |
| `reports/*.html` | One self-contained HTML file per assessment reporting period |

## Project structure

```
engage/
  client.py          # Playwright session management + requests_session()
  config.py          # .env loader
  parsers.py         # HTML → dataclasses for timetable/lessons/homework
  store.py           # SQLite schema + upsert helpers
  hw_scraper.py      # Full homework scraper (all years, all tiles)
  notices_scraper.py # Mailbox notices via PupilMailService ASMX
  reports_scraper.py # Assessment reports via PupilAssessmentServices ASMX
  tools.py           # Clean getter functions for use as LLM tools
  ics_export.py      # iCalendar export
  discover.py        # Portal crawler / discovery helper
main.py              # CLI entry point
```

## Using as LLM tools

`engage/tools.py` exposes `get_timetable()`, `get_homework()`, and `get_lessons()`
as plain Python functions that read from the local DB — register them directly as
tools with any LLM framework.

## Why Playwright and not requests

The login form uses `__VIEWSTATE`/`__EVENTVALIDATION` round-tripping with
portal-specific field names. A real browser handles all of that plus any JS
initialisation. Once logged in, the saved cookies are reused with plain `requests`
for all ASMX service calls — see `client.requests_session()`.
