"""Web routes for the dashboard.

    GET  /dashboard          → the HTML page
    GET  /dashboard/login    → a small login form (only used when DASHBOARD_TOKEN is set)
    POST /dashboard/login    → checks the token from the form and sets a login cookie
    GET  /dashboard/api      → the same data as JSON (header "X-FitCoach-Refresh: 1" fetches fresh data)
    POST /dashboard/weight   → save a weigh-in, body: {"kg": 96.4, "date": "2026-09-24"}

All routes use include_in_schema=False, so they do NOT show up in /openapi.json
(the file the Custom GPT reads). The GPT only sees the original HEVY endpoints.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import jinja2  # noqa: F401  (imported so a missing package gives a clear ImportError)
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import sources
from .demo_data import (
    demo_garmin_days,
    demo_scenario,
    demo_template_muscles,
    demo_weights,
    demo_workouts,
)

# "uvicorn.error" is uvicorn's normal log, so these messages show up next to uvicorn's own lines
log = logging.getLogger("uvicorn.error")

router = APIRouter()

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

COOKIE_NAME = "fitcoach_dash"
COOKIE_MAX_AGE = 90 * 24 * 3600   # 90 days
MAX_LOGIN_BODY = 4096            # the login form is tiny; refuse anything bigger
REFRESH_HEADER = "x-fitcoach-refresh"   # the page sends "X-FitCoach-Refresh: 1" to skip the caches

# Weigh-ins added in demo mode (kept in memory only, so an open demo never writes to disk)
_demo_added_weights: list[dict] = []
_weight_lock = asyncio.Lock()


# ─── Settings from the environment ───

def _local_tz() -> ZoneInfo:
    """Time zone for "today" and the greeting (env DASHBOARD_TZ, default Europe/Oslo)."""
    try:
        return ZoneInfo(os.getenv("DASHBOARD_TZ") or "Europe/Oslo")
    except Exception:
        log.warning("[dashboard] unknown DASHBOARD_TZ, using Europe/Oslo")
        return ZoneInfo("Europe/Oslo")


def _local_now() -> datetime:
    return datetime.now(_local_tz())


# ─── Access control ───
#
# With DASHBOARD_TOKEN set (recommended):
#   - the browser logs in once on /dashboard/login and gets a cookie (90 days)
#   - curl/scripts can send the header  "Authorization: Bearer <token>"  to the API routes
#   The token is never put in the URL, so it can't leak into logs or browser history.
#
# Without DASHBOARD_TOKEN:
#   - only direct requests from this machine or the home network get in
#     (not requests that come through ngrok, which adds X-Forwarded-* headers)
#   - demo mode (FITCOACH_DEMO) is open, since it only shows made-up data
#   - in both cases the address in the browser must be a name we know
#     (localhost, an IP address, *.local or DASHBOARD_ALLOWED_HOSTS)

# Networks that count as "local": this machine and ordinary home networks
LOCAL_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]
# Headers that proxies and tunnels (ngrok, Cloudflare, nginx …) add.
# If any of them is present, the request came from outside, even if it
# reaches us from 127.0.0.1.
PROXY_HEADERS = (
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded",
    "x-real-ip", "via", "cf-connecting-ip", "true-client-ip", "x-client-ip",
)


def _cookie_value(token: str) -> str:
    """What we store in the cookie: HMAC(token, "fitcoach-dash-v1").

    A fingerprint of the token, not the token itself. Changing DASHBOARD_TOKEN
    changes the fingerprint, which logs out every browser.
    """
    return hmac.new(token.encode(), b"fitcoach-dash-v1", hashlib.sha256).hexdigest()


def _same(a: str, b: str) -> bool:
    """Compare two secrets in constant time (so nobody can guess them by timing)."""
    return secrets.compare_digest(a.encode(), b.encode())


def _is_local_direct(request: Request) -> bool:
    """True when the request comes straight from this machine or the home network."""
    if any(header in request.headers for header in PROXY_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False   # not an IP address at all
    if ip.version == 6 and ip.ipv4_mapped:   # "::ffff:127.0.0.1" is really 127.0.0.1
        ip = ip.ipv4_mapped
    return any(ip in net for net in LOCAL_NETWORKS)


def _host_name_allowed(request: Request) -> bool:
    """Protection against "DNS rebinding" when access is based on the IP address.

    A bad website can make its own name (e.g. rebind.attacker.example) point to
    127.0.0.1 or the Pi, and then its JavaScript could read the dashboard. The
    browser still sends the attacker's name in the Host header, so we only accept
    names we know: localhost, a plain IP address, *.local, or a name listed in
    DASHBOARD_ALLOWED_HOSTS (comma-separated, e.g. "pi.hjemme,fitcoach.lan").
    """
    name = (request.url.hostname or "").lower().rstrip(".")
    if name == "localhost" or name.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    extra = os.getenv("DASHBOARD_ALLOWED_HOSTS", "")
    return name in {h.strip().lower().rstrip(".") for h in extra.split(",") if h.strip()}


# ─── Brake on wrong keys (per IP address) ───
# The first FREE_TRIES wrong keys from one address are free (typos happen).
# After that the address must wait 2, 4, 8 … seconds (max 5 min) before the
# next try, and gets "429 Too Many Requests" until then. Other addresses (e.g.
# you on your phone) are not affected. This slows guessing down, but an attacker
# with many IP addresses is not stopped by it – the long random key is the real
# protection.

FREE_TRIES = 3
MAX_WAIT_SECONDS = 300
_failed_tries: dict[str, tuple[int, float]] = {}   # ip → (wrong tries in a row, time of the last one)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _seconds_to_wait(ip: str) -> int:
    """How long `ip` must still wait before it may try a key again (0 = go ahead)."""
    count, last = _failed_tries.get(ip, (0, 0.0))
    if count < FREE_TRIES:
        return 0
    wait = min(2 ** (count - FREE_TRIES + 1), MAX_WAIT_SECONDS)
    return max(0, math.ceil(last + wait - time.monotonic()))


def _record_wrong_try(ip: str) -> None:
    count, _ = _failed_tries.get(ip, (0, 0.0))
    _failed_tries[ip] = (count + 1, time.monotonic())
    if len(_failed_tries) > 1000:   # forget addresses that have been quiet for an hour
        cutoff = time.monotonic() - 3600
        for old_ip in [k for k, (_, t) in _failed_tries.items() if t < cutoff]:
            del _failed_tries[old_ip]


def _check_key(ip: str, given: str, token: str) -> str:
    """Check a typed key or Bearer key. Returns "ok", "wrong" or "wait".

    There is no `await` in here, so even many requests at the same time are
    counted one by one.
    """
    if _seconds_to_wait(ip) > 0:
        return "wait"
    if given and _same(given, token):
        _failed_tries.pop(ip, None)
        return "ok"
    _record_wrong_try(ip)
    return "wrong"


def _too_many_tries_json(ip: str) -> JSONResponse:
    wait = _seconds_to_wait(ip)
    return JSONResponse(
        {"ok": False, "error": f"For mange feil nøkler. Vent {wait} sekunder og prøv igjen."},
        status_code=429, headers={"Retry-After": str(wait)},
    )


# ─── Who may see the dashboard? ───

def _check_access(request: Request, api: bool) -> Response | None:
    """Return None when the visitor may continue, otherwise the response to send back."""
    token = os.getenv("DASHBOARD_TOKEN", "")

    if token:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and _same(cookie, _cookie_value(token)):
            return None
        auth = request.headers.get("authorization", "")
        if api and auth.lower().startswith("bearer "):
            result = _check_key(_client_ip(request), auth[7:].strip(), token)
            if result == "ok":
                return None
            if result == "wait":
                return _too_many_tries_json(_client_ip(request))
        if api:
            return JSONResponse(
                {"ok": False, "error": "Ingen tilgang. Logg inn på /dashboard/login først."},
                status_code=401,
            )
        return RedirectResponse("/dashboard/login", status_code=303)

    # No token: only direct local visitors (or demo), and only under a known name
    if demo_scenario() or _is_local_direct(request):
        if _host_name_allowed(request):
            return None
        if api:
            return JSONResponse({"ok": False, "error": WRONG_HOST_TEXT}, status_code=403)
        return HTMLResponse(WRONG_HOST_HTML, status_code=403, headers=NO_STORE)

    if api:
        return JSONResponse(
            {"ok": False, "error": "Ingen tilgang. Sett DASHBOARD_TOKEN i .hevy_env for å bruke dashboardet utenfra."},
            status_code=401,
        )
    return HTMLResponse(NO_TOKEN_HTML, status_code=401, headers=NO_STORE)


def _cross_site_refusal(request: Request) -> Response | None:
    """Stop other websites from sending forms/requests to our POST routes (CSRF).

    When the dashboard is open because of the visitor's IP address, a bad web page
    in the same browser could otherwise post to it. Browsers tell us where a
    request comes from with the Origin and Sec-Fetch-Site headers.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return JSONResponse({"ok": False, "error": "Forespørselen kom fra en annen nettside."}, status_code=403)

    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {f"{request.url.scheme}://{request.url.netloc}"}
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:   # behind ngrok with --host-header=rewrite
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            allowed.add(f"{proto}://{forwarded_host}")
        if origin.lower().rstrip("/") not in {a.lower() for a in allowed}:
            return JSONResponse({"ok": False, "error": "Forespørselen kom fra en annen nettside."}, status_code=403)
    return None


