"""Tests for the dashboard routes, data sources and demo data.

Run with:  pytest tests/test_routes.py

Everything runs in demo mode or with fake ("mock") clients. No test ever
talks to HEVY or Garmin: real network access is blocked by a fixture.
"""
import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import threading
import sys
import time
import types
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

# Make "import hevy_proxy" and "import dashboard" work from the tests folder
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

TOKEN = "test-token-123"


# ─── Stub for analytics.build_dashboard ───
# The route tests use this small stand-in, so they test the routes and not the
# analytics maths (that has its own tests in test_analytics.py). One test at the
# bottom runs the real analytics with the demo data.

def _stub_build_dashboard(workouts, template_muscles, garmin_days, weights, today, now,
                          name="", demo=False, sources=None):
    """A tiny stand-in with the same signature as the real build_dashboard (SCHEMA.md)."""
    entries = sorted(weights, key=lambda e: e["date"])
    return {
        "generated_at": now.isoformat(),
        "today": today.isoformat(),
        "demo": demo,
        "name": name,
        "weight": {"current_kg": entries[-1]["kg"] if entries else None, "entries": entries},
        "workout_count": len(workouts),
        "sources": sources,
    }


def _install_stub(monkeypatch):
    stub = types.ModuleType("dashboard.analytics")
    stub.build_dashboard = _stub_build_dashboard
    monkeypatch.setitem(sys.modules, "dashboard.analytics", stub)


# ─── Fixtures ───

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if anything tries to reach the internet."""
    async def blocked(*args, **kwargs):
        raise AssertionError("Real network access in a test!")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture(autouse=True)
def fresh_source_state(monkeypatch):
    """Every test starts with empty caches and new locks (tests use different event loops)."""
    from dashboard import sources
    monkeypatch.setattr(sources, "_workouts_lock", asyncio.Lock())
    monkeypatch.setattr(sources, "_muscles_lock", asyncio.Lock())
    monkeypatch.setattr(sources, "_garmin_lock", asyncio.Lock())
    monkeypatch.setattr(sources, "_muscles_error", {"message": None, "at": float("-inf")})
    monkeypatch.setattr(sources, "_workouts_cache",
                        {"fetched_at": 0.0, "workouts": None, "error": None, "error_at": float("-inf")})
    monkeypatch.setattr(sources, "_garmin_cache",
                        {"fetched_at": 0.0, "key": None, "days": None,
                         "error": None, "error_at": 0.0, "error_is_login": False})
    monkeypatch.setattr(sources, "_garmin_client", None)
    monkeypatch.setattr(sources, "_garmin_task", None)
    monkeypatch.setattr(sources, "_garmin_task_key", None)
    monkeypatch.setattr(sources, "_garmin_task_started", 0.0)
    monkeypatch.setattr(sources, "_garmin_task_box", {})
    monkeypatch.setattr(sources, "_password_login_blocked", False)
    monkeypatch.setattr(sources, "_password_unblocked_at", float("-inf"))
    from dashboard import routes
    monkeypatch.setattr(routes, "_weight_lock", asyncio.Lock())
    monkeypatch.setattr(routes, "_failed_tries", {})


@pytest.fixture
def app(monkeypatch, tmp_path):
    """The real hevy_proxy app, in demo mode, with weigh-ins in a temp file."""
    monkeypatch.setenv("FITCOACH_DEMO", "1")
    import hevy_proxy  # imported after FITCOACH_DEMO is set, so no API key is needed

    from dashboard import routes, sources
    monkeypatch.setattr(sources, "_garmin_login", lambda: pytest.fail("Garmin login in a test!"))
    monkeypatch.setenv("WEIGHTS_PATH", str(tmp_path / "weights.json"))
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_NAME", raising=False)
    # TestClient calls itself "testserver"; allow that name like a real home-network name
    monkeypatch.setenv("DASHBOARD_ALLOWED_HOSTS", "testserver")
    monkeypatch.setattr(routes, "_demo_added_weights", [])
    _install_stub(monkeypatch)
    return hevy_proxy.app


@pytest.fixture
def client(app):
    """A visitor from "outside" (TestClient's default address is not an IP)."""
    return TestClient(app)


@pytest.fixture
def local_client(app):
    """A visitor on the Pi itself (127.0.0.1), with no proxy headers."""
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def no_demo(monkeypatch):
    """Turn demo mode off and replace build_data, so no real fetching happens."""
    from dashboard import routes
    monkeypatch.delenv("FITCOACH_DEMO")

    async def fake_build_data(force=False):
        return {"weight": None, "ok": "real"}

    monkeypatch.setattr(routes, "build_data", fake_build_data)


@pytest.fixture
def simple_template(monkeypatch, tmp_path):
    """A minimal dashboard.html, so these tests don't depend on the real UI template."""
    from dashboard import routes
    folder = tmp_path / "templates"
    folder.mkdir()
    (folder / "dashboard.html").write_text(
        "<html><body><script id='data'>{{ data_json | safe }}</script>{{ data.today }}</body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "TEMPLATES_DIR", folder)
    monkeypatch.setattr(routes, "templates", routes.Jinja2Templates(directory=str(folder)))


# ─── /dashboard/api ───

def test_api_returns_demo_data(client):
    resp = client.get("/dashboard/api")
    assert resp.status_code == 200
    data = resp.json()
    assert data["demo"] is True
    assert data["sources"]["hevy"] == "demo"
    assert data["sources"]["garmin"] == "demo"
    assert data["weight"]["entries"], "demo weigh-ins should be included"
    assert resp.headers["cache-control"] == "no-store"


def test_refresh_only_with_header(client, monkeypatch):
    """Fresh data only with the header X-FitCoach-Refresh: 1 (an <img> can't send headers)."""
    from dashboard import routes
    seen = []
    real_build_data = routes.build_data

    async def spy(force=False):
        seen.append(force)
        return await real_build_data(force=force)

    monkeypatch.setattr(routes, "build_data", spy)
    assert client.get("/dashboard/api", headers={"X-FitCoach-Refresh": "1"}).status_code == 200
    assert client.get("/dashboard/api?refresh=1").status_code == 200
    assert client.get("/dashboard/api", headers={"X-FitCoach-Refresh": "yes"}).status_code == 200
    assert client.get("/dashboard?refresh=1").status_code == 200
    assert client.get("/dashboard", headers={"X-FitCoach-Refresh": "1"}).status_code == 200
    assert seen == [True, False, False, False, False]


def test_comeback_scenario(client, monkeypatch):
    monkeypatch.setenv("FITCOACH_DEMO", "comeback")
    data = client.get("/dashboard/api").json()
    assert data["demo"] is True


def test_build_dashboard_gets_contract_inputs(client, monkeypatch):
    """build_dashboard must be called with exactly the inputs in SCHEMA.md."""
    seen = {}

    def spy(workouts, template_muscles, garmin_days, weights, today, now,
            name="", demo=False, sources=None):
        seen.update(locals())
        return {"weight": None}

    monkeypatch.setitem(sys.modules, "dashboard.analytics", types.SimpleNamespace(build_dashboard=spy))
    monkeypatch.setenv("DASHBOARD_NAME", "Håkon")
    assert client.get("/dashboard/api").status_code == 200

    assert isinstance(seen["workouts"], list) and seen["workouts"]
    assert set(seen["workouts"][0]) >= {"id", "title", "description", "start_time", "end_time", "exercises"}
    assert isinstance(seen["template_muscles"], dict)
    assert len(seen["garmin_days"]) == 7
    assert seen["today"] == seen["now"].date()
    assert seen["now"].tzinfo is not None
    assert seen["name"] == "Håkon"
    assert seen["demo"] is True
    assert seen["sources"] == {"hevy": "demo", "garmin": "demo", "errors": []}


# ─── /dashboard (HTML) ───

def test_page_renders_with_template(client, simple_template, monkeypatch):
    monkeypatch.setenv("DASHBOARD_NAME", "</script><!-- & -->")
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # <, > and & inside the JSON are escaped, so they can't close the <script> tag
    assert "\\u003c/script\\u003e\\u003c!-- \\u0026 --\\u003e" in resp.text
    assert resp.text.count("</script>") == 1
    assert "<!--" not in resp.text
    # ...and the browser still reads the original text back
    embedded = resp.text.split("<script id='data'>")[1].split("</script>")[0]
    assert json.loads(embedded)["name"] == "</script><!-- & -->"


def test_page_fallback_when_template_missing(client, monkeypatch, tmp_path):
    from dashboard import routes
    monkeypatch.setattr(routes, "TEMPLATES_DIR", tmp_path / "nothing-here")
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "/dashboard/api" in resp.text


# ─── POST /dashboard/weight ───

def test_demo_weight_is_memory_only_and_same_date_replaced(client, tmp_path):
    """In demo mode a weigh-in is kept in memory only, even when WEIGHTS_PATH is set."""
    day = (date.today() - timedelta(days=1)).isoformat()
    resp = client.post("/dashboard/weight", json={"kg": 95.8, "date": day})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert {"date": day, "kg": 95.8} in body["weight"]["entries"]

    # Same date again → replaced, not duplicated
    body = client.post("/dashboard/weight", json={"kg": "95,6", "date": day}).json()
    same_day = [e for e in body["weight"]["entries"] if e["date"] == day]
    assert same_day == [{"date": day, "kg": 95.6}]
    assert not (tmp_path / "weights.json").exists()   # nothing written to disk


def test_real_mode_weight_is_saved_to_file(local_client, no_demo, tmp_path):
    day = (date.today() - timedelta(days=1)).isoformat()
    assert local_client.post("/dashboard/weight", json={"kg": 95.8, "date": day}).status_code == 200
    assert local_client.post("/dashboard/weight", json={"kg": "95,6", "date": day}).status_code == 200
    saved = json.loads((tmp_path / "weights.json").read_text(encoding="utf-8"))
    assert saved == [{"date": day, "kg": 95.6}]


@pytest.mark.parametrize("payload", [
    {"kg": 20},
    {"kg": 300},
    {"kg": "abc"},
    {"kg": True},
    {"kg": 95, "date": "24.09.2026"},
    {"kg": 95, "date": (date.today() + timedelta(days=3)).isoformat()},
    {"date": "2026-01-01"},
    [95],
    {"kg": 95, "date": "1999-12-31"},
    {"kg": 95, "date": "0001-01-01"},
    {"kg": 10 ** 400},
    {"kg": "1e999"},
    {"kg": "nan"},
    {"kg": None},
    {"kg": {"a": 1}},
    {"kg": "96_4"},
    {"kg": "٩٦"},
    {"kg": "9"},
    {"kg": 95, "date": "20260901"},
])
def test_weight_post_invalid(client, payload):
    resp = client.post("/dashboard/weight", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded",
                                          "multipart/form-data", None])
def test_weight_post_must_be_json(client, content_type):
    headers = {"content-type": content_type} if content_type else {}
    resp = client.post("/dashboard/weight", content=b'{"kg": 95}', headers=headers)
    assert resp.status_code == 415


def test_weight_post_json_with_charset_ok(client):
    resp = client.post("/dashboard/weight", content=b'{"kg": 95}',
                       headers={"content-type": "application/json; charset=utf-8"})
    assert resp.status_code == 200


def test_weight_post_broken_json(client):
    resp = client.post("/dashboard/weight", content=b"{kg: 95", headers={"content-type": "application/json"})
    assert resp.status_code == 400


# ─── CSRF: other websites can't post to us ───

@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://testserver.evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_posts_are_refused(client, headers):
    assert client.post("/dashboard/weight", json={"kg": 95}, headers=headers).status_code == 403


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "http://testserver"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
])
def test_same_origin_posts_are_allowed(client, headers):
    assert client.post("/dashboard/weight", json={"kg": 95}, headers=headers).status_code == 200


