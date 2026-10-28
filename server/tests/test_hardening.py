"""Security hardening: signed sessions, lockouts, body limits, headers,
bounded growth, art proxy allowlist, id validation."""

import os
import tempfile
import time

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
run_dir = "{_tmp}/run"
[audio]
fifo_path = "{_tmp}/audio.fifo"
''')
os.environ["CLINICJAM_CONFIG"] = _config

from fastapi.testclient import TestClient  # noqa: E402

from clinicjam import main  # noqa: E402
from clinicjam.auth import LoginLimiter, SessionSigner  # noqa: E402
from clinicjam.cache import ByteLRU, TTLCache  # noqa: E402
from clinicjam.jam import MAX_DEVICES, Jam  # noqa: E402
from clinicjam.models import proxied_art, spotify_id  # noqa: E402

HEADERS = {"X-Device-Id": "testdev", "X-Device-Name": "Tester"}


# NOTE: clinicjam.main is a module-level singleton, so every test file in
# this suite must use the same PINs (see test_auth.py) — whichever file is
# imported first wins.


def client() -> TestClient:
    main.login_limiter = LoginLimiter()  # fresh lockout state per test
    main.jam.devices.clear()
    return TestClient(main.app, client=("127.0.0.1", 50000))


# ---------------- sessions

def test_signed_session_roundtrip_and_tamper():
    s = SessionSigner(b"secret", {"admin": "9999", "guest": "1234"})
    tok = s.issue("guest", 60, "dev-1")
    assert s.verify(tok, "guest") == "dev-1"
    assert s.verify(tok, "admin") is None          # tier is bound
    assert s.verify(tok[:-1] + "0", "guest") is None  # signature
    assert s.verify("", "guest") is None


def test_session_invalidated_when_pin_changes():
    pins = {"admin": "9999", "guest": "1234"}
    s = SessionSigner(b"secret", pins)
    tok = s.issue("admin", 60)
    assert s.verify(tok, "admin") is not None
    pins["admin"] = "111111"
    assert s.verify(tok, "admin") is None


def test_expired_session_rejected():
    s = SessionSigner(b"secret", {"guest": "1234"})
    tok = s.issue("guest", -1)
    assert s.verify(tok, "guest") is None


def test_sessions_survive_restart_without_server_state():
    c = client()
    assert c.post("/api/admin/login", json={"pin": "9999"}).status_code == 200
    cookie = c.cookies.get("clinicjam_admin")
    # a second client (as after a restart) with the same cookie is admin
    c2 = TestClient(main.app, client=("127.0.0.1", 50001))
    c2.cookies.set("clinicjam_admin", cookie)
    assert c2.get("/api/session").json()["admin"] is True
    # and logout clears it
    c.post("/api/admin/logout")
    assert c.get("/api/session").json()["admin"] is False


def test_non_ascii_pin_is_just_wrong_not_500():
    c = client()
    assert c.post("/api/admin/login", json={"pin": "ñandú"}).status_code == 401


# ---------------- brute force

def test_login_lockout_after_failures():
    c = client()
    codes = [c.post("/api/admin/login", json={"pin": "000000"}).status_code for _ in range(6)]
    assert codes[:4] == [401] * 4 and codes[4] == 429 and codes[5] == 429
    # even the right PIN is refused while locked
    assert c.post("/api/admin/login", json={"pin": "9999"}).status_code == 429


def test_limiter_backoff_grows_and_is_bounded():
    lim = LoginLimiter(threshold=2)
    assert lim.record_failure("1.2.3.4") == 0
    assert lim.record_failure("1.2.3.4") == 30
    assert lim.record_failure("1.2.3.4") == 60
    for _ in range(20):
        lim.record_failure("1.2.3.4")
    assert lim.locked_for("1.2.3.4") <= 900
    lim.record_success("1.2.3.4")
    assert lim.locked_for("1.2.3.4") == 0


def test_limiter_memory_is_bounded():
    lim = LoginLimiter()
    for i in range(6000):
        lim.record_failure(f"10.0.{i // 250}.{i % 250}")
    assert len(lim._by_ip) <= lim._max_tracked + 1


# ---------------- request limits + headers

def test_oversized_body_rejected():
    c = client()
    big = "x" * (main.MAX_BODY_BYTES + 10)
    r = c.post("/api/login", content=b'{"pin":"' + big.encode() + b'"}',
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_security_headers_present():
    r = client().get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_captive_probe_redirects_and_api_ignores_host():
    c = client()
    r = c.get("/generate_204", follow_redirects=False)
    assert r.status_code == 302
    r = c.get("/", headers={"Host": "www.example.com"}, follow_redirects=False)
    assert r.status_code == 302
    r = c.get("/api/state", headers={"Host": "www.example.com", **HEADERS})
    assert r.status_code == 200


def test_internal_hook_needs_token():
    c = client()
    r = c.post("/api/internal/librespot-event", json={"event": "end_of_track"})
    assert r.status_code == 403
    r = c.post("/api/internal/librespot-event", json={"event": "end_of_track"},
               headers={"Authorization": f"Bearer {main.hook_token}"})
    assert r.status_code == 200


def test_stream_refused_for_guests():
    c = TestClient(main.app, client=("10.24.7.77", 50002))
    assert c.get("/stream.mp3").status_code == 403


# ---------------- bounded memory

def test_devices_are_bounded_and_uncounted_until_joined():
    jam = Jam({"skip_ratio": 0.5})
    for i in range(MAX_DEVICES + 300):
        jam.touch_device(f"dev{i}", counted=False)
    assert len(jam.devices) <= MAX_DEVICES
    assert jam.active_device_count() == 0
    jam.touch_device("joined", counted=True)
    assert jam.active_device_count() == 1
    jam.devices["joined"]["last_seen"] = time.time() - 25 * 3600
    jam.prune_devices()
    assert "joined" not in jam.devices


def test_unjoined_state_calls_do_not_inflate_listeners():
    c = client()
    for i in range(20):
        c.get("/api/state", headers={"X-Device-Id": f"fake{i}"})
    assert c.get("/api/state", headers=HEADERS).json()["listeners"] == 0


def test_caches_are_bounded():
    t = TTLCache(max_items=3, ttl=60)
    for i in range(10):
        t.put(i, i)
    assert len(t) == 3
    b = ByteLRU(max_bytes=100)
    for i in range(10):
        b.put(str(i), b"x" * 20, "image/jpeg")
    assert b.size <= 100 and b.get("9") is not None and b.get("0") is None
    b.put("huge", b"x" * 90, "image/jpeg")  # bigger than a quarter: ignored
    assert b.get("huge") is None


# ---------------- input validation

def test_spotify_id_validation():
    assert spotify_id("spotify:track:6rqhFgbbKwnb9MLmUQDhG6", "track") == "6rqhFgbbKwnb9MLmUQDhG6"
    assert spotify_id("https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc", "track") == "6rqhFgbbKwnb9MLmUQDhG6"
    assert spotify_id("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M", "playlist") == "37i9dQZF1DXcBWIGoYBM5M"
    assert spotify_id("spotify:track:../../me/player", "track") is None
    assert spotify_id("spotify:playlist:6rqhFgbbKwnb9MLmUQDhG6", "track") is None
    assert spotify_id("6rqhFgbbKwnb9MLmUQDhG6", "track") == "6rqhFgbbKwnb9MLmUQDhG6"


def test_art_proxy_only_allows_spotify_cdn():
    assert proxied_art("https://i.scdn.co/image/" + "a" * 40).startswith("/api/art?u=")
    assert proxied_art("https://evil.example/image/" + "a" * 40) == ""
    c = client()
    assert c.get("/api/art", params={"u": "http://169.254.169.254/latest"}).status_code == 404


def test_state_file_is_private_and_has_backup():
    st = main.state
    st.save()
    assert oct(os.stat(st.path).st_mode & 0o777) == "0o600"
    st.save()
    assert st.path.with_suffix(".bak").exists()
