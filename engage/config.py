"""Central configuration, loaded from .env / environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

BASE_URL: str = os.getenv("ENGAGE_BASE_URL", "https://elgounaschool.engagehosted.com").rstrip("/")
USERNAME: str = os.getenv("ENGAGE_USERNAME", "")
PASSWORD: str = os.getenv("ENGAGE_PASSWORD", "")
SECURITY_CODE: str = os.getenv("ENGAGE_SECURITY_CODE", "")
HEADED: bool = os.getenv("ENGAGE_HEADED", "1") == "1"

LOGIN_URL = f"{BASE_URL}/Login.aspx"

# Artefacts
SESSION_FILE = ROOT / "session_state.json"
DB_FILE = ROOT / "engage.db"
DISCOVERY_DIR = ROOT / "discovery"
EXPORT_FILE = ROOT / "export.json"