def test_same_origin_behind_ngrok_host_rewrite(client):
    """ngrok with --host-header=rewrite: Host is local, the real host is in X-Forwarded-Host."""
    headers = {"Origin": "https://abc.ngrok-free.app", "X-Forwarded-Host": "abc.ngrok-free.app",
               "X-Forwarded-Proto": "https"}
    assert client.post("/dashboard/weight", json={"kg": 95}, headers=headers).status_code == 200


def test_cross_site_login_is_refused(client, with_token):
    resp = client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False,
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers


# ─── DASHBOARD_TOKEN: login form, cookie and Bearer header ───

@pytest.fixture
def with_token(monkeypatch):
    from dashboard import routes
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)


def test_token_flow(client, simple_template, with_token):
    # Not logged in → the page sends you to the login form, the API says 401
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"
    assert client.get("/dashboard/api").status_code == 401
    assert client.post("/dashboard/weight", json={"kg": 95}).status_code == 401

    # The login form
    resp = client.get("/dashboard/login")
    assert resp.status_code == 200
    assert '<form method="post" action="/dashboard/login">' in resp.text
    assert 'type="password"' in resp.text
    assert "#FAF7F2" in resp.text and "#E8553A" in resp.text and "border-radius: 24px" in resp.text
    assert TOKEN not in resp.text

    # Wrong token → 401 with a friendly message, no cookie
    resp = client.post("/dashboard/login", data={"token": "wrong"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "Feil nøkkel" in resp.text
    assert "set-cookie" not in resp.headers

    # Right token (sent in the POST body) → cookie + redirect to /dashboard
    resp = client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False,
                       headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    cookie = resp.headers["set-cookie"]
    assert "fitcoach_dash=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Max-Age=7776000" in cookie
    assert "Secure" not in cookie          # plain http in the test
    assert TOKEN not in cookie             # the cookie holds an HMAC, not the token
    expected = hmac.new(TOKEN.encode(), b"fitcoach-dash-v1", hashlib.sha256).hexdigest()
    assert f"fitcoach_dash={expected}" in cookie

    # The TestClient keeps the cookie → now everything works
    assert client.get("/dashboard").status_code == 200
    assert client.get("/dashboard/api").status_code == 200
    assert client.post("/dashboard/weight", json={"kg": 95}).status_code == 200
    # Already logged in → the login page goes straight to the dashboard
    assert client.get("/dashboard/login", follow_redirects=False).headers["location"] == "/dashboard"

    # A made-up cookie is rejected
    client.cookies.set("fitcoach_dash", "nope")
    assert client.get("/dashboard/api").status_code == 401


def test_key_in_url_is_no_longer_accepted(client, with_token):
    resp = client.get(f"/dashboard?key={TOKEN}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/login"
    assert "set-cookie" not in resp.headers
    assert client.get(f"/dashboard/api?key={TOKEN}").status_code == 401


def test_bearer_header_on_api(client, with_token):
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/dashboard/api", headers=good).status_code == 200
    assert client.post("/dashboard/weight", json={"kg": 95}, headers=good).status_code == 200
    assert client.get("/dashboard/api", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/dashboard/api", headers={"Authorization": TOKEN}).status_code == 401
    # The header is only for the API; the browser page uses the login form
    assert client.get("/dashboard", headers=good, follow_redirects=False).status_code == 303


def test_secure_cookie_on_https(app, with_token):
    client = TestClient(app, base_url="https://testserver")
    resp = client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False)
    assert "Secure" in resp.headers["set-cookie"]


def test_token_is_required_even_from_localhost(local_client, with_token):
    assert local_client.get("/dashboard/api").status_code == 401


# ─── No DASHBOARD_TOKEN: only direct local requests (demo is open) ───

def test_no_token_demo_mode_is_open(client):
    assert client.get("/dashboard/api").status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1",
                                  "10.1.2.3", "172.16.0.5", "172.31.255.1", "192.168.1.20"])
def test_no_token_local_ip_is_allowed(app, no_demo, host):
    client = TestClient(app, client=(host, 50000))
    assert client.get("/dashboard/api").status_code == 200
    assert client.get("/dashboard").status_code == 200


@pytest.mark.parametrize("host", ["8.8.8.8", "172.32.0.1", "100.64.0.1", "2001:db8::1", "testclient"])
def test_no_token_outside_ip_is_refused(app, no_demo, host):
    client = TestClient(app, client=(host, 50000))
    assert client.get("/dashboard/api").status_code == 401
    resp = client.get("/dashboard")
    assert resp.status_code == 401
    assert "DASHBOARD_TOKEN" in resp.text and ".hevy_env" in resp.text
    assert client.post("/dashboard/weight", json={"kg": 95}).status_code == 401
    assert client.get("/dashboard/login").status_code == 401


@pytest.mark.parametrize("header", [
    {"X-Forwarded-For": "8.8.8.8"},
    {"X-Forwarded-For": ""},
    {"X-Forwarded-Host": "abc.ngrok-free.app"},
    {"X-Forwarded-Proto": "https"},
    {"Forwarded": "for=8.8.8.8;proto=https"},
    {"X-Real-IP": "8.8.8.8"},
    {"Via": "1.1 ngrok"},
    {"CF-Connecting-IP": "8.8.8.8"},
    {"True-Client-IP": "8.8.8.8"},
    {"X-Client-IP": "8.8.8.8"},
])
def test_no_token_proxied_request_is_refused(local_client, no_demo, header):
    """ngrok connects from 127.0.0.1 but adds forwarding headers → treated as outside."""
    assert local_client.get("/dashboard/api", headers=header).status_code == 401
    assert local_client.get("/dashboard", headers=header).status_code == 401


# ─── The token never ends up in uvicorn's access log ───

def test_access_log_filter_masks_key(app, caplog):
    from dashboard.routes import MaskKeyFilter
    access_log = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, MaskKeyFilter) for f in access_log.filters), "install() adds the filter"

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        # Exactly how uvicorn writes an access-log line
        access_log.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5678", "GET",
                        "/dashboard?refresh=1&key=s3cr3t-Token&x=1", "1.1", 303)
        access_log.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5678", "GET", "/dashboard?KEY=abc", "1.1", 303)
    assert "s3cr3t" not in caplog.text and "abc" not in caplog.text
    assert "/dashboard?refresh=1&key=***&x=1" in caplog.text
    assert "/dashboard?KEY=***" in caplog.text


