"""Tests for the dashboard routes, data sources and demo data.

Run with:  pytest tests/test_routes.py

Everything runs in demo mode or with fake ("mock") clients. No test ever
talks to HEVY or Garmin: real network access is blocked by a fixture.
"""
import asyncio
import importlib
import json
import logging
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
    monkeypatch.setattr(sources, "_workouts_cache", {"fetched_at": 0.0, "workouts": None})
    monkeypatch.setattr(sources, "_garmin_cache",
                        {"fetched_at": 0.0, "key": None, "days": None, "error": None, "error_at": 0.0})
    monkeypatch.setattr(sources, "_garmin_client", None)
    monkeypatch.setattr(sources, "_password_login_blocked", False)


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


def test_api_refresh(client):
    assert client.get("/dashboard/api?refresh=1").status_code == 200


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

def test_weight_post_ok_and_same_date_replaced(client, tmp_path):
    day = (date.today() - timedelta(days=1)).isoformat()
    resp = client.post("/dashboard/weight", json={"kg": 95.8, "date": day})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert {"date": day, "kg": 95.8} in body["weight"]["entries"]

    # Same date again → replaced, not duplicated
    client.post("/dashboard/weight", json={"kg": "95,6", "date": day})
    saved = json.loads((tmp_path / "weights.json").read_text(encoding="utf-8"))
    assert saved == [{"date": day, "kg": 95.6}]


def test_weight_post_in_memory_when_no_weights_path(client, monkeypatch):
    monkeypatch.delenv("WEIGHTS_PATH")
    day = (date.today() - timedelta(days=2)).isoformat()
    body = client.post("/dashboard/weight", json={"kg": 97.0, "date": day}).json()
    assert body["ok"] is True
    assert {"date": day, "kg": 97.0} in body["weight"]["entries"]
    assert not (PROJECT_DIR / "weights.json").exists() or "97.0" not in (PROJECT_DIR / "weights.json").read_text()


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
])
def test_weight_post_invalid(client, payload):
    resp = client.post("/dashboard/weight", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]


def test_weight_post_not_json(client):
    resp = client.post("/dashboard/weight", content=b"kg=95", headers={"content-type": "text/plain"})
    assert resp.status_code == 400


# ─── DASHBOARD_TOKEN: login form, cookie and Bearer header ───

@pytest.fixture
def with_token(monkeypatch):
    from dashboard import routes
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setattr(routes, "LOGIN_FAIL_DELAY", 0)


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
    resp = client.post("/dashboard/login", data={"token": TOKEN}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    cookie = resp.headers["set-cookie"]
    assert "fitcoach_dash=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Max-Age=7776000" in cookie
    assert "Secure" not in cookie          # plain http in the test
    assert TOKEN not in cookie             # the cookie holds a hash, not the token

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
    {"X-Forwarded-Host": "abc.ngrok-free.app"},
    {"Forwarded": "for=8.8.8.8;proto=https"},
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
                               ("1.2.3.4:5", "GET", "/workouts?page=1&monkey=2", "1.1", 200), None)
    assert MaskKeyFilter().filter(record) is True
    assert record.getMessage() == '1.2.3.4:5 - "GET /workouts?page=1&monkey=2 HTTP/1.1" 200'


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
    monkeypatch.setattr(sources, "_workouts_cache", {"fetched_at": 0.0, "workouts": None})

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


def test_garmin_timeout_is_cached_as_error(monkeypatch, garmin_env):
    from dashboard import sources
    calls = []

    def slow(days, today):
        calls.append(1)
        time.sleep(0.3)
        return []

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", slow)
    monkeypatch.setattr(sources, "GARMIN_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        with pytest.raises(sources.GarminError, match="svarte ikke"):
            await sources.fetch_garmin_days(7, today=date(2026, 9, 24))
        # Within 20 minutes: the cached error comes back without asking Garmin again
        with pytest.raises(sources.GarminError, match="svarte ikke"):
            await sources.fetch_garmin_days(7, today=date(2026, 9, 24))
        assert len(calls) == 1
        # ?refresh=1 (force) tries again
        with pytest.raises(sources.GarminError):
            await sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24))
        assert len(calls) == 2

    asyncio.run(scenario())


def test_garmin_error_expires_after_20_minutes(monkeypatch, garmin_env):
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


def test_garmin_lock_means_one_fetch_at_a_time(monkeypatch, garmin_env):
    from dashboard import sources
    calls = []

    def fetch(days, today):
        calls.append(1)
        time.sleep(0.1)
        return [sources.empty_garmin_day("2026-09-24")]

    monkeypatch.setattr(sources, "_fetch_garmin_days_sync", fetch)

    async def two_at_once():
        return await asyncio.gather(
            sources.fetch_garmin_days(1, today=date(2026, 9, 24)),
            sources.fetch_garmin_days(1, today=date(2026, 9, 24)),
        )

    first, second = asyncio.run(two_at_once())
    assert first == second
    assert len(calls) == 1   # the second call waited and then used the cache


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
    # …but ?refresh=1 (force=True) allows one new password login
    with pytest.raises(sources.GarminLoginError, match="MFA"):
        asyncio.run(sources.fetch_garmin_days(7, force=True, today=date(2026, 9, 24)))
    assert FakeGarminLogin.password_tries == 2


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
