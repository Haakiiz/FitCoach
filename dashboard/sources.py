"""Data sources for the dashboard.

Everything that talks to the outside world lives here:
- HEVY: all workouts + which muscle group each exercise template trains
- Garmin Connect: sleep, body battery, steps, heart rate, HRV and runs
- weights.json: the weigh-ins you type into the dashboard

Each source keeps a small cache in memory, so reloading the page does not
hammer the APIs. Pass `force=True` to skip the cache.
"""
import asyncio
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

# "uvicorn.error" is uvicorn's normal log, so these messages show up in the
# terminal / journalctl next to uvicorn's own lines (it is not only for errors).
log = logging.getLogger("uvicorn.error")

# The project folder (the folder that contains hevy_proxy.py)
PROJECT_DIR = Path(__file__).resolve().parent.parent

HEVY_API_URL = "https://api.hevyapp.com/v1"


# ─── Small helpers ───

def _write_json_atomic(path: Path, data) -> None:
    """Write JSON to `path` safely.

    We first write to a temporary file and then rename it over the real file.
    A rename is "atomic": if the Pi loses power halfway, you get either the old
    file or the new one, never a half-written file. flush + fsync make sure the
    new content is really on the SD card before the rename.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _number(value, digits: int = 0):
    """Return `value` rounded (int when digits=0), or None if it is not a real number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except OverflowError:   # an int too big to be a float, e.g. 10**400
        return None
    if digits == 0:
        return int(round(value))
    return round(float(value), digits)


def _first(*values):
    """Return the first value that is not None (Garmin often has the same number in several places)."""
    for value in values:
        if value is not None:
            return value
    return None


# ─── HEVY: workouts ───

WORKOUTS_TTL_SECONDS = 10 * 60   # keep workouts for 10 minutes
MAX_WORKOUT_PAGES = 40           # safety net: 40 pages × 10 workouts = 400 workouts

_workouts_cache = {"fetched_at": 0.0, "workouts": None}
_workouts_lock = asyncio.Lock()


async def fetch_all_workouts(api_key: str, force: bool = False) -> list[dict]:
    """Fetch ALL workouts from HEVY (page by page), newest first.

    HEVY returns at most 10 workouts per page, so we keep asking for the next
    page until we reach `page_count`. The result is cached for 10 minutes.
    """
    if not api_key:
        raise RuntimeError("HEVY_API_KEY mangler")

    async with _workouts_lock:  # two page loads at once should only fetch once
        age = time.monotonic() - _workouts_cache["fetched_at"]
        if not force and _workouts_cache["workouts"] is not None and age < WORKOUTS_TTL_SECONDS:
            return _workouts_cache["workouts"]

        workouts: list[dict] = []
        page = 1
        async with httpx.AsyncClient(
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=15,
        ) as client:
            while page <= MAX_WORKOUT_PAGES:
                resp = await client.get(
                    f"{HEVY_API_URL}/workouts",
                    params={"page": page, "pageSize": 10},
                )
                if resp.status_code == 404:
                    break  # asked past the last page
                resp.raise_for_status()
                body = resp.json()
                chunk = body.get("workouts", [])
                workouts.extend(chunk)

                page_count = body.get("page_count") or 1
                if not chunk or page >= page_count:
                    break
                page += 1

        if page > MAX_WORKOUT_PAGES:
            log.warning(
                "[dashboard] stopped after %d pages of HEVY workouts (MAX_WORKOUT_PAGES); "
                "older workouts are not shown", MAX_WORKOUT_PAGES,
            )

        _workouts_cache["workouts"] = workouts
        _workouts_cache["fetched_at"] = time.monotonic()
        return workouts


# ─── HEVY: exercise template → muscle group ───

MUSCLES_CACHE_PATH = PROJECT_DIR / "exercise_muscles_cache.json"
MUSCLES_MAX_AGE_SECONDS = 7 * 24 * 3600   # refresh the file once a week
MAX_TEMPLATE_PAGES = 50                   # safety net: 50 pages × 100 templates

_muscles_memory: dict[str, str] = {}
_muscles_lock = asyncio.Lock()


async def fetch_template_muscles(api_key: str) -> dict[str, str]:
    """Return {exercise_template_id: primary_muscle_group}, e.g. {"D04AC939": "quadriceps"}.

    The mapping hardly ever changes, so it is saved to exercise_muscles_cache.json
    and only downloaded again when that file is older than 7 days.
    """
    async with _muscles_lock:   # two page loads at once should only download once
        return await _fetch_template_muscles(api_key)