def test_access_log_filter_leaves_other_lines_alone():
    from dashboard.routes import MaskKeyFilter
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/workouts?page=1&pageSize=10", "1.1", 200), None)
    assert MaskKeyFilter().filter(record) is True
    assert record.getMessage() == '1.2.3.4:5 - "GET /workouts?page=1&pageSize=10 HTTP/1.1" 200'


@pytest.mark.parametrize("text, secret", [
    ("/dashboard?token=abc123", "abc123"),
    ("/x?user=a&password=hunter2&y=1", "hunter2"),
    ("/x?api_key=zzz999", "zzz999"),
    ("/x?access_token=tok777", "tok777"),
    ("Authorization: Bearer sekret-bearer", "sekret-bearer"),
    ("{'authorization': 'Basic dXNlcjpwYXNz'}", "dXNlcjpwYXNz"),
])
def test_access_log_filter_masks_other_secrets(text, secret):
    from dashboard.routes import MaskKeyFilter
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", (text,), None)
    MaskKeyFilter().filter(record)
    assert secret not in record.getMessage()
    assert "***" in record.getMessage()


def test_wrong_keys_are_braked_per_ip(app, with_token):
    from dashboard import routes
    client = TestClient(app)                                   # address "testclient"
    for _ in range(routes.FREE_TRIES):
        assert client.post("/dashboard/login", data={"token": "wrong"}).status_code == 401
    resp = client.post("/dashboard/login", data={"token": "wrong"})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    assert "Vent" in resp.text
    # Even the right key must wait now…
    assert client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False).status_code == 429
    # …but someone on another address (you, on your phone) is not affected
    other = TestClient(app, client=("10.0.0.9", 50000))
    assert other.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    # When the wait is over, the right key works and the counter is reset
    count, last = routes._failed_tries["testclient"]
    routes._failed_tries["testclient"] = (count, last - routes.MAX_WAIT_SECONDS - 1)
    assert client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False).status_code == 303
    assert "testclient" not in routes._failed_tries