# ─── Collect the data and build the dashboard ───

def _age_text(minutes: int | None) -> str:
    """40 → "40 minutter", 125 → "2 timer og 5 minutter"."""
    minutes = minutes or 0
    if minutes < 60:
        return f"{minutes} minutter"
    hours, rest = divmod(minutes, 60)
    hours_text = "1 time" if hours == 1 else f"{hours} timer"
    return f"{hours_text} og {rest} minutter" if rest else hours_text


def _stored_weights_demo(today: date) -> list[dict]:
    """Demo weigh-ins plus anything added through the form (kept in memory only)."""
    entries = demo_weights(today)
    for e in _demo_added_weights:
        entries = sources.upsert_weight(entries, e["kg"], e["date"])
    return entries


async def _gather_inputs(today: date, force: bool) -> dict:
    """Fetch everything build_dashboard needs. Never raises for HEVY/Garmin problems:
    failures become empty data plus a Norwegian message in sources["errors"]."""
    scenario = demo_scenario()
    if scenario:
        # Demo: fake data, no network at all
        return {
            "workouts": demo_workouts(today, scenario),
            "template_muscles": demo_template_muscles(),
            "garmin_days": demo_garmin_days(today, scenario),
            "weights": _stored_weights_demo(today),
            "demo": True,
            "sources": {"hevy": "demo", "garmin": "demo", "errors": []},
        }

    errors: list[str] = []
    status = {"hevy": "ok", "garmin": "ok"}
    api_key = os.getenv("HEVY_API_KEY", "")

    # Ask HEVY and Garmin at the same time. return_exceptions=True means an error
    # comes back as a value instead of stopping everything.
    # (fetch_garmin_days returns None straight away when Garmin is not configured.)
    workouts, muscles, garmin_days = await asyncio.gather(
        sources.fetch_all_workouts(api_key, force=force),
        sources.fetch_template_muscles(api_key),
        sources.fetch_garmin_days(7, force=force, today=today),
        return_exceptions=True,
    )

    if isinstance(workouts, Exception):
        log.warning("[dashboard] HEVY workouts failed: %r", workouts)
        errors.append("Klarte ikke å hente økter fra HEVY. Prøv å laste siden på nytt om litt.")
        status["hevy"] = "error"
        workouts = []

    if isinstance(muscles, Exception):
        log.warning("[dashboard] HEVY exercise templates failed: %r", muscles)
        if status["hevy"] == "ok":
            errors.append("Klarte ikke å hente muskelgrupper fra HEVY, så volum per muskelgruppe kan mangle.")
        muscles = {}

    if not sources.garmin_configured():
        status["garmin"] = "not_configured"
        garmin_days = None
    elif isinstance(garmin_days, Exception):
        error = garmin_days
        log.warning("[dashboard] Garmin failed: %s", type(error).__name__)
        stale = getattr(error, "stale_days", None)
        if isinstance(error, sources.GarminError):
            errors.append(str(error))
        else:
            errors.append("Klarte ikke å hente data fra Garmin. Prøv igjen senere.")
        if stale:
            # Show the last good data, and say clearly how old it is
            errors.append(f"Viser Garmin-data fra {_age_text(error.stale_minutes)} siden.")
            status["garmin"] = "ok"
            garmin_days = stale
        else:
            status["garmin"] = "error"
            garmin_days = []

    try:
        weights = sources.load_weights(sources.weights_path())
    except Exception as err:
        log.warning("[dashboard] could not read weights: %s", err)
        errors.append("Klarte ikke å lese vektmålingene (weights.json).")
        weights = []

    return {
        "workouts": workouts,
        "template_muscles": muscles,
        "garmin_days": garmin_days,
        "weights": weights,
        "demo": False,
        "sources": {**status, "errors": errors},
    }


