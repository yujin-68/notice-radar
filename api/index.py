from __future__ import annotations

import sys
from pathlib import Path


# --- Vercel deployment glue ---
# Vercel loads Python functions from api/*.py and expects a top-level `app`
# callable. This keeps the web app logic in web_app.py unchanged.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_app import app as web_app

app = web_app