def test_brake_counts_parallel_guesses(app, with_token):
    """Many guesses at the same moment are still counted one by one."""
    from dashboard import routes

    async def ten_at_once():
        transport = httpx.ASGITransport(app=app)   # client address 127.0.0.1
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            answers = await asyncio.gather(*[ac.post("/dashboard/login", data={"token": f"g{i}"})
                                             for i in range(10)])
        return sorted(a.status_code for a in answers)

    assert asyncio.run(ten_at_once()) == [401] * routes.FREE_TRIES + [429] * (10 - routes.FREE_TRIES)


def test_bearer_is_braked_too(client, with_token):
    from dashboard import routes
    for _ in range(routes.FREE_TRIES):
        assert client.get("/dashboard/api", headers={"Authorization": "Bearer nope"}).status_code == 401
    resp = client.get("/dashboard/api", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 429 and "retry-after" in resp.headers
    assert client.get("/dashboard/api", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429


def test_wait_grows_and_has_a_ceiling():
    from dashboard import routes
    now = time.monotonic()
    waits = []
    for count in (3, 4, 5, 20):
        routes._failed_tries["x"] = (count, now)
        waits.append(routes._seconds_to_wait("x"))
    routes._failed_tries.clear()
    assert waits[0] == 2 and waits[1] == 4 and waits[2] == 8 and waits[3] == routes.MAX_WAIT_SECONDS


def test_login_body_is_limited(client, with_token):
    big = b"token=" + b"a" * 5000
    resp = client.post("/dashboard/login", content=big,
                       headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413

    def chunks():   # no Content-Length: the server must stop reading by itself
        for _ in range(10):
            yield b"a" * 1000

    resp = client.post("/dashboard/login", content=chunks(),
                       headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413


# ─── DNS rebinding: unknown host names are refused when access is by IP ───

@pytest.mark.parametrize("base_url", ["http://evil.example", "http://rebind.attacker.example:8000",
                                      "http://127.0.0.1.evil.example"])
def test_unknown_host_name_is_refused(app, no_demo, base_url, tmp_path):
    client = TestClient(app, client=("127.0.0.1", 50000), base_url=base_url)
    resp = client.get("/dashboard/api")
    assert resp.status_code == 403
    assert "DASHBOARD_ALLOWED_HOSTS" in resp.json()["error"]
    resp = client.get("/dashboard")
    assert resp.status_code == 403 and "DASHBOARD_ALLOWED_HOSTS" in resp.text
    origin = base_url
    resp = client.post("/dashboard/weight", json={"kg": 150},
                       headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 403
    assert not (tmp_path / "weights.json").exists()   # nothing written to disk


def test_unknown_host_name_is_refused_in_demo_too(app):
    client = TestClient(app, base_url="http://evil.example")
    assert client.get("/dashboard/api").status_code == 403


@pytest.mark.parametrize("base_url, allowed_hosts", [
    ("http://localhost:8000", ""),
    ("http://127.0.0.1:8000", ""),
    ("http://192.168.1.20:8000", ""),
    ("http://fitcoach.local:8000", ""),
    ("http://pi.hjemme:8000", "pi.hjemme"),
    ("http://PI.Hjemme:8000", " fitcoach.lan , pi.hjemme "),
])
def test_known_host_names_are_allowed(app, no_demo, monkeypatch, base_url, allowed_hosts):
    monkeypatch.setenv("DASHBOARD_ALLOWED_HOSTS", allowed_hosts)
    client = TestClient(app, client=("192.168.1.30", 50000), base_url=base_url)
    assert client.get("/dashboard/api").status_code == 200


@pytest.mark.parametrize("host, ok", [("[::1]:8000", True), ("[fe80::1]", True), ("evil.example:8000", False)])
def test_host_name_check_ipv6(monkeypatch, host, ok):
    """TestClient can't use IPv6 base URLs, so this checks the helper directly."""
    from starlette.requests import Request
    from dashboard import routes
    monkeypatch.delenv("DASHBOARD_ALLOWED_HOSTS", raising=False)
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": "http",
             "server": ("127.0.0.1", 8000), "headers": [(b"host", host.encode())]}
    assert routes._host_name_allowed(Request(scope)) is ok


def test_host_check_does_not_apply_with_token(app, with_token):
    """With DASHBOARD_TOKEN the key protects us, so any host name (e.g. the ngrok name) works."""
    client = TestClient(app, base_url="https://abc.ngrok-free.app")
    assert client.get("/dashboard/api", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_changing_the_token_logs_everyone_out(client, with_token, monkeypatch):
    client.post("/dashboard/login", data={"token": TOKEN})
    assert client.get("/dashboard/api").status_code == 200
    monkeypatch.setenv("DASHBOARD_TOKEN", "a-brand-new-token")
    assert client.get("/dashboard/api").status_code == 401


def test_install_adds_the_filter_only_once(app):
    from fastapi import FastAPI

    from dashboard import routes
    routes.install(FastAPI())
    routes.install(FastAPI())
    count = sum(isinstance(f, routes.MaskKeyFilter) for f in logging.getLogger("uvicorn.access").filters)
    assert count == 1


# ─── hevy_proxy.py survives a missing dashboard package ───

def test_gpt_endpoints_survive_missing_dashboard(app, monkeypatch):
    import hevy_proxy
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "dashboard.routes", None)   # "import dashboard.routes" now fails
        m.setenv("HEVY_API_KEY", "dummy")                    # no dashboard → no demo mode either
        importlib.reload(hevy_proxy)
        paths = {route.path for route in hevy_proxy.app.routes}
    importlib.reload(hevy_proxy)   # put the normal app back for the other tests
    assert "/workouts" in paths and "/exercise_templates/all" in paths
    assert "/dashboard" not in paths


def test_gpt_endpoints_answer_503_without_api_key(client, monkeypatch):
    import hevy_proxy
    monkeypatch.setattr(hevy_proxy, "HEVY_API_KEY", None)
    resp = client.get("/workouts")
    assert resp.status_code == 503
    assert "HEVY_API_KEY mangler" in resp.json()["detail"]
    resp = client.post("/workouts", json={"workout": {"exercises": []}})
    assert resp.status_code == 503


# ─── The GPT's OpenAPI schema must not change ───

def test_openapi_has_no_dashboard_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/workouts" in paths
    assert "/exercise_templates/all" in paths
    assert not any(p.startswith("/dashboard") for p in paths)


# ─── Errors from HEVY/Garmin still render the page ───

def test_hevy_error_gives_empty_data_and_message(local_client, monkeypatch):
    from dashboard import sources
    monkeypatch.delenv("FITCOACH_DEMO")
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

    async def broken(*args, **kwargs):
        raise httpx.ConnectError("no internet")

    async def no_muscles(*args, **kwargs):
        return {}

    monkeypatch.setattr(sources, "fetch_all_workouts", broken)
    monkeypatch.setattr(sources, "fetch_template_muscles", no_muscles)

    seen = {}

    def spy(workouts, template_muscles, garmin_days, weights, today, now,
            name="", demo=False, sources=None):
        seen.update(workouts=workouts, garmin_days=garmin_days, demo=demo)
        return {"sources": sources, "weight": None}

    monkeypatch.setitem(sys.modules, "dashboard.analytics", types.SimpleNamespace(build_dashboard=spy))
    data = local_client.get("/dashboard/api").json()
    assert seen == {"workouts": [], "garmin_days": None, "demo": False}
    assert data["sources"]["hevy"] == "error"
    assert data["sources"]["garmin"] == "not_configured"
    assert data["sources"]["errors"] and "HEVY" in data["sources"]["errors"][0]


def test_garmin_login_error_is_reported(local_client, monkeypatch):
    from dashboard import sources
    monkeypatch.delenv("FITCOACH_DEMO")
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("GARMIN_PASSWORD", "hemmelig")

    async def workouts(*args, **kwargs):
        return []

    async def muscles(*args, **kwargs):
        return {}

    async def garmin(*args, **kwargs):
        raise sources.GarminLoginError("Innlogging mot Garmin feilet (Test).")

    monkeypatch.setattr(sources, "fetch_all_workouts", workouts)
    monkeypatch.setattr(sources, "fetch_template_muscles", muscles)
    monkeypatch.setattr(sources, "fetch_garmin_days", garmin)
    monkeypatch.setitem(sys.modules, "dashboard.analytics", types.SimpleNamespace(
        build_dashboard=lambda **kw: {"sources": kw["sources"], "garmin_days": kw["garmin_days"]}))

    data = local_client.get("/dashboard/api").json()
    assert data["sources"]["hevy"] == "ok"
    assert data["sources"]["garmin"] == "error"
    assert data["garmin_days"] == []
    assert "hemmelig" not in json.dumps(data)


# ─── sources.py with fake HTTP / fake Garmin ───

def test_fetch_all_workouts_reads_every_page(monkeypatch):
    from dashboard import sources
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.headers["api-key"] == "fake-key"
        assert request.url.params["pageSize"] == "10"
        return httpx.Response(200, json={"page": page, "page_count": 3, "workouts": [{"id": f"w{page}"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "_workouts_cache",
                        {"fetched_at": 0.0, "workouts": None, "error": None, "error_at": float("-inf")})

    result = asyncio.run(sources.fetch_all_workouts("fake-key"))
    assert [w["id"] for w in result] == ["w1", "w2", "w3"]
    assert pages == [1, 2, 3]

    # Second call within 10 minutes comes from the cache
    asyncio.run(sources.fetch_all_workouts("fake-key"))
    assert pages == [1, 2, 3]


def test_fetch_template_muscles_uses_and_writes_cache(monkeypatch, tmp_path):
    from dashboard import sources

    def handler(request):
        if request.url.params["page"] == "2":
            return httpx.Response(404)
        return httpx.Response(200, json={"exercise_templates": [
            {"id": "AAA", "title": "Push Up", "primary_muscle_group": "chest"},
            {"id": "BBB", "title": "Running", "primary_muscle_group": "cardio"},
        ]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "MUSCLES_CACHE_PATH", tmp_path / "muscles.json")
    monkeypatch.setattr(sources, "_muscles_memory", {})

    result = asyncio.run(sources.fetch_template_muscles("fake-key"))
    assert result == {"AAA": "chest", "BBB": "cardio"}
    assert json.loads((tmp_path / "muscles.json").read_text()) == result


class FakeGarmin:
    """Pretends to be garminconnect.Garmin with realistic answers (no network)."""

    def get_body_battery(self, start, end):
        return [{"date": end,
                 "bodyBatteryValueDescriptorDTOList": [
                     {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
                     {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"}],
                 "bodyBatteryValuesArray": [[1, 19], [2, 79], [3, 55]]}]

    def get_activities_by_date(self, start, end):
        return [
            {"activityType": {"typeKey": "treadmill_running"}, "startTimeLocal": f"{end} 06:00:00",
             "distance": 5100.0, "duration": 1890.0},
            {"activityType": {"typeKey": "strength_training"}, "startTimeLocal": f"{end} 19:00:00",
             "distance": 0.0, "duration": 3000.0},
        ]

    def get_sleep_data(self, day):
        return {"dailySleepDTO": {"sleepTimeSeconds": 22320, "deepSleepSeconds": 3840,
                                  "remSleepSeconds": 7020, "lightSleepSeconds": 11400,
                                  "awakeSleepSeconds": 60}}

    def get_stats(self, day):
        return {"totalSteps": 8200, "totalKilocalories": 2650.0, "restingHeartRate": 54}

    def get_hrv_data(self, day):
        raise RuntimeError("no HRV today")   # one broken metric must not break the rest

    def get_spo2_data(self, day):
        return {"averageSpO2": 93.0}

    def get_respiration_data(self, day):
        return {"avgSleepRespirationValue": 12.0}


def test_fetch_garmin_days_normalises(monkeypatch):
    from dashboard import sources
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("GARMIN_PASSWORD", "x")
    monkeypatch.setattr(sources, "_garmin_login", lambda: FakeGarmin())

    today = date(2026, 9, 24)
    days = asyncio.run(sources.fetch_garmin_days(7, today=today))
    assert len(days) == 7
    assert [d["date"] for d in days] == [(today - timedelta(days=6 - i)).isoformat() for i in range(7)]
    for d in days:
        assert set(d) == set(sources.empty_garmin_day("x"))

    last = days[-1]
    assert last["sleep_seconds"] == 22320
    assert last["body_battery_peak"] == 79 and last["body_battery_low"] == 19
    assert last["resting_hr"] == 54 and last["steps"] == 8200 and last["calories"] == 2650
    assert last["hrv_ms"] is None           # that call failed
    assert last["spo2_avg"] == 93 and last["respiration_avg"] == 12.0
    assert last["runs"] == [{"km": 5.1, "minutes": 31.5}]
    assert days[0]["body_battery_peak"] is None


def test_garmin_not_configured_returns_none(monkeypatch):
    from dashboard import sources
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    assert asyncio.run(sources.fetch_garmin_days()) is None


def test_atomic_write_fsyncs_before_replace(tmp_path, monkeypatch):
    from dashboard import sources
    order = []
    real_fsync, real_replace = sources.os.fsync, sources.os.replace
    monkeypatch.setattr(sources.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(sources.os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1])
    sources._write_json_atomic(tmp_path / "x.json", [1, 2])
    assert order == ["fsync", "replace"]
    assert json.loads((tmp_path / "x.json").read_text()) == [1, 2]


@pytest.mark.parametrize("text", ["96_4", "٩٦", "９６", "1e2", "9", "1000", "96,456", "96.", ",5", " ", "96 4"])
def test_weight_text_must_be_plain_digits(text):
    from dashboard import sources
    with pytest.raises(ValueError):
        sources.validate_weight(text, None, date(2026, 9, 24))


@pytest.mark.parametrize("text, kg", [("96", 96.0), ("96,4", 96.4), ("96.45", 96.45), (" 105 ", 105.0)])
def test_weight_text_ok(text, kg):
    from dashboard import sources
    assert sources.validate_weight(text, None, date(2026, 9, 24))[0] == kg


def test_add_weight_file(tmp_path):
    from dashboard import sources
    path = tmp_path / "w.json"
    assert sources.load_weights(path) == []
    sources.add_weight(path, 97.2, "2026-09-01", today=date(2026, 9, 24))
    sources.add_weight(path, 96.8, "2026-08-30", today=date(2026, 9, 24))
    entries = sources.add_weight(path, 97.0, "2026-09-01", today=date(2026, 9, 24))
    assert entries == [{"date": "2026-08-30", "kg": 96.8}, {"date": "2026-09-01", "kg": 97.0}]
    assert not (tmp_path / "w.json.tmp").exists()
    with pytest.raises(ValueError):
        sources.add_weight(path, 95, "2026-09-25", today=date(2026, 9, 24))


# ─── Garmin robustness: timeout, error cache, lock, no password retries ───

@pytest.fixture
def garmin_env(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("GARMIN_PASSWORD", "hemmelig")


def test_garmin_timeout_then_busy_then_result_is_picked_up(monkeypatch, garmin_env):
    from dashboard import sources
    calls = []
    release = threading.Event()

    def slow(days, today):
        calls.append(1)
        release.wait(5)
        return [sources.empty_garmin_day("2026-09-24") | {"steps": 1234}]

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", slow)
    monkeypatch.setattr(sources, "GARMIN_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        with pytest.raises(sources.GarminError, match="svarte ikke"):
            await sources.fetch_garmin_days(1, today=date(2026, 9, 24))
        # Still running: no new thread, not even with force (the "Oppdater" button)
        for force in (False, True, True):
            with pytest.raises(sources.GarminError, match="holder fortsatt på"):
                await sources.fetch_garmin_days(1, force=force, today=date(2026, 9, 24))
        assert len(calls) == 1
        # The thread finishes → the next call picks up its result
        release.set()
        while not sources._garmin_task.done():
            await asyncio.sleep(0.01)
        days = await sources.fetch_garmin_days(1, today=date(2026, 9, 24))
        assert days[0]["steps"] == 1234
        assert len(calls) == 1

    asyncio.run(scenario())


def test_never_more_than_one_garmin_thread(monkeypatch, garmin_env):
    """Same idea as the critic's racetest.py: many forced refreshes, slow Garmin."""
    from dashboard import sources
    active, peak, lock = [0], [0], threading.Lock()

    def slow(days, today):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.3)
        with lock:
            active[0] -= 1
        return []

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", slow)
    monkeypatch.setattr(sources, "GARMIN_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        for _ in range(5):
            with pytest.raises(sources.GarminError):
                await sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24))
        await asyncio.gather(*[sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24))
                               for _ in range(5)], return_exceptions=True)
        await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert peak[0] == 1


def test_garmin_error_is_cached_for_20_minutes(monkeypatch, garmin_env):
    from dashboard import sources
    calls = []

    def broken(days, today):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", broken)
    for _ in range(3):
        with pytest.raises(sources.GarminError, match="Klarte ikke"):
            asyncio.run(sources.fetch_garmin_days(7, today=date(2026, 9, 24)))
    assert len(calls) == 1
    sources._garmin_cache["error_at"] -= sources.GARMIN_ERROR_TTL_SECONDS + 1   # pretend 20 min passed
    with pytest.raises(sources.GarminError):
        asyncio.run(sources.fetch_garmin_days(7, today=date(2026, 9, 24)))
    assert len(calls) == 2
    # force (the "Oppdater" button) skips the error cache
    with pytest.raises(sources.GarminError):
        asyncio.run(sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24)))
    assert len(calls) == 3


def test_new_login_is_only_kept_through_the_job_result(monkeypatch, garmin_env):
    """The thread puts a new login in its job box; only fetch_garmin_days saves it."""
    from dashboard import sources
    monkeypatch.setattr(sources, "_garmin_login", lambda: FakeGarmin())

    box = {}
    sources._run_garmin_job(1, date(2026, 9, 24), box)
    assert isinstance(box["client"], FakeGarmin)
    assert sources._garmin_client is None          # the thread did not touch it

    asyncio.run(sources.fetch_garmin_days(1, today=date(2026, 9, 24)))
    assert isinstance(sources._garmin_client, FakeGarmin)   # saved from the finished job


def test_garmin_shows_last_good_data_when_busy_or_failing(monkeypatch, garmin_env):
    from dashboard import sources
    good = [sources.empty_garmin_day("2026-09-24") | {"steps": 5000}]
    mode = {"fail": False}

    def job(days, today):
        if mode["fail"]:
            raise RuntimeError("boom")
        return good

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", job)
    assert asyncio.run(sources.fetch_garmin_days(1, today=date(2026, 9, 24))) == good

    # Garmin breaks, data is 10 min old → the old data is returned quietly
    mode["fail"] = True
    sources._garmin_cache["fetched_at"] -= 10 * 60
    assert asyncio.run(sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24))) == good

    # Data is 40 min old → GarminError, but with the old data and its age attached
    sources._garmin_cache["fetched_at"] -= 30 * 60
    with pytest.raises(sources.GarminError) as info:
        asyncio.run(sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24)))
    assert info.value.stale_days == good and info.value.stale_minutes == 40

    # Older than 24 hours → no data at all
    sources._garmin_cache["fetched_at"] -= 24 * 3600
    with pytest.raises(sources.GarminError) as info:
        asyncio.run(sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24)))
    assert info.value.stale_days is None


def test_page_uses_stale_garmin_data_with_a_note(local_client, monkeypatch, garmin_env):
    from dashboard import sources
    monkeypatch.delenv("FITCOACH_DEMO")
    stale = [sources.empty_garmin_day("2026-09-24") | {"steps": 5000}]

    async def empty(*args, **kwargs):
        return []

    async def no_muscles(*args, **kwargs):
        return {}

    async def garmin(*args, **kwargs):
        raise sources.GarminError("Garmin svarte ikke.", stale_days=stale, stale_minutes=125)

    monkeypatch.setattr(sources, "fetch_all_workouts", empty)
    monkeypatch.setattr(sources, "fetch_template_muscles", no_muscles)
    monkeypatch.setattr(sources, "fetch_garmin_days", garmin)
    monkeypatch.setitem(sys.modules, "dashboard.analytics", types.SimpleNamespace(
        build_dashboard=lambda **kw: {"sources": kw["sources"], "garmin_days": kw["garmin_days"]}))

    data = local_client.get("/dashboard/api").json()
    assert data["garmin_days"] == stale
    assert data["sources"]["garmin"] == "ok"
    assert "Garmin svarte ikke." in data["sources"]["errors"]
    assert "Viser Garmin-data fra 2 timer og 5 minutter siden." in data["sources"]["errors"]


def test_hung_garmin_job_is_given_up_after_10_minutes(monkeypatch, garmin_env):
    from dashboard import sources
    calls = []
    release = threading.Event()

    def hang(days, today):
        calls.append(1)
        release.wait(5)
        return []

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", hang)
    monkeypatch.setattr(sources, "GARMIN_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        with pytest.raises(sources.GarminError, match="svarte ikke"):
            await sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24))
        with pytest.raises(sources.GarminError, match="holder fortsatt på"):
            await sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24))
        assert len(calls) == 1
        sources._garmin_task_started -= sources.GARMIN_TASK_MAX_SECONDS + 1   # pretend 10 min passed
        with pytest.raises(sources.GarminError):
            await sources.fetch_garmin_days(1, force=True, today=date(2026, 9, 24))
        assert len(calls) == 2   # the hung job was given up and a new one started
        release.set()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())