async def build_data(force: bool = False) -> dict:
    """Everything the page shows, in the shape described in SCHEMA.md."""
    # Imported here so the app still starts while analytics.py is being written
    from .analytics import build_dashboard

    now = _local_now()
    today = now.date()
    inputs = await _gather_inputs(today, force)
    return build_dashboard(
        workouts=inputs["workouts"],
        template_muscles=inputs["template_muscles"],
        garmin_days=inputs["garmin_days"],
        weights=inputs["weights"],
        today=today,
        now=now,
        name=os.getenv("DASHBOARD_NAME", ""),
        demo=inputs["demo"],
        sources=inputs["sources"],
    )


def _to_script_json(data: dict) -> str:
    """JSON that is safe to put inside a <script> tag.

    <, > and & are written as \\u003c, \\u003e and \\u0026. JSON reads them back
    as the same characters, but the browser can never see "</script>" or "<!--" in them.
    """
    text = json.dumps(data, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


NO_STORE = {"Cache-Control": "no-store"}   # personal data: don't let browsers/proxies keep copies


# ─── Routes ───

@router.get("/dashboard", include_in_schema=False)
async def dashboard_page(request: Request):
    """The dashboard web page."""
    blocked = _check_access(request, api=False)
    if blocked is not None:
        return blocked

    try:
        # No refresh here on purpose: a GET link or <img> from another site must
        # not be able to make us hammer HEVY/Garmin. The page's own button uses the API.
        data = await build_data()
    except Exception:
        log.exception("[dashboard] building the dashboard failed")
        return HTMLResponse(ERROR_HTML, status_code=500, headers=NO_STORE)

    if not (TEMPLATES_DIR / "dashboard.html").exists():
        return HTMLResponse(FALLBACK_HTML, headers=NO_STORE)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "data": data, "data_json": _to_script_json(data)},
        headers=NO_STORE,
    )


