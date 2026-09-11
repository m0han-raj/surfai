"""Guards on things that only break at deploy time.

Each of these has a slow, confusing failure mode in production and a fast,
obvious one here.
"""

from __future__ import annotations

import json
import os
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


def _by_name(lines: list[str]) -> dict[str, str]:
    """Map distribution name to its pinned version, ignoring extras."""
    pins = {}
    for line in lines:
        spec, _, version = line.partition("==")
        pins[spec.split("[")[0].strip().lower()] = version.strip()
    return pins


def test_the_vercel_requirements_are_a_subset_of_the_backend_ones() -> None:
    """Vercel installs from the root list; Docker installs from the backend one.

    The root list is deliberately smaller: Vercel invokes the ASGI app directly
    so no server is needed, and migrations are a separate step so Alembic is
    not needed at request time. Both are megabytes against a hard lambda size
    limit.

    What must never happen is a package appearing in both at different
    versions, or the root list acquiring something the backend does not have.
    """
    root = _by_name(_pinned(ROOT / "requirements.txt"))
    backend = _by_name(_pinned(ROOT / "backend" / "requirements.txt"))

    extra = set(root) - set(backend)
    assert not extra, (
        f"requirements.txt has packages the backend list does not: {sorted(extra)}. "
        "The deployment would install something never tested against."
    )

    mismatched = {
        name: (root[name], backend[name])
        for name in root
        if root[name] != backend[name]
    }
    assert not mismatched, (
        f"Pinned versions have drifted between the two lists: {mismatched}. "
        "The Vercel deployment would run different code than the container."
    )


def test_the_vercel_requirements_cover_what_the_app_imports() -> None:
    """Trimming the deployment list must not remove something loaded at import."""
    root = _by_name(_pinned(ROOT / "requirements.txt"))

    # Imported transitively by app.main; without any of these the lambda 500s
    # on its first request rather than failing at build time.
    for package in ("fastapi", "pydantic", "pydantic-settings", "sqlalchemy", "httpx"):
        assert package in root, f"{package} is required at runtime but not in requirements.txt"

    # Present in the backend list for the container, deliberately absent here.
    for package in ("uvicorn", "alembic"):
        assert package not in root, (
            f"{package} is not needed on Vercel and costs lambda size; "
            "remove it or update this test with the reason it is back"
        )


def test_every_requirement_is_pinned() -> None:
    """An unpinned dependency makes a deployment unreproducible."""
    for path in (ROOT / "requirements.txt", ROOT / "backend" / "requirements.txt"):
        for line in _pinned(path):
            assert "==" in line, f"{path.name}: {line!r} is not pinned to a version"


def _vercel() -> dict:
    return json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


def test_vercel_routes_the_catch_all_to_the_app() -> None:
    config = _vercel()
    entrypoint = config["builds"][0]["src"]
    assert (ROOT / entrypoint).is_file(), f"vercel.json builds {entrypoint}, which is missing"

    # Order matters: the catch-all must come last, or it swallows every other
    # route and the static pages 404 through the Python app.
    assert config["routes"][-1] == {"src": "/(.*)", "dest": entrypoint}

    # `functions` and `builds` together are rejected at deploy time, which is a
    # slow way to find out.
    assert "functions" not in config


def test_the_demo_pages_are_served_statically() -> None:
    """Routing them through the lambda would spend an invocation on a file."""
    config = _vercel()
    assert any(b.get("use") == "@vercel/static" for b in config["builds"])
    assert {"src": "/demo/(.*)", "dest": "/demo/$1"} in config["routes"]
    assert (ROOT / "demo" / "index.html").is_file()


def test_the_landing_page_is_shipped_with_the_lambda() -> None:
    """The Python builder traces imports, not data files.

    Without an explicit includeFiles the page is simply absent at runtime, and
    the only symptom is the fallback JSON that this page exists to replace.
    """
    config = _vercel()
    include = config["builds"][0]["config"].get("includeFiles", "")
    assert "static" in include, "the landing page would not be deployed"

    page = ROOT / "backend" / "app" / "static" / "index.html"
    assert page.is_file()

    html = page.read_text(encoding="utf-8")
    # It is the public face of the project; these are the things it must carry.
    assert "<title>SurfAI</title>" in html
    assert "/health" in html
    assert "github.com/m0han-raj/surfai" in html


def test_vercel_allows_a_full_agent_step() -> None:
    """A step waits on the model, so the timeout has to clear a slow response."""
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["builds"][0]["config"]["maxDuration"] >= 30


def test_the_entrypoint_exposes_an_asgi_app() -> None:
    """Vercel imports `app` from the entrypoint; a rename would 500 on deploy."""
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r"^app = FastAPI\(", source, re.MULTILINE)

    # The shim re-exports it, and exists only to put `backend/` on sys.path.
    shim = (ROOT / "backend" / "vercel_entry.py").read_text(encoding="utf-8")
    assert "from app.main import app" in shim
    assert "sys.path" in shim, (
        "vercel_entry.py exists solely to make the app package importable when "
        "the working directory is not backend/. Without it the lambda raises "
        "ModuleNotFoundError on every request."
    )


def test_the_serverless_entrypoint_imports_without_the_backend_cwd() -> None:
    """Reproduce what Vercel does: load the file by path, from elsewhere.

    No PYTHONPATH and a different working directory, so the only thing that can
    make `from app.main import app` resolve is the sys.path insert inside the
    shim. Without it this raises ModuleNotFoundError, which is exactly how the
    first deployment failed on every request.
    """
    import subprocess
    import sys

    script = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('vercel_entry', r'{path}');"
        "mod = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(mod);"
        "assert mod.app is not None;"
        "print('ok')"
    ).format(path=ROOT / "backend" / "vercel_entry.py")

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "DATABASE_URL": "sqlite:///./_entry_check.db",
        },
        capture_output=True,
        text=True,
    )
    (ROOT / "_entry_check.db").unlink(missing_ok=True)

    assert result.returncode == 0, (
        "the serverless entrypoint could not import itself outside backend/: "
        + result.stderr[-900:]
    )
    assert "ok" in result.stdout


def test_the_manifest_declares_icons_that_exist() -> None:
    """A missing icon makes Chrome refuse to load the unpacked extension."""
    manifest_path = ROOT / "extension" / "public" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for size, relative in manifest["icons"].items():
        icon = manifest_path.parent / relative
        assert icon.is_file(), f"manifest declares a {size}px icon at {relative}, which is missing"