def test_password_login_keeps_tokenstore_for_refreshed_tokens(monkeypatch, garmin_env, tmp_path):
    import garminconnect
    from dashboard import sources
    dumped = []

    class InnerClient:
        _tokenstore_path = None

        def dump(self, path):
            dumped.append(path)

    class PasswordOnlyGarmin:
        def __init__(self, email=None, password=None, **kwargs):
            self.client = InnerClient()
            self.email = email

        def login(self, tokenstore=None):
            if tokenstore or not self.email:
                raise FileNotFoundError("no tokens")

    monkeypatch.setattr(garminconnect, "Garmin", PasswordOnlyGarmin)
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(tmp_path / "tokens"))
    client = sources._garmin_login()
    assert client.client._tokenstore_path == str(tmp_path / "tokens")
    assert dumped == [str(tmp_path / "tokens")]


def test_hevy_errors_are_cached_for_2_minutes(monkeypatch, tmp_path):
    from dashboard import sources
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "MUSCLES_CACHE_PATH", tmp_path / "muscles.json")
    monkeypatch.setattr(sources, "_muscles_memory", {})

    for _ in range(3):
        with pytest.raises(Exception):
            asyncio.run(sources.fetch_all_workouts("k"))
        with pytest.raises(Exception):
            asyncio.run(sources.fetch_template_muscles("k"))
    assert calls == ["/v1/workouts", "/v1/exercise_templates"]   # asked once each, not 3 times

    # The "Oppdater" button (force) asks HEVY again for workouts
    with pytest.raises(Exception):
        asyncio.run(sources.fetch_all_workouts("k", force=True))
    assert calls.count("/v1/workouts") == 2

    # After 2 minutes HEVY is asked again
    sources._workouts_cache["error_at"] -= sources.HEVY_ERROR_TTL_SECONDS + 1
    sources._muscles_error["at"] -= sources.HEVY_ERROR_TTL_SECONDS + 1
    with pytest.raises(Exception):
        asyncio.run(sources.fetch_all_workouts("k"))
    with pytest.raises(Exception):
        asyncio.run(sources.fetch_template_muscles("k"))
    assert calls.count("/v1/workouts") == 3 and calls.count("/v1/exercise_templates") == 2