@router.get("/dashboard/login", include_in_schema=False)
async def login_page(request: Request):
    """Show the login form (or go straight to the dashboard when no login is needed)."""
    token = os.getenv("DASHBOARD_TOKEN", "")
    if not token:
        blocked = _check_access(request, api=False)
        return blocked if blocked is not None else RedirectResponse("/dashboard", status_code=303)
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and _same(cookie, _cookie_value(token)):
        return RedirectResponse("/dashboard", status_code=303)
    return HTMLResponse(_login_html(), headers=NO_STORE)


@router.post("/dashboard/login", include_in_schema=False)
async def login_submit(request: Request):
    """Check the token from the form. Right token → cookie + back to /dashboard."""
    token = os.getenv("DASHBOARD_TOKEN", "")
    if not token:
        return RedirectResponse("/dashboard", status_code=303)
    refused = _cross_site_refusal(request)
    if refused is not None:
        return refused

    # The form is sent as "token=...". We read it by hand (FastAPI's form
    # support needs an extra package). The token is in the body, never in the URL.
    # We read at most 4096 bytes, so nobody can make us swallow a huge upload.
    too_big = HTMLResponse(_login_html(error="Forespørselen var for stor."), status_code=413, headers=NO_STORE)
    try:
        if int(request.headers.get("content-length") or 0) > MAX_LOGIN_BODY:
            return too_big
    except ValueError:
        return too_big
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_LOGIN_BODY:
            return too_big
    fields = parse_qs(body.decode("utf-8", errors="replace"))
    given = (fields.get("token") or [""])[0].strip()

    ip = _client_ip(request)
    result = _check_key(ip, given, token)
    if result == "wait":
        wait = _seconds_to_wait(ip)
        return HTMLResponse(_login_html(error=f"For mange feil nøkler. Vent {wait} sekunder og prøv igjen."),
                            status_code=429, headers={**NO_STORE, "Retry-After": str(wait)})
    if result == "wrong":
        return HTMLResponse(_login_html(error="Feil nøkkel. Prøv igjen."), status_code=401, headers=NO_STORE)

    response = RedirectResponse("/dashboard", status_code=303)
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE_NAME,
        _cookie_value(token),
        max_age=COOKIE_MAX_AGE,
        httponly=True,        # JavaScript can't read it
        samesite="lax",       # other websites can't send it along with their forms
        secure=is_https,      # only sent over https when we are on https (ngrok)
    )
    return response


