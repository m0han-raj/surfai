"""ASGI entrypoint for serverless hosts.

Vercel imports the target file directly from the deployment root, so `backend/`
is never the working directory and the absolute `app.*` imports fail with
`ModuleNotFoundError: No module named 'app'`. Running locally sets the working
directory to `backend/`, which is why nothing else needs this.

Putting the path fix in its own file keeps it out of `app/main.py`, where it
would read as an unexplained hack and would run in every context including the
test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The directory holding the `app` package.
_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.main import app  # noqa: E402

__all__ = ["app"]