def test_old_muscle_file_is_used_while_hevy_is_down(monkeypatch, tmp_path):
    import os as _os
    from dashboard import sources
    old = tmp_path / "muscles.json"
    old.write_text(json.dumps({"OLD": "chest"}))
    week_ago = time.time() - 8 * 24 * 3600
    _os.utime(old, (week_ago, week_ago))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw))
    monkeypatch.setattr(sources, "MUSCLES_CACHE_PATH", old)
    monkeypatch.setattr(sources, "_muscles_memory", {})
    assert asyncio.run(sources.fetch_template_muscles("k")) == {"OLD": "chest"}
    assert asyncio.run(sources.fetch_template_muscles("k")) == {"OLD": "chest"}   # during the 2 min pause


def test_garmin_days_are_fetched_at_most_4_at_a_time(monkeypatch, garmin_env):
    from dashboard import sources
    active, peak, lock = [0], [0], threading.Lock()

    class CountingGarmin(FakeGarmin):
        def get_sleep_data(self, day):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return super().get_sleep_data(day)

    monkeypatch.setattr(sources, "_garmin_login", lambda: CountingGarmin())
    days = sources._fetch_garmin_days_sync(7, date(2026, 9, 24))
    assert [d["date"] for d in days] == [f"2026-09-{n}" for n in range(18, 25)]   # still in order
    assert 1 < peak[0] <= 4