@router.get("/dashboard/api", include_in_schema=False)
async def dashboard_api(request: Request):
    """The dashboard data as JSON.

    To skip the caches, send the header "X-FitCoach-Refresh: 1". It is a header
    (not ?refresh=1) because other websites cannot add custom headers to a
    request, but they can make the browser load any URL, e.g. with <img>.
    """
    blocked = _check_access(request, api=True)
    if blocked is not None:
        return blocked
    try:
        data = await build_data(force=request.headers.get(REFRESH_HEADER) == "1")
    except Exception:
        log.exception("[dashboard] building the dashboard failed")
        return JSONResponse({"ok": False, "error": "Noe gikk galt da dashboardet skulle bygges."},
                            status_code=500, headers=NO_STORE)
    return JSONResponse(data, headers=NO_STORE)


@router.post("/dashboard/weight", include_in_schema=False)
async def dashboard_weight(request: Request):
    """Save a weigh-in. Body: {"kg": 96.4, "date": "YYYY-MM-DD" (optional, default today)}."""
    blocked = _check_access(request, api=True)
    if blocked is not None:
        return blocked
    refused = _cross_site_refusal(request)
    if refused is not None:
        return refused
    # Only real JSON. A plain HTML form on another site can send text/plain or
    # form data without asking, but not application/json.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JSONResponse({"ok": False, "error": "Send vekten som JSON (Content-Type: application/json)."},
                            status_code=415)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "Ugyldig forespørsel. Send JSON med feltet «kg»."}, status_code=400)
    if not isinstance(body, dict) or "kg" not in body:
        return JSONResponse({"ok": False, "error": "Du må fylle inn vekten i kg."}, status_code=400)

    today = _local_now().date()
    try:
        async with _weight_lock:   # one save at a time, so two quick clicks can't clash
            if demo_scenario():   # demo: memory only, never to disk
                kg, day = sources.validate_weight(body["kg"], body.get("date"), today)
                _demo_added_weights[:] = sources.upsert_weight(_demo_added_weights, kg, day)
            else:
                sources.add_weight(sources.weights_path(), body["kg"], body.get("date"), today)
    except ValueError as err:
        return JSONResponse({"ok": False, "error": str(err)}, status_code=400)
    except (OverflowError, TypeError):
        return JSONResponse({"ok": False, "error": "Vekten må være et tall, for eksempel 96,4."}, status_code=400)
    except Exception:
        log.exception("[dashboard] saving weight failed")
        return JSONResponse({"ok": False, "error": "Klarte ikke å lagre vekten. Prøv igjen."}, status_code=500)

    try:
        data = await build_data()
    except Exception:
        log.exception("[dashboard] building the dashboard failed after saving weight")
        return JSONResponse({"ok": True, "weight": None})
    return JSONResponse({"ok": True, "weight": data["weight"]}, headers=NO_STORE)


# ─── Hide secrets in uvicorn's access log ───

class MaskKeyFilter(logging.Filter):
    """Hides secrets in access-log lines: key=, token=, password= (also api_key=,
    access_token= …) and Authorization values become ***.

    The dashboard never puts the token in the URL, but an old bookmark like
    /dashboard?key=... must still never end up in the log.
    """
    QUERY_SECRET = re.compile(r"((?:^|[?&;\s])[\w.-]*(?:key|token|password)=)[^&\s\"']*", re.IGNORECASE)
    AUTHORIZATION = re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)(?:[A-Za-z]+\s+)?[^\s\"',}]+", re.IGNORECASE)

    def _mask(self, value):
        if not isinstance(value, str):
            return value
        value = self.QUERY_SECRET.sub(r"\1***", value)
        return self.AUTHORIZATION.sub(r"\1***", value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._mask(arg) for arg in record.args)
        return True   # keep the line, just masked


# ─── Hook the dashboard into the main app ───

def install(app: FastAPI) -> None:
    """Add the dashboard routes and the /dashboard/static files to `app`."""
    STATIC_DIR.mkdir(exist_ok=True)
    app.include_router(router)
    app.mount("/dashboard/static", StaticFiles(directory=str(STATIC_DIR)), name="dashboard_static")

    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, MaskKeyFilter) for f in access_log.filters):
        access_log.addFilter(MaskKeyFilter())

    scenario = demo_scenario()
    if scenario:
        print(f"[dashboard] DEMO MODE ({scenario}): showing made-up data, no calls to HEVY or Garmin.")
    if not os.getenv("DASHBOARD_TOKEN"):
        print(
            "[dashboard] WARNING: DASHBOARD_TOKEN is not set. /dashboard only answers direct requests "
            "from this machine or the home network (not via ngrok). Set DASHBOARD_TOKEN in .hevy_env "
            "to use it from anywhere."
        )


