"""
How the app starts in single-user local mode: it must import and serve
/health with no Google OAuth project at all (no Google client id, secret or
redirect URI), and it must answer only requests addressed to this machine.
conftest.py sets the Google variables for the whole suite and
talleyrand.core.config builds its Settings singleton at import time, so local
mode is exercised in a fresh subprocess rather than by monkeypatching the
already-imported settings object.
"""

import subprocess
import sys

from fastapi.testclient import TestClient

from talleyrand.main import app

_SCRIPT = """
import os
os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-never-use-this-anywhere-real"
for name in ("GOOGLE_REDIRECT_URI", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ.pop(name, None)
os.environ["LOCAL_USER_EMAIL"] = "you@example.com"

from fastapi.testclient import TestClient
from talleyrand.main import app

client = TestClient(app, base_url="http://localhost:8000")
response = client.get("/health")
assert response.status_code == 200, response.text
assert response.json() == {"status": "healthy"}
print("OK")
"""


def test_app_imports_and_serves_health_in_local_mode_without_google():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


_HOST_CHECK_SCRIPT = """
import os
os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-never-use-this-anywhere-real"
os.environ["LOCAL_USER_EMAIL"] = "you@example.com"

from fastapi.testclient import TestClient
from talleyrand.main import app

client = TestClient(app)
for host in ("localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000", "rebind.example:8000"):
    print(host, client.get("/health", headers={"Host": host}).status_code)
"""


def test_local_mode_answers_only_requests_addressed_to_this_machine():
    # A page on any other name that re-resolves to 127.0.0.1 (DNS rebinding)
    # counts as same-origin with that name, so it could sign in locally and
    # read every case. Its requests carry the foreign name in Host.
    result = subprocess.run(
        [sys.executable, "-c", _HOST_CHECK_SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    statuses = dict(line.split() for line in result.stdout.splitlines())
    assert statuses == {
        "localhost": "200",
        "localhost:8000": "200",
        "127.0.0.1": "200",
        "127.0.0.1:8000": "200",
        "rebind.example:8000": "400",
    }


def test_foreign_host_is_served_when_local_mode_is_off():
    response = TestClient(app).get("/health", headers={"Host": "talleyrand.example.org"})
    assert response.status_code == 200
