"""Small shared helpers: safe numbers, Norwegian formatting, dates and HEVY sets.

Used by analytics.py, progression.py and recommend.py. Everything here is tiny and
pure: no network, no clock reads (except the time zone definition).
"""

from datetime import date, datetime, timedelta, timezone

from . import exercise_info as ex

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Oslo")          # same zone as routes.py uses for `today`
except Exception:                               # no tz database installed: fall back to UTC
    LOCAL_TZ = timezone.utc

MONTHS_NO = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"]

# Plausible ranges for logged set values. Anything outside is treated as a typo and ignored.
MAX_SET_KG = 500
MAX_SET_REPS = 100
MAX_SET_SECONDS = 4 * 3600
MAX_SET_METERS = 200_000

# Definite form for sentences ("Skuldrene har ikke blitt trent …").
GROUP_DEFINITE = {"bryst": "brystet", "rygg": "ryggen", "skuldre": "skuldrene",
                  "armer": "armene", "bein": "beina", "kjerne": "kjernemuskulaturen"}


# ─── Numbers ───

def num(value):
    """Safe float: numbers and numeric strings -> float; None/bool/text/NaN/inf -> None."""
    return ex.to_number(value)


def in_range(value, low, high):
    """The number if it lies within [low, high], otherwise None (plausibility check)."""
    value = num(value)
    return value if value is not None and low <= value <= high else None


def round_or_none(value, digits=1):
    value = num(value)
    return None if value is None else round(value, digits)


def as_list(value):
    """Lists stay lists; anything else (None, dict, text) becomes an empty list."""
    return value if isinstance(value, list) else []


# ─── Norwegian text ───

def fmt_kg(kg):
    """Norwegian weight text without the unit: 22.0 -> '22', 67.5 -> '67,5'."""
    kg = num(kg)
    if kg is None:
        return "–"
    if abs(kg - round(kg)) < 1e-9:
        return str(int(round(kg)))
    return f"{kg:.1f}".replace(".", ",")


def fmt_num(value, digits=1):
    """Norwegian decimal comma: 6.25 -> '6,3'."""
    value = num(value)
    if value is None:
        return "–"
    if digits == 0 or abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.{digits}f}".replace(".", ",")


def fmt_date_no(day):
    """date(2026, 6, 17) -> '17. juni'."""
    return f"{day.day}. {MONTHS_NO[day.month - 1]}"


def fmt_pace(minutes, km):
    """Pace as 'm:ss' per km, e.g. 28 min over 4 km -> '7:00'. None when unknown."""
    minutes, km = num(minutes), num(km)
    if not minutes or not km or km <= 0:
        return None
    total_seconds = int(round(minutes * 60 / km))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def plural(n, singular, plural_form):
    """Number + noun with the right form: plural(1, 'økt', 'økter') -> '1 økt', 2 -> '2 økter'."""
    return f"{n} {singular if n == 1 else plural_form}"


def days_text(n):
    """1 -> '1 dag', 5 -> '5 dager'."""
    return plural(n, "dag", "dager")


def reps_text(n):
    """1 -> '1 repetisjon', 12 -> '12 repetisjoner'."""
    return plural(n, "repetisjon", "repetisjoner")


def neglect_sentence(group_name, days_since):
    """'Skuldrene har ikke blitt trent på 21 dager.' (or '… ennå.' when never trained)."""
    name = GROUP_DEFINITE.get(group_name, group_name).capitalize()
    when = "ennå" if days_since is None else f"på {days_text(days_since)}"
    return f"{name} har ikke blitt trent {when}."


# ─── Dates ───

def parse_date(value):
    """'2026-09-20' (or a date/datetime) -> date, else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_datetime(value, tz):
    """HEVY ISO timestamp -> local date in time zone `tz`, or None.

    HEVY sends UTC ("+00:00"). A timestamp without any offset is treated as UTC too.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return parse_date(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if tz is not None:
        moment = moment.astimezone(tz)
    return moment.date()


def monday(day):
    """The Monday of the ISO week that `day` belongs to."""
    return day - timedelta(days=day.weekday())


# ─── HEVY sets ───

def is_work_set(s):
    """True for real work sets (normal, failure, dropset). Warm-up sets do not count."""
    return isinstance(s, dict) and (s.get("type") or "normal") != "warmup"


def set_weight(s):
    """Weight of a set in kg, or None for no load (0/None) and implausible values."""
    w = num(s.get("weight_kg"))
    return w if w and 0 < w <= MAX_SET_KG else None


def set_reps(s):
    r = num(s.get("reps"))
    return int(r) if r and 1 <= r <= MAX_SET_REPS else None


def set_seconds(s):
    d = num(s.get("duration_seconds"))
    return d if d and 0 < d <= MAX_SET_SECONDS else None


def set_meters(s):
    m = num(s.get("distance_meters"))
    return m if m and 0 < m <= MAX_SET_METERS else None


def is_meaningful_set(s):
    """A work set that actually contains something: weight, reps, time or distance above 0."""
    return is_work_set(s) and bool(set_weight(s) or set_reps(s) or set_seconds(s) or set_meters(s))


def epley(weight_kg, reps):
    """Estimated one-rep max with Epley's formula: weight × (1 + reps / 30).

    Only valid for 1–15 reps; above that the estimate gets unreliable because the set
    becomes an endurance test rather than a strength test. Returns None when not usable.
    Used for the strength charts and PRs only – never to plan the next session.
    """
    weight_kg, reps = num(weight_kg), num(reps)
    if weight_kg is None or reps is None or weight_kg <= 0:
        return None
    if reps < 1 or reps > 15:
        return None
    return round(weight_kg * (1 + reps / 30.0), 1)