# ─── Small HTML pages (login, errors) ───

_PAGE = """<!doctype html>
<html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="robots" content="noindex">
<title>FitCoach</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: #FAF7F2; color: #2B2724;
         font-family: "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .card {{ background: #fff; border-radius: 24px; padding: 32px; width: 100%; max-width: 420px;
          box-shadow: 0 12px 40px rgba(60, 40, 20, .08); border: 1px solid #EFE9E1; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  p {{ color: #5E5750; line-height: 1.5; margin: 8px 0 0; }}
  a {{ color: #C2410C; }}
  code {{ background: #F5F0E8; padding: 2px 6px; border-radius: 6px; word-break: break-all; }}
  label {{ display: block; font-size: 13px; color: #5E5750; margin: 20px 0 6px; }}
  input {{ width: 100%; font: inherit; padding: 12px 14px; border-radius: 14px;
          border: 1px solid #E6DED3; background: #FFFDFA; color: inherit; }}
  input:focus {{ outline: 2px solid #E8553A; outline-offset: 1px; }}
  button {{ margin-top: 16px; width: 100%; font: inherit; font-weight: 600; color: #fff;
           background: #E8553A; border: 0; border-radius: 14px; padding: 12px 16px; cursor: pointer; }}
  button:hover {{ background: #D94A30; }}
  .error {{ color: #B42318; font-weight: 600; }}
</style></head>
<body><main class="card">{body}</main></body></html>
"""

_LOGIN_BODY = """
<h1>Velkommen til FitCoach</h1>
<p>Skriv inn nøkkelen din for å åpne dashboardet. Du blir husket på denne enheten i 90 dager.</p>
{error}
<form method="post" action="/dashboard/login">
  <label for="token">Nøkkel</label>
  <input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
  <button type="submit">Logg inn</button>
</form>
<p>Nøkkelen er verdien av <code>DASHBOARD_TOKEN</code> i <code>.hevy_env</code> på Pi-en.</p>
"""


def _login_html(error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{error}</p>' if error else ""
    return _PAGE.format(body=_LOGIN_BODY.format(error=error_html))


WRONG_HOST_TEXT = (
    "Dashboardet svarer bare på localhost, en IP-adresse, navn som slutter på .local "
    "eller navn i DASHBOARD_ALLOWED_HOSTS i .hevy_env."
)

WRONG_HOST_HTML = _PAGE.format(body="""
<h1>Ukjent adresse</h1>
<p>Dashboardet ble åpnet med et navn det ikke kjenner igjen. Det er en beskyttelse mot
nettsider som prøver å lure nettleseren til å lese dashboardet ditt.</p>
<p>Bruk <code>http://localhost:8000/dashboard</code> eller Pi-ens IP-adresse. Vil du bruke et eget navn,
for eksempel <code>pi.hjemme</code>, legger du det til i <code>.hevy_env</code>:<br>
<code>DASHBOARD_ALLOWED_HOSTS=pi.hjemme</code></p>
""")

NO_TOKEN_HTML = _PAGE.format(body="""
<h1>Dashboardet er låst</h1>
<p>Uten nøkkel svarer dashboardet bare på maskinen selv og hjemmenettet, ikke via ngrok.</p>
<p>Slik åpner du det fra hvor som helst:</p>
<p>1. Legg til en lang, tilfeldig nøkkel i <code>.hevy_env</code> på Pi-en:<br>
<code>DASHBOARD_TOKEN=din-lange-nøkkel</code></p>
<p>2. Start proxyen på nytt og logg inn på <code>/dashboard/login</code>.</p>
""")

FALLBACK_HTML = _PAGE.format(body="""
<h1>Dashboardet er nesten klart</h1>
<p>Siden (<code>dashboard/templates/dashboard.html</code>) mangler ennå,
men dataene fungerer: <a href="/dashboard/api">se dem som JSON</a>.</p>
""")

ERROR_HTML = _PAGE.format(body="""
<h1>Oi, noe gikk galt</h1>
<p>Dashboardet kunne ikke bygges akkurat nå. Prøv å laste siden på nytt om litt.
Detaljer står i loggen på Pi-en.</p>
""")