class FakeGarminLogin:
    """Fake garminconnect.Garmin: no saved tokens, and the password login wants MFA."""
    token_tries = 0
    password_tries = 0

    def __init__(self, email=None, password=None, **kwargs):
        self.email = email

    def login(self, tokenstore=None):
        if tokenstore:
            FakeGarminLogin.token_tries += 1
            raise FileNotFoundError("no tokens")
        FakeGarminLogin.password_tries += 1
        raise Exception("MFA Required but no prompt_mfa mechanism supplied")


def test_password_login_is_not_retried_after_failure(monkeypatch, garmin_env, tmp_path):
    import garminconnect
    from dashboard import sources
    monkeypatch.setattr(garminconnect, "Garmin", FakeGarminLogin)
    monkeypatch.setattr(FakeGarminLogin, "token_tries", 0)
    monkeypatch.setattr(FakeGarminLogin, "password_tries", 0)
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(tmp_path / "tokens"))

    # 1st try: tokens fail, the password login needs MFA → clear Norwegian message
    with pytest.raises(sources.GarminLoginError, match="MFA"):
        sources._garmin_login()
    assert (FakeGarminLogin.token_tries, FakeGarminLogin.password_tries) == (1, 1)

    # 2nd try: tokens are tried again, but the password is NOT
    with pytest.raises(sources.GarminLoginError, match="Oppdater"):
        sources._garmin_login()
    assert (FakeGarminLogin.token_tries, FakeGarminLogin.password_tries) == (2, 1)

    # Through fetch_garmin_days: normal loads don't retry the password…
    with pytest.raises(sources.GarminLoginError):
        asyncio.run(sources.fetch_garmin_days(7, today=date(2026, 9, 24)))
    assert FakeGarminLogin.password_tries == 1
    # …but "Oppdater" (force=True) allows one new password login
    with pytest.raises(sources.GarminLoginError, match="MFA"):
        asyncio.run(sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24)))
    assert FakeGarminLogin.password_tries == 2
    # …and only once per 10 minutes, however often you press it
    for _ in range(3):
        with pytest.raises(sources.GarminLoginError, match="Oppdater"):
            asyncio.run(sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24)))
    assert FakeGarminLogin.password_tries == 2
    sources._password_unblocked_at -= sources.PASSWORD_RETRY_SECONDS + 1   # pretend 10 min passed
    with pytest.raises(sources.GarminLoginError, match="MFA"):
        asyncio.run(sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24)))
    assert FakeGarminLogin.password_tries == 3


