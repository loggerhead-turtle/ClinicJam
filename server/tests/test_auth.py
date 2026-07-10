"""Two-tier PIN model: guest PIN gates jam actions; admin PIN gates settings."""

import os
import tempfile

# Config must be pinned before clinicjam.main is imported.
_tmp = tempfile.mkdtemp(prefix="clinicjam-test-")
_config = os.path.join(_tmp, "config.toml")
with open(_config, "w") as f:
    f.write(f'''
[server]
port = 8080
admin_pin = "9999"
guest_pin = "1234"
[paths]
state_file = "{_tmp}/state.json"
[audio]
fifo_path = "{_tmp}/audio.fifo"
''')
os.environ["CLINICJAM_CONFIG"] = _config

from fastapi.testclient import TestClient  # noqa: E402

from clinicjam.main import app  # noqa: E402

# No `with` block: lifespan (streamer/cast/player) intentionally not started —
# these tests exercise only the HTTP auth layer.
HEADERS = {"X-Device-Id": "testdev", "X-Device-Name": "Tester"}


def client() -> TestClient:
    return TestClient(app)


def test_state_is_public():
    assert client().get("/api/state", headers=HEADERS).status_code == 200


def test_session_reports_pin_required():
    body = client().get("/api/session").json()
    assert body == {"guest_pin_required": True, "guest": False, "admin": False}


def test_guest_actions_locked_without_pin():
    c = client()
    assert c.get("/api/search?q=hello", headers=HEADERS).status_code == 401
    assert c.post("/api/queue", headers=HEADERS,
                  json={"uri": "spotify:track:x"}).status_code == 401
    assert c.post("/api/skip-vote", headers=HEADERS).status_code == 401
    assert c.post("/api/queue/xyz/vote", headers=HEADERS,
                  json={"up": True}).status_code == 401


def test_wrong_guest_pin_rejected():
    assert client().post("/api/login", json={"pin": "0000"}).status_code == 401


def test_guest_pin_unlocks_jam_but_not_settings():
    c = client()
    assert c.post("/api/login", json={"pin": "1234"}).status_code == 200
    session = c.get("/api/session").json()
    assert session["guest"] is True and session["admin"] is False
    # jam actions pass auth now (search 503s because Spotify isn't linked)
    assert c.get("/api/search?q=hello", headers=HEADERS).status_code == 503
    # settings stay locked
    assert c.put("/api/admin/settings", json={"queue_mode": "fifo"}).status_code == 401
    assert c.get("/api/admin/status").status_code == 401
    assert c.post("/api/admin/jam/start").status_code == 401


def test_admin_pin_accepted_at_guest_login():
    c = client()
    assert c.post("/api/login", json={"pin": "9999"}).status_code == 200
    assert c.get("/api/session").json()["guest"] is True


def test_admin_login_grants_guest_too():
    c = client()
    assert c.post("/api/admin/login", json={"pin": "9999"}).status_code == 200
    session = c.get("/api/session").json()
    assert session["admin"] is True and session["guest"] is True
    assert c.get("/api/search?q=hello", headers=HEADERS).status_code == 503
    assert c.put("/api/admin/settings", json={"queue_mode": "fifo"}).status_code == 200


def test_guest_pin_not_valid_for_admin():
    c = client()
    assert c.post("/api/admin/login", json={"pin": "1234"}).status_code == 401