async def _fetch_template_muscles(api_key: str) -> dict[str, str]:
    # 1) Fresh cache file on disk? Use it.
    if MUSCLES_CACHE_PATH.exists():
        age = time.time() - MUSCLES_CACHE_PATH.stat().st_mtime
        if age < MUSCLES_MAX_AGE_SECONDS:
            if not _muscles_memory:
                try:
                    _muscles_memory.update(json.loads(MUSCLES_CACHE_PATH.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    log.warning("[dashboard] could not read %s, downloading again", MUSCLES_CACHE_PATH.name)
            if _muscles_memory:
                return dict(_muscles_memory)

    # 2) Otherwise download all templates (same paging style as hevy_proxy.py)
    if not api_key:
        raise RuntimeError("HEVY_API_KEY mangler")

    muscles: dict[str, str] = {}
    page = 1
    try:
        async with httpx.AsyncClient(headers={"api-key": api_key}, timeout=15) as client:
            while page <= MAX_TEMPLATE_PAGES:
                resp = await client.get(
                    f"{HEVY_API_URL}/exercise_templates",
                    params={"page": page, "page_size": 100},
                )
                if resp.status_code == 404:
                    break  # no more pages
                resp.raise_for_status()
                chunk = resp.json().get("exercise_templates", [])
                if not chunk:
                    break
                for tpl in chunk:
                    if tpl.get("id") and tpl.get("primary_muscle_group"):
                        muscles[tpl["id"]] = tpl["primary_muscle_group"]
                page += 1
        if page > MAX_TEMPLATE_PAGES:
            log.warning("[dashboard] stopped after %d pages of exercise templates (MAX_TEMPLATE_PAGES)",
                        MAX_TEMPLATE_PAGES)
    except Exception:
        # Download failed: an old cache file is better than nothing
        if MUSCLES_CACHE_PATH.exists():
            log.warning("[dashboard] template download failed, using the old muscle cache")
            return json.loads(MUSCLES_CACHE_PATH.read_text(encoding="utf-8"))
        raise

    _write_json_atomic(MUSCLES_CACHE_PATH, muscles)
    _muscles_memory.clear()
    _muscles_memory.update(muscles)
    log.info("[dashboard] cached %d exercise muscle groups", len(muscles))
    return dict(muscles)


# ─── Garmin Connect ───

GARMIN_TTL_SECONDS = 30 * 60        # keep Garmin data for 30 minutes
GARMIN_ERROR_TTL_SECONDS = 20 * 60  # after a failure, wait 20 minutes before trying again
GARMIN_TIMEOUT_SECONDS = 45         # the page stops waiting for Garmin after 45 seconds
GARMIN_WORKERS = 4                  # how many days are fetched at the same time
PASSWORD_RETRY_SECONDS = 10 * 60    # "Oppdater" may allow a new password login once per 10 min

_garmin_cache = {"fetched_at": 0.0, "key": None, "days": None,
                 "error": None, "error_at": 0.0, "error_is_login": False}
_garmin_lock = asyncio.Lock()
_garmin_client = None               # the logged-in Garmin client, reused between fetches

# The one background job that talks to Garmin (None when nothing is running).
# There is never more than one: while it runs, nobody starts another.
_garmin_task = None
_garmin_task_key = None

# Bumped every time a failed job has been handled. A job only saves its
# logged-in client if the number has not changed since the job started.
_garmin_generation = 0

# After a failed e-mail/password login we do NOT try the password again by
# ourselves: repeated attempts can trigger MFA e-mails, "429 Too Many Requests"
# or even lock the account. "Oppdater" (force=True) can unlock it, but at most
# once per 10 minutes. Restarting the proxy also unlocks it. Saved tokens are
# still tried every time.
_password_login_blocked = False
_password_unblocked_at = float("-inf")


class GarminError(Exception):
    """Something went wrong with Garmin. The message is Norwegian and is shown on the page."""


class GarminLoginError(GarminError):
    """Raised when we cannot log in to Garmin Connect."""


def garmin_configured() -> bool:
    """True when GARMIN_EMAIL and GARMIN_PASSWORD are set (e.g. in .hevy_env)."""
    return bool(os.getenv("GARMIN_EMAIL") and os.getenv("GARMIN_PASSWORD"))


def garmin_tokenstore() -> str:
    """Folder where Garmin login tokens are saved (default ~/.garminconnect)."""
    return str(Path(os.getenv("GARMIN_TOKENSTORE") or "~/.garminconnect").expanduser())


def _garmin_login():
    """Log in to Garmin and return a ready-to-use client.

    1. Try the saved tokens first (no password needed, no MFA).
    2. If that fails, log in with e-mail + password and save new tokens.
    The password is never printed or logged.
    """
    global _password_login_blocked
    try:
        from garminconnect import Garmin  # imported here so the app starts even without it
    except ImportError as err:
        raise GarminError("Pakken garminconnect er ikke installert. Kjør pip install -r requirements.txt.") from err

    tokenstore = garmin_tokenstore()

    # 1) Saved tokens
    try:
        client = Garmin()
        client.login(tokenstore)
        return client
    except Exception as err:
        log.info("[garmin] saved tokens not usable (%s), logging in with e-mail/password", type(err).__name__)

    # 2) E-mail + password (only if the last password login did not fail)
    if _password_login_blocked:
        raise GarminLoginError(
            "Innloggingen mot Garmin feilet tidligere, så passordet prøves ikke på nytt av seg selv "
            "(for å unngå MFA-e-poster og kontolås). Trykk «Oppdater» eller start proxyen på nytt."
        )
    try:
        client = Garmin(os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD"))
        client.login()
    except Exception as err:
        _password_login_blocked = True
        text = str(err)
        if "MFA" in text:
            raise GarminLoginError(
                "Garmin ber om en MFA-kode. Kjør `python -m dashboard.sources` én gang i terminalen "
                "for å logge inn og lagre tokens."
            ) from err
        raise GarminLoginError(
            f"Innlogging mot Garmin feilet ({type(err).__name__}). "
            "Sjekk GARMIN_EMAIL og GARMIN_PASSWORD i .hevy_env."
        ) from err

    try:
        client.client.dump(tokenstore)   # save tokens so next time step 1 works
    except Exception as err:
        log.warning("[garmin] could not save tokens to %s: %s", tokenstore, type(err).__name__)
    return client


def _safe(metric: str, func, *args):
    """Call func(*args). If it fails, log it and return None, so one broken
    metric (e.g. no HRV data) does not break the whole Garmin card."""
    try:
        return func(*args)
    except Exception as err:
        log.warning("[garmin] could not fetch %s: %s", metric, type(err).__name__)
        return None


def empty_garmin_day(day: str) -> dict:
    """A normalised Garmin day with every key present and no data yet (see SCHEMA.md)."""
    return {
        "date": day,
        "sleep_seconds": None, "deep_seconds": None, "rem_seconds": None,
        "light_seconds": None, "awake_seconds": None,
        "body_battery_peak": None, "body_battery_low": None,
        "resting_hr": None, "hrv_ms": None, "spo2_avg": None, "respiration_avg": None,
        "steps": None, "calories": None,
        "runs": [],
    }


def _body_battery_levels(entry: dict) -> list[int]:
    """Pull the body battery levels (0–100) out of one day from get_body_battery().

    Garmin sends `bodyBatteryValuesArray` as rows like [timestamp, level] (or with
    more columns). The descriptor list tells us which column holds the level.
    """
    rows = entry.get("bodyBatteryValuesArray") or []
    level_index = None
    for desc in entry.get("bodyBatteryValueDescriptorDTOList") or []:
        if "level" in str(desc.get("bodyBatteryValueDescriptorKey", "")).lower():
            level_index = desc.get("bodyBatteryValueDescriptorIndex")
    levels = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        value = row[level_index] if level_index is not None and level_index < len(row) else row[-1]
        if _number(value) is not None and 0 <= value <= 100:
            levels.append(int(value))
    return levels


def _fill_day(day: dict, sleep, stats, hrv, spo2, resp, bb_entry) -> dict:
    """Copy the numbers we care about from Garmin's raw answers into `day`."""
    sleep = sleep or {}
    dto = sleep.get("dailySleepDTO") or {}
    stats = stats or {}
    hrv = hrv or {}
    spo2 = spo2 or {}
    resp = resp or {}

    # Sleep
    day["sleep_seconds"] = _number(dto.get("sleepTimeSeconds"))
    day["deep_seconds"] = _number(dto.get("deepSleepSeconds"))
    day["rem_seconds"] = _number(dto.get("remSleepSeconds"))
    day["light_seconds"] = _number(dto.get("lightSleepSeconds"))
    day["awake_seconds"] = _number(dto.get("awakeSleepSeconds"))

    # Body battery: the daily report first, the daily summary as backup
    levels = _body_battery_levels(bb_entry) if bb_entry else []
    if levels:
        day["body_battery_peak"] = max(levels)
        day["body_battery_low"] = min(levels)
    else:
        day["body_battery_peak"] = _number(stats.get("bodyBatteryHighestValue"))
        day["body_battery_low"] = _number(stats.get("bodyBatteryLowestValue"))

    # Heart
    day["resting_hr"] = _first(_number(stats.get("restingHeartRate")), _number(sleep.get("restingHeartRate")))
    hrv_summary = hrv.get("hrvSummary") or {}
    day["hrv_ms"] = _first(_number(hrv_summary.get("lastNightAvg")), _number(sleep.get("avgOvernightHrv")))

    # Blood oxygen and breathing
    day["spo2_avg"] = _first(
        _number(spo2.get("averageSpO2")),
        _number(spo2.get("avgSleepSpO2")),
        _number(stats.get("averageSpo2")),
        _number(dto.get("averageSpO2Value")),
    )
    day["respiration_avg"] = _first(
        _number(resp.get("avgSleepRespirationValue"), 1),
        _number(resp.get("avgWakingRespirationValue"), 1),
        _number(dto.get("averageRespirationValue"), 1),
        _number(stats.get("avgWakingRespirationValue"), 1),
    )

    # Activity
    day["steps"] = _number(stats.get("totalSteps"))
    day["calories"] = _number(stats.get("totalKilocalories"))
    return day


def _runs_by_date(activities) -> dict[str, list[dict]]:
    """Group running activities by date: {"2026-09-20": [{"km": 5.1, "minutes": 31.5}]}."""
    runs: dict[str, list[dict]] = {}
    for act in activities or []:
        type_key = str((act.get("activityType") or {}).get("typeKey", ""))
        if "running" not in type_key:   # running, treadmill_running, trail_running …
            continue
        started = str(act.get("startTimeLocal") or "")[:10]
        meters = _number(act.get("distance"), 1)
        seconds = _number(act.get("duration"), 1)
        if not started or not meters:
            continue
        runs.setdefault(started, []).append({
            "km": round(meters / 1000, 2),
            "minutes": round(seconds / 60, 1) if seconds else None,
        })
    return runs


def _fetch_one_day(client, ds: str, bb_entry, runs: list) -> dict:
    """Fetch and normalise one Garmin day (5 small API calls)."""
    day = empty_garmin_day(ds)
    _fill_day(
        day,
        sleep=_safe("sleep", client.get_sleep_data, ds),
        stats=_safe("stats", client.get_stats, ds),
        hrv=_safe("hrv", client.get_hrv_data, ds),
        spo2=_safe("spo2", client.get_spo2_data, ds),
        resp=_safe("respiration", client.get_respiration_data, ds),
        bb_entry=bb_entry,
    )
    day["runs"] = runs
    return day


def _fetch_garmin_days_sync(days: int, today: date) -> list[dict]:
    """The slow, blocking part of fetch_garmin_days (runs in a background thread)."""
    global _garmin_client
    generation = _garmin_generation
    client = _garmin_client
    if client is None:
        client = _garmin_login()
        if generation == _garmin_generation:   # nobody has given up on this job meanwhile
            _garmin_client = client

    first = today - timedelta(days=days - 1)
    first_s, today_s = first.isoformat(), today.isoformat()
    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]

    # Two range calls cover all days at once. They run first and alone, so an
    # expiring login token is refreshed once before the parallel calls start.
    bb_list = _safe("body battery", client.get_body_battery, first_s, today_s) or []
    bb_by_date = {e.get("date"): e for e in bb_list if isinstance(e, dict)}
    runs = _runs_by_date(_safe("activities", client.get_activities_by_date, first_s, today_s))

    # The days themselves: 4 at a time instead of one after another (~35 calls)
    with ThreadPoolExecutor(max_workers=GARMIN_WORKERS) as pool:
        result = list(pool.map(
            lambda ds: _fetch_one_day(client, ds, bb_by_date.get(ds), runs.get(ds, [])),
            dates,
        ))

    # Not a single number came back? Then the login has probably expired.
    # Raising here makes fetch_garmin_days log in again next time.
    if not any(value not in (None, []) for d in result for key, value in d.items() if key != "date"):
        raise RuntimeError("Garmin returned no data at all")
    return result   # oldest first, newest last


def _garmin_error_message(err: BaseException) -> str:
    """A Norwegian sentence for the page explaining what went wrong."""
    if isinstance(err, GarminError):
        return str(err)
    return f"Klarte ikke å hente data fra Garmin ({type(err).__name__}). Vi prøver igjen om litt."


def _collect_finished_garmin_task() -> None:
    """If the background job has finished, move its result (or error) into the cache."""
    global _garmin_task, _garmin_client, _garmin_generation
    task = _garmin_task
    if task is None or not task.done():
        return
    _garmin_task = None

    error = asyncio.CancelledError() if task.cancelled() else task.exception()
    if error is None:
        _garmin_cache.update(fetched_at=time.monotonic(), key=_garmin_task_key, days=task.result(),
                             error=None, error_at=0.0, error_is_login=False)
        return

    _garmin_generation += 1
    _garmin_client = None   # log in again next time
    log.warning("[garmin] fetch failed: %s", type(error).__name__)
    _garmin_cache.update(error=_garmin_error_message(error), error_at=time.monotonic(),
                         error_is_login=isinstance(error, GarminLoginError))


def _raise_cached_garmin_error():
    error_class = GarminLoginError if _garmin_cache["error_is_login"] else GarminError
    raise error_class(_garmin_cache["error"])


async def fetch_garmin_days(days: int = 7, force: bool = False, today: date | None = None) -> list[dict] | None:
    """Fetch the last `days` days from Garmin, normalised as in SCHEMA.md (newest last).

    - Returns None when Garmin is not configured.
    - Good data is cached for 30 minutes.
    - A failure raises GarminError (Norwegian message) and is remembered for
      20 minutes, so a broken Garmin does not slow down every page load.
    - Only ONE background job talks to Garmin at a time. If it takes longer
      than 45 s, the page gets a GarminError, the job keeps going, and a later
      call picks up its result. No new job starts while one is running.
    - force=True (the "Oppdater" button) skips the caches and may allow a new password
      login (at most once per 10 minutes).
    """
    global _garmin_task, _garmin_task_key, _password_login_blocked, _password_unblocked_at
    if not garmin_configured():
        return None

    today = today or date.today()
    key = (days, today.isoformat())

    async with _garmin_lock:
        _collect_finished_garmin_task()
        if _garmin_task is not None:
            raise GarminError("Garmin holder fortsatt på med forrige henting. Last siden på nytt om litt.")

        now = time.monotonic()
        fresh = _garmin_cache["key"] == key and now - _garmin_cache["fetched_at"] < GARMIN_TTL_SECONDS
        if not force:
            if fresh:
                return _garmin_cache["days"]
            if _garmin_cache["error"] and now - _garmin_cache["error_at"] < GARMIN_ERROR_TTL_SECONDS:
                _raise_cached_garmin_error()
        elif _password_login_blocked and now - _password_unblocked_at >= PASSWORD_RETRY_SECONDS:
            _password_login_blocked = False
            _password_unblocked_at = now

        # Start the background job. garminconnect is not async, so it runs in a thread.
        _garmin_task = asyncio.ensure_future(asyncio.to_thread(_fetch_garmin_days_sync, days, today))
        _garmin_task_key = key
        try:
            # shield(): when we stop waiting, the job itself is NOT cancelled
            await asyncio.wait_for(asyncio.shield(_garmin_task), timeout=GARMIN_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError):
            raise GarminError(
                f"Garmin svarte ikke innen {GARMIN_TIMEOUT_SECONDS} sekunder. "
                "Hentingen fortsetter i bakgrunnen, så last siden på nytt om litt."
            )
        except Exception:
            pass   # the error is handled just below

        _collect_finished_garmin_task()
        if _garmin_cache["key"] == key and _garmin_cache["error"] is None:
            return _garmin_cache["days"]
        _raise_cached_garmin_error()


# ─── Weigh-ins (weights.json) ───

MIN_KG, MAX_KG = 30.0, 250.0
MIN_DATE = date(2000, 1, 1)

# Typed weights: 2–3 digits, optionally a comma or dot and 1–2 decimals ("96", "96,4", "96.45").
# re.ASCII makes \d mean only 0–9 (not Arabic or other digits); "_" and "1e3" don't match.
KG_TEXT = re.compile(r"^\d{2,3}([.,]\d{1,2})?$", re.ASCII)
DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)


def weights_path() -> Path:
    """Where weigh-ins are stored: env WEIGHTS_PATH, default weights.json in the project folder."""
    return Path(os.getenv("WEIGHTS_PATH") or PROJECT_DIR / "weights.json")


def load_weights(path) -> list[dict]:
    """Read weigh-ins from `path`: [{"date": "YYYY-MM-DD", "kg": 96.4}], oldest first.
    A missing file simply means "no weigh-ins yet"."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise RuntimeError(f"Vektfila {path.name} er ødelagt og kunne ikke leses.") from err
    entries = [
        {"date": str(e["date"]), "kg": float(e["kg"])}
        for e in data
        if isinstance(e, dict) and "date" in e and _number(e.get("kg"), 2) is not None
    ]
    return sorted(entries, key=lambda e: e["date"])


def validate_weight(kg, day=None, today: date | None = None) -> tuple[float, str]:
    """Check a weigh-in and return (kg, "YYYY-MM-DD").

    Raises ValueError with a Norwegian message the page can show directly.
    """
    today = today or date.today()

    # Accept "96,4" as well as 96.4 (Norwegian keyboards use a comma)
    if isinstance(kg, str):
        text = kg.strip()
        if not KG_TEXT.match(text):
            raise ValueError("Vekten må være et tall, for eksempel 96,4.")
        kg = float(text.replace(",", "."))
    kg = _number(kg, 2)
    if kg is None:
        raise ValueError("Vekten må være et tall, for eksempel 96,4.")
    if not MIN_KG <= kg <= MAX_KG:
        raise ValueError(f"Vekten må være mellom {MIN_KG:.0f} og {MAX_KG:.0f} kg.")

    if day in (None, ""):
        day = today
    elif isinstance(day, str):
        try:
            if not DATE_TEXT.match(day.strip()):
                raise ValueError
            day = date.fromisoformat(day.strip())
        except ValueError:
            raise ValueError("Datoen må ha formatet ÅÅÅÅ-MM-DD.")
    elif isinstance(day, datetime):
        day = day.date()
    elif not isinstance(day, date):
        raise ValueError("Datoen må ha formatet ÅÅÅÅ-MM-DD.")
    if day > today:
        raise ValueError("Datoen kan ikke være i fremtiden.")
    if day < MIN_DATE:
        raise ValueError("Datoen må være år 2000 eller senere.")
    return kg, day.isoformat()


def upsert_weight(entries: list[dict], kg: float, day: str) -> list[dict]:
    """Return a new sorted list where `day` has weight `kg` (same date is replaced)."""
    kept = [e for e in entries if e["date"] != day]
    kept.append({"date": day, "kg": kg})
    return sorted(kept, key=lambda e: e["date"])


def add_weight(path, kg, day=None, today: date | None = None) -> list[dict]:
    """Save a weigh-in to `path` and return all weigh-ins (oldest first).

    - kg must be between 30 and 250
    - the date defaults to today and cannot be in the future
    - a second weigh-in on the same date replaces the first one
    """
    kg, day_s = validate_weight(kg, day, today)
    entries = upsert_weight(load_weights(path), kg, day_s)
    _write_json_atomic(Path(path), entries)
    return entries


# ─── One-time Garmin login from the terminal ───
# Run `python -m dashboard.sources` on the Pi once. It asks for the MFA code if
# Garmin wants one and saves tokens to ~/.garminconnect, so the dashboard can
# log in by itself afterwards.

if __name__ == "__main__":
    import getpass

    from dotenv import load_dotenv
    from garminconnect import Garmin

    load_dotenv(dotenv_path=PROJECT_DIR / ".hevy_env")
    email = os.getenv("GARMIN_EMAIL") or input("Garmin e-post: ")
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin passord: ")
    garmin = Garmin(email, password, prompt_mfa=lambda: input("MFA-kode fra Garmin: "))
    garmin.login()
    garmin.client.dump(garmin_tokenstore())
    print(f"Innlogget. Tokens er lagret i {garmin_tokenstore()}")
