"""Guards on things that only break at deploy time.

Each of these has a slow, confusing failure mode in production and a fast,
obvious one here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pinned(path: Path) -> list[str]:
    """Requirement lines, ignoring comments and blanks."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_root_and_backend_requirements_agree() -> None:
    """Vercel installs from the root list; Docker installs from the backend one.

    They are separate files because Vercel's parser rejects `-r` includes, so
    nothing but this test stops them drifting into different dependency sets on
    the two deployment paths.
    """
    root = _pinned(ROOT / "requirements.txt")
    backend = _pinned(ROOT / "backend" / "requirements.txt")

    assert root == backend, (
        "requirements.txt and backend/requirements.txt have diverged. "
        "Update both, or the Vercel deployment installs different versions "
        "than the container."
    )


def test_every_requirement_is_pinned() -> None:
    """An unpinned dependency makes a deployment unreproducible."""
    for path in (ROOT / "requirements.txt", ROOT / "backend" / "requirements.txt"):
        for line in _pinned(path):
            assert "==" in line, f"{path.name}: {line!r} is not pinned to a version"


def test_vercel_routes_everything_to_the_app() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

    entrypoint = config["builds"][0]["src"]
    assert (ROOT / entrypoint).is_file(), f"vercel.json builds {entrypoint}, which is missing"

    # The app is a single ASGI entrypoint; anything not routed to it 404s in a
    # way that looks like a code bug rather than a config one.
    assert config["routes"] == [{"src": "/(.*)", "dest": entrypoint}]

    # `functions` and `builds` together are rejected at deploy time, which is a
    # slow way to find out.
    assert "functions" not in config


def test_vercel_allows_a_full_agent_step() -> None:
    """A step waits on the model, so the timeout has to clear a slow response."""
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["builds"][0]["config"]["maxDuration"] >= 30


def test_the_entrypoint_exposes_an_asgi_app() -> None:
    """Vercel imports `app` from the entrypoint; a rename would 500 on deploy."""
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r"^app = FastAPI\(", source, re.MULTILINE)


def test_the_manifest_declares_icons_that_exist() -> None:
    """A missing icon makes Chrome refuse to load the unpacked extension."""
    manifest_path = ROOT / "extension" / "public" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for size, relative in manifest["icons"].items():
        icon = manifest_path.parent / relative
        assert icon.is_file(), f"manifest declares a {size}px icon at {relative}, which is missing"