def test_garmin_timeout_shows_on_page(local_client, monkeypatch, garmin_env):
    from dashboard import sources
    monkeypatch.delenv("FITCOACH_DEMO")

    async def empty(*args, **kwargs):
        return []

    async def no_muscles(*args, **kwargs):
        return {}

    def slow(days, today):
        time.sleep(0.3)
        return []

    monkeypatch.setattr(sources, "fetch_all_workouts", empty)
    monkeypatch.setattr(sources, "fetch_template_muscles", no_muscles)
    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", slow)
    monkeypatch.setattr(sources, "GARMIN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(sys.modules, "dashboard.analytics", types.SimpleNamespace(
        build_dashboard=lambda **kw: {"sources": kw["sources"], "garmin_days": kw["garmin_days"]}))

    data = local_client.get("/dashboard/api").json()
    assert data["sources"]["garmin"] == "error"
    assert data["garmin_days"] == []
    assert any("Garmin svarte ikke" in e for e in data["sources"]["errors"])


# ─── Page limits and warnings ───

def test_workout_page_limit_logs_warning(monkeypatch, caplog):
    from dashboard import sources

    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"page": page, "page_count": 99, "workouts": [{"id": f"w{page}"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "MAX_WORKOUT_PAGES", 2)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        result = asyncio.run(sources.fetch_all_workouts("fake-key"))
    assert [w["id"] for w in result] == ["w1", "w2"]
    assert "MAX_WORKOUT_PAGES" in caplog.text


def test_template_page_limit(monkeypatch, tmp_path, caplog):
    from dashboard import sources
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(200, json={"exercise_templates": [
            {"id": f"T{page}", "title": "x", "primary_muscle_group": "chest"}]})   # never ends

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "MUSCLES_CACHE_PATH", tmp_path / "muscles.json")
    monkeypatch.setattr(sources, "_muscles_memory", {})
    monkeypatch.setattr(sources, "MAX_TEMPLATE_PAGES", 3)
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        result = asyncio.run(sources.fetch_template_muscles("fake-key"))
    assert pages == [1, 2, 3]
    assert result == {"T1": "chest", "T2": "chest", "T3": "chest"}
    assert "MAX_TEMPLATE_PAGES" in caplog.text


def test_template_muscles_lock_downloads_once(monkeypatch, tmp_path):
    from dashboard import sources
    pages = []

    async def handler(request):
        pages.append(request.url.params["page"])
        await asyncio.sleep(0.05)
        if request.url.params["page"] == "2":
            return httpx.Response(404)
        return httpx.Response(200, json={"exercise_templates": [
            {"id": "AAA", "title": "Push Up", "primary_muscle_group": "chest"}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(sources.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(sources, "MUSCLES_CACHE_PATH", tmp_path / "muscles.json")
    monkeypatch.setattr(sources, "_muscles_memory", {})

    async def two_at_once():
        return await asyncio.gather(sources.fetch_template_muscles("k"), sources.fetch_template_muscles("k"))

    first, second = asyncio.run(two_at_once())
    assert first == second == {"AAA": "chest"}
    assert pages == ["1", "2"]   # downloaded once; the second call used the fresh cache


def test_weight_date_and_number_limits():
    from dashboard import sources
    today = date(2026, 9, 24)
    assert sources.validate_weight("96,4", "2000-01-01", today) == (96.4, "2000-01-01")
    for kg, day in [(95, "1999-12-31"), (10 ** 400, None), ("1e999", None), (float("inf"), None),
                    (float("nan"), None), ("", None)]:
        with pytest.raises(ValueError):
            sources.validate_weight(kg, day, today)


# ─── Demo data sanity ───

def test_demo_data_shape():
    from dashboard import demo_data
    today = date(2026, 9, 24)
    active = demo_data.demo_workouts(today, "active")
    assert active == demo_data.demo_workouts(today, "active")          # deterministic
    assert active[0]["start_time"].startswith((today - timedelta(days=2)).isoformat())
    assert active[0]["start_time"].endswith("+00:00")
    comeback = demo_data.demo_workouts(today, "comeback")
    assert comeback[0]["start_time"].startswith((today - timedelta(days=99)).isoformat())

    muscles = demo_data.demo_template_muscles()
    for w in active:
        for ex in w["exercises"]:
            assert ex["exercise_template_id"] in muscles
            # Same keys as the real HEVY GET /v1/workouts answer (it uses "superset_id")
            assert set(ex) == {"index", "title", "notes", "exercise_template_id", "superset_id", "sets"}
            for s in ex["sets"]:
                assert set(s) == {"index", "type", "weight_kg", "reps", "distance_meters",
                                  "duration_seconds", "rpe", "custom_metric"}
                if ex["title"] == "Romanian Deadlift (Barbell)":
                    assert s["weight_kg"] <= 90

    garmin = demo_data.demo_garmin_days(today)
    assert len(garmin) == 7 and garmin[-1]["date"] == today.isoformat()
    weights = demo_data.demo_weights(today)
    assert weights[0]["kg"] == 102.0 and weights[-1]["kg"] == 96.4


# ─── The real analytics with the demo data (end to end) ───

@pytest.mark.parametrize("scenario", ["1", "comeback"])
def test_real_analytics_with_demo_data(client, monkeypatch, scenario):
    monkeypatch.delitem(sys.modules, "dashboard.analytics")   # remove the stub
    try:
        importlib.import_module("dashboard.analytics")
    except ImportError:
        pytest.skip("dashboard/analytics.py is not written yet")
    monkeypatch.setenv("FITCOACH_DEMO", scenario)

    resp = client.get("/dashboard/api")
    assert resp.status_code == 200
    data = resp.json()
    assert data["demo"] is True
    assert data["sources"]["hevy"] == "demo"
    assert data["weight"]["current_kg"] == 96.4
