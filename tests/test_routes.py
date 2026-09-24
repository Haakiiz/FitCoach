"""Tests for the dashboard routes, data sources and demo data.

Run with:  pytest tests/test_routes.py

Everything runs in demo mode or with fake ("mock") clients. No test ever
talks to HEVY or Garmin: real network access is blocked by a fixture.
"""
import asyncio
import importlib
import json
import sys
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
    return TestClient(app)


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
    monkeypatch.setenv("DASHBOARD_NAME", "</script><b>")
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # "</" inside the JSON must be escaped so it can't close the <script> tag
    assert "<\\/script><b>" in resp.text
    assert resp.text.count("</script>") == 1


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


# ─── DASHBOARD_TOKEN ───

def test_token_flow(client, simple_template, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)

    # No key, no cookie → 401 everywhere
    resp = client.get("/dashboard")
    assert resp.status_code == 401
    assert "nøkkel" in resp.text
    assert client.get("/dashboard/api").status_code == 401
    assert client.post("/dashboard/weight", json={"kg": 95}).status_code == 401
    assert client.get("/dashboard?key=wrong").status_code == 401

    # Right key → cookie + redirect to /dashboard without the key
    resp = client.get(f"/dashboard?key={TOKEN}", follow_redirects=False)
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

    # A made-up cookie is rejected
    client.cookies.set("fitcoach_dash", "nope")
    assert client.get("/dashboard/api").status_code == 401


def test_token_key_on_api_and_secure_cookie_on_https(app, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    client = TestClient(app, base_url="https://testserver")
    assert client.get(f"/dashboard/api?key={TOKEN}").status_code == 200
    resp = client.get(f"/dashboard?key={TOKEN}", follow_redirects=False)
    assert "Secure" in resp.headers["set-cookie"]


def test_no_token_means_open(client):
    assert client.get("/dashboard/api").status_code == 200


# ─── The GPT's OpenAPI schema must not change ───

def test_openapi_has_no_dashboard_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/workouts" in paths
    assert "/exercise_templates/all" in paths
    assert not any(p.startswith("/dashboard") for p in paths)


# ─── Errors from HEVY/Garmin still render the page ───

def test_hevy_error_gives_empty_data_and_message(client, monkeypatch):
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
    data = client.get("/dashboard/api").json()
    assert seen == {"workouts": [], "garmin_days": None, "demo": False}
    assert data["sources"]["hevy"] == "error"
    assert data["sources"]["garmin"] == "not_configured"
    assert data["sources"]["errors"] and "HEVY" in data["sources"]["errors"][0]


def test_garmin_login_error_is_reported(client, monkeypatch):
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

    data = client.get("/dashboard/api").json()
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
    monkeypatch.setattr(sources, "_garmin_client", None)
    monkeypatch.setattr(sources, "_garmin_cache", {"fetched_at": 0.0, "key": None, "days": None})

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
