"""Turn raw training, recovery and weight data into the numbers on the dashboard.

The one public entry point is `build_dashboard(...)`. It takes plain Python data
(HEVY workouts, Garmin days, weigh-ins) and returns ONE JSON-serialisable dict with
exactly the shape described in `dashboard/SCHEMA.md`.

Everything here is pure and deterministic: no network, no clock reads, no randomness.
`today` and `now` are passed in, so the same input always gives the same output
(which also makes it easy to test).

The coaching rules are simple and based on well-established training science:

- **e1RM (Epley):** estimated one-rep max = weight × (1 + reps / 30). Lets us compare
  "90 kg × 6" with "80 kg × 10" on one scale.
- **Double progression:** keep the weight until every work set reaches the top of the
  rep range (12), then go to the next available weight and start lower in the range.
- **Alternation:** strength and cardio take turns, so each muscle gets ~48 hours to repair
  while you still train every day.
- **Comeback deload:** after ≥ 14 days off we start at 70–80 % of the old weights, because
  nerves, tendons and connective tissue lose tolerance faster than muscle is lost.

All user-facing text is Norwegian (bokmål).
"""

from datetime import date, datetime, time, timedelta, timezone

from . import exercise_info as ex
from .quotes import quote_of_day

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Oslo")          # same zone as routes.py uses for `today`
except Exception:                               # no tz database installed: fall back to UTC
    LOCAL_TZ = timezone.utc

# ─── Profile and constants ───
START_KG = 102.0
GOAL_KG = 90.0
HEIGHT_M = 1.85
SLEEP_GOAL_HOURS = 7.5

TOP_REPS = 12                # top of the rep range for double progression
BOTTOM_REPS = 8              # where you restart after a weight increase
MAX_SETS = 5
COMEBACK_DAYS = 14
NEGLECTED_DAYS = 7
STALE_WEIGHT_DAYS = 14
STALE_RECOVERY_DAYS = 1      # Garmin data older than yesterday is ignored by the coach
MAX_TREND_KG_PER_WEEK = 1.5
BIG_JUMP_PCT = 35            # a jump larger than this (10 -> 16 kg) is split up with more reps first

MONTHS_NO = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"]

# Movement patterns for a full-body session, in the order they are done.
# Only HEVY titles verbatim. The first exercise the user has actually trained is preferred.
PATTERNS = [
    ("knebøy", ["Goblet Squat", "Squat (Barbell)", "Walking Lunge"]),
    ("hofte", ["Romanian Deadlift (Barbell)", "Romanian Deadlift (Dumbbell)"]),
    ("press", ["Bench Press (Dumbbell)", "Floor Press (Dumbbell)", "Bench Press (Barbell)", "Push Up"]),
    ("trekk", ["Dumbbell Row", "Bent Over Row (Barbell)", "Inverted Row"]),
    ("kjerne", ["Hanging Knee Raise"]),
    ("legg", ["Single Leg Standing Calf Raise"]),
]

# Extra exercise added when a display group has been neglected for a while.
NEGLECT_BOOST = {
    "skuldre": "Shoulder Press (Dumbbell)",
    "bryst": "Incline Chest Fly (Dumbbell)",
    "armer": "Dead Hang",
    "rygg": "Inverted Row",
}

# Definite form for sentences ("Skuldrene har ikke blitt trent …").
GROUP_DEFINITE = {"bryst": "brystet", "rygg": "ryggen", "skuldre": "skuldrene",
                  "armer": "armene", "bein": "beina", "kjerne": "kjernemuskulaturen"}

# Short Norwegian hint for each neglected group.
NEGLECT_HINTS = {
    "bryst": "Legg inn gulvpress eller armhevinger i neste styrkeøkt.",
    "rygg": "Legg inn roing – trekkøvelser balanserer alt pressarbeidet og gir bedre holdning.",
    "skuldre": "Legg inn skulderpress – skuldrene får lite direkte arbeid av benkpress alene.",
    "armer": "Roing og press trener armene indirekte, og heng i stang bygger grepet.",
    "bein": "Legg inn knebøy eller rumensk markløft – store muskler forbrenner mest.",
    "kjerne": "Avslutt neste økt med hengende kneløft.",
}


# ═══════════════════════════ Small formatting helpers ═══════════════════════════

def _num(value):
    """Safe float: numbers and numeric strings -> float; None/bool/text/NaN/inf -> None."""
    return ex.to_number(value)


def _round(value, digits=1):
    """Round a number (or return None)."""
    value = _num(value)
    return None if value is None else round(value, digits)


def fmt_kg(kg):
    """Norwegian weight text without the unit: 22.0 -> '22', 67.5 -> '67,5'."""
    kg = _num(kg)
    if kg is None:
        return "–"
    if abs(kg - round(kg)) < 1e-9:
        return str(int(round(kg)))
    return f"{kg:.1f}".replace(".", ",")


def fmt_num(value, digits=1):
    """Norwegian decimal comma: 6.25 -> '6,3'."""
    value = _num(value)
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
    minutes, km = _num(minutes), _num(km)
    if not minutes or not km or km <= 0:
        return None
    total_seconds = int(round(minutes * 60 / km))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def _days_text(n):
    """1 -> '1 dag', 5 -> '5 dager'."""
    return f"{n} dag" if n == 1 else f"{n} dager"


def _parse_date(value):
    """'2026-09-20' (or a date/datetime) -> date, else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_datetime(value, tz):
    """HEVY ISO timestamp -> local date in time zone `tz`, or None.

    HEVY sends UTC ("+00:00"). A timestamp without any offset is treated as UTC too.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return _parse_date(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if tz is not None:
        moment = moment.astimezone(tz)
    return moment.date()


def greeting(hour):
    """'God morgen' 05–10, 'God dag' 10–17, otherwise 'God kveld' (also late at night)."""
    if 5 <= hour < 10:
        return "God morgen"
    if 10 <= hour < 17:
        return "God dag"
    return "God kveld"


# ═══════════════════════════ Sets and workouts ═══════════════════════════

def epley(weight_kg, reps):
    """Estimated one-rep max with Epley's formula: weight × (1 + reps / 30).

    Only valid for 1–15 reps; above that the estimate gets unreliable because the set
    becomes an endurance test rather than a strength test. Returns None when not usable.
    """
    weight_kg, reps = _num(weight_kg), _num(reps)
    if weight_kg is None or reps is None or weight_kg <= 0:
        return None
    if reps < 1 or reps > 15:
        return None
    return round(weight_kg * (1 + reps / 30.0), 1)


def is_work_set(s):
    """True for real work sets (normal, failure, dropset). Warm-up sets do not count."""
    return isinstance(s, dict) and (s.get("type") or "normal") != "warmup"


def _set_weight(s):
    """Weight of a set in kg, treating 0/None as 'no external load'."""
    w = _num(s.get("weight_kg"))
    return w if w and w > 0 else None


def _set_reps(s):
    r = _num(s.get("reps"))
    return int(r) if r and r > 0 else None


def _set_seconds(s):
    d = _num(s.get("duration_seconds"))
    return d if d and d > 0 else None


def _set_meters(s):
    m = _num(s.get("distance_meters"))
    return m if m and m > 0 else None


def is_meaningful_set(s):
    """A work set that actually contains something: weight, reps, time or distance above 0."""
    return is_work_set(s) and bool(_set_weight(s) or _set_reps(s) or _set_seconds(s) or _set_meters(s))


def _as_list(value):
    """Lists stay lists; anything else (None, dict, text) becomes an empty list."""
    return value if isinstance(value, list) else []


def normalize_workouts(workouts, template_muscles=None, tz=None):
    """Clean raw HEVY workouts into a sorted list (oldest first) of simple dicts.

    Each item: {"date", "title", "kind", "exercises": [{"title", "template_id", "group",
    "cardio", "sets": [work sets only]}]}.

    Skipped: workouts without a readable start_time, and "empty" workouts (no exercises,
    or only warm-up/blank sets). Those would otherwise count as training days and could
    even cancel a comeback deload.
    """
    cleaned = []
    for w in _as_list(workouts):
        if not isinstance(w, dict):
            continue
        day = _parse_datetime(w.get("start_time"), tz)
        if day is None:
            continue
        exercises = []
        for e in _as_list(w.get("exercises")):
            if not isinstance(e, dict):
                continue
            title = ex.canonical_title(e.get("title"))
            template_id = e.get("exercise_template_id")
            template_id = template_id if isinstance(template_id, str) else None
            sets = [s for s in _as_list(e.get("sets")) if is_meaningful_set(s)]
            if not sets:
                continue
            exercises.append({
                "title": title,
                "template_id": template_id,
                "group": ex.group(title, template_id, template_muscles),
                "cardio": ex.is_cardio(e, template_muscles),
                "sets": sets,
            })
        if not exercises:
            continue
        has_cardio = any(e["cardio"] for e in exercises)
        has_strength = any(not e["cardio"] for e in exercises)
        if has_cardio and has_strength:
            kind = "mixed"
        elif has_cardio:
            kind = "cardio"
        else:
            kind = "strength"
        cleaned.append({
            "date": day,
            "start": str(w.get("start_time") or ""),
            "title": str(w.get("title") or "").strip() or "Økt",
            "kind": kind,
            "exercises": exercises,
        })
    cleaned.sort(key=lambda item: (item["date"], item["start"]))
    return cleaned


def _main_kind(kind):
    """Collapse 'mixed' into 'strength' (lifting is the bigger recovery cost)."""
    return "cardio" if kind == "cardio" else "strength"


# ═══════════════════════════ Garmin helpers ═══════════════════════════

def _garmin_sorted(garmin_days, today):
    """Garmin days with a valid date up to today, oldest first."""
    days = []
    for d in garmin_days or []:
        if not isinstance(d, dict):
            continue
        day = _parse_date(d.get("date"))
        if day is not None and day <= today:
            days.append((day, d))
    days.sort(key=lambda pair: pair[0])
    return days


def collect_runs(workouts, garmin_days, today):
    """All runs as [{"date": date, "km", "minutes", "pace", "source"}], newest first.

    HEVY: every cardio exercise with a distance becomes one run (sets are summed).
    Garmin: runs on a day that already has a HEVY run are skipped, so a treadmill run
    logged in both places is only counted once.
    """
    runs = []
    hevy_days = set()
    for w in workouts:
        for e in w["exercises"]:
            if not e["cardio"]:
                continue
            meters = sum(_num(s.get("distance_meters")) or 0 for s in e["sets"])
            seconds = sum(_num(s.get("duration_seconds")) or 0 for s in e["sets"])
            if meters <= 0:
                continue
            km = meters / 1000.0
            minutes = seconds / 60.0 if seconds > 0 else None
            runs.append({"date": w["date"], "km": round(km, 2),
                         "minutes": _round(minutes, 1), "pace": fmt_pace(minutes, km),
                         "source": "hevy"})
            hevy_days.add(w["date"])
    for day, g in _garmin_sorted(garmin_days, today):
        if day in hevy_days:
            continue
        for r in _as_list(g.get("runs")):
            if not isinstance(r, dict):
                continue
            km = _num(r.get("km"))
            if not km or km <= 0:
                continue
            minutes = _num(r.get("minutes"))
            runs.append({"date": day, "km": round(km, 2), "minutes": _round(minutes, 1),
                         "pace": fmt_pace(minutes, km), "source": "garmin"})
    runs.sort(key=lambda r: r["date"], reverse=True)
    return runs


def build_sessions(workouts, runs):
    """One list of training sessions (oldest first): HEVY workouts plus Garmin-only runs.

    Each session: {"date", "kind": "strength"|"cardio"|"mixed", "title"}.
    """
    sessions = [{"date": w["date"], "kind": w["kind"], "title": w["title"]} for w in workouts]
    for r in runs:
        if r["source"] == "garmin":
            sessions.append({"date": r["date"], "kind": "cardio", "title": "Løpetur (Garmin)"})
    sessions.sort(key=lambda s: s["date"])
    return sessions


# ═══════════════════════════ Strength: e1RM, key lifts, PRs ═══════════════════════════

def exercise_sessions(workouts):
    """Group work sets per exercise and date: {title: [(date, [sets]), …]} oldest first.

    Two sessions of the same exercise on the same day are merged.
    """
    per_exercise = {}
    for w in workouts:
        for e in w["exercises"]:
            if e["cardio"] or not e["sets"]:
                continue
            days = per_exercise.setdefault(e["title"], {})
            days.setdefault(w["date"], []).extend(e["sets"])
    return {title: sorted(days.items()) for title, days in per_exercise.items()}


def best_set(sets):
    """The set with the highest e1RM as (e1rm, weight, reps), or None if no weighted set."""
    best = None
    for s in sets:
        w, r = _set_weight(s), _set_reps(s)
        value = epley(w, r)
        if value is not None and (best is None or value > best[0]):
            best = (value, w, r)
    return best


def key_lifts(ex_sessions, template_ids, template_muscles, limit=6):
    """Up to `limit` weighted lifts with e1RM history, most recently trained first."""
    lifts = []
    for title, sessions in ex_sessions.items():
        history = []
        top = None
        for day, sets in sessions:
            b = best_set(sets)
            if b is None:
                continue
            history.append({"date": day.isoformat(), "e1rm_kg": b[0]})
            top = b
        if not history:
            continue
        first, current = history[0]["e1rm_kg"], history[-1]["e1rm_kg"]
        change = round((current - first) / first * 100, 1) if len(history) >= 2 and first > 0 else None
        lifts.append({
            "exercise": title,
            "label": ex.label(title),
            "muscle_group": ex.muscle(title, template_ids.get(title), template_muscles),
            "best_e1rm_kg": max(h["e1rm_kg"] for h in history),
            "current_e1rm_kg": current,
            "change_pct": change,
            "last_top_set": f"{fmt_kg(top[1])} kg × {top[2]}",
            "history": history,
        })
    lifts.sort(key=lambda l: (l["history"][-1]["date"], l["best_e1rm_kg"]), reverse=True)
    return lifts[:limit]


def find_prs(ex_sessions, today, days=60, limit=5):
    """Personal records: a session whose best e1RM beats every earlier session.

    The very first session of an exercise is a baseline, not a PR.
    Returns PRs from the last `days` days, newest first.
    """
    prs = []
    for title, sessions in ex_sessions.items():
        best_so_far = None
        for day, sets in sessions:
            b = best_set(sets)
            if b is None:
                continue
            if best_so_far is not None and b[0] > best_so_far:
                prs.append({"exercise": title, "label": ex.label(title), "date": day.isoformat(),
                            "value": f"{fmt_kg(b[1])} kg × {b[2]}", "e1rm_kg": b[0]})
            best_so_far = b[0] if best_so_far is None else max(best_so_far, b[0])
    cutoff = (today - timedelta(days=days)).isoformat()
    recent = [p for p in prs if cutoff <= p["date"] <= today.isoformat()]
    recent.sort(key=lambda p: (p["date"], p["e1rm_kg"]), reverse=True)
    return recent[:limit]


# ═══════════════════════════ Volume and neglected groups ═══════════════════════════

def _monday(day):
    return day - timedelta(days=day.weekday())


def weekly_volume(workouts, today, weeks=8):
    """Tonnage (kg × reps of work sets) per ISO week and display group, oldest week first."""
    this_monday = _monday(today)
    mondays = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    table = {m: {g: 0.0 for g in ex.STRENGTH_GROUPS} for m in mondays}
    for w in workouts:
        m = _monday(w["date"])
        if m not in table:
            continue
        for e in w["exercises"]:
            if e["cardio"] or e["group"] not in table[m]:
                continue
            for s in e["sets"]:
                weight, reps = _set_weight(s), _set_reps(s)
                if weight and reps:
                    table[m][e["group"]] += weight * reps
    result = []
    for m in mondays:
        by_group = {g: int(round(v)) for g, v in table[m].items()}
        result.append({"week_start": m.isoformat(), "total_kg": sum(by_group.values()),
                       "by_group": by_group})
    return result


def sets_last_28d(workouts, today):
    """Number of work sets per display group in the last 28 days (bodyweight sets too)."""
    counts = {g: 0 for g in ex.STRENGTH_GROUPS}
    start = today - timedelta(days=27)
    for w in workouts:
        if not (start <= w["date"] <= today):
            continue
        for e in w["exercises"]:
            if not e["cardio"] and e["group"] in counts:
                counts[e["group"]] += len(e["sets"])
    return counts


def last_trained_per_group(workouts, today):
    """{group: last date with at least one work set} for the six strength groups."""
    last = {}
    for w in workouts:
        if w["date"] > today:
            continue
        for e in w["exercises"]:
            if not e["cardio"] and e["sets"] and e["group"] in ex.STRENGTH_GROUPS:
                if e["group"] not in last or w["date"] > last[e["group"]]:
                    last[e["group"]] = w["date"]
    return last


def neglect_sentence(group_name, days_since):
    """'Skuldrene har ikke blitt trent på 21 dager.' (or '… ennå.' when never trained)."""
    name = GROUP_DEFINITE.get(group_name, group_name).capitalize()
    when = "ennå" if days_since is None else f"på {_days_text(days_since)}"
    return f"{name} har ikke blitt trent {when}."


def neglected_groups(workouts, today):
    """Groups not trained for ≥ 7 days (or never), most neglected first."""
    last = last_trained_per_group(workouts, today)
    items = []
    for g in ex.STRENGTH_GROUPS:
        days_since = (today - last[g]).days if g in last else None
        if days_since is not None and days_since < NEGLECTED_DAYS:
            continue
        message = f"{neglect_sentence(g, days_since)} {NEGLECT_HINTS[g]}"
        items.append({"group": g, "days_since": days_since, "message": message})
    # Never trained first, then most days; ties keep the fixed group order.
    items.sort(key=lambda i: (i["days_since"] is not None, -(i["days_since"] or 0)))
    return items


# ═══════════════════════════ Consistency ═══════════════════════════

def streaks(session_dates, today):
    """(current_streak_weeks, longest_streak_weeks) of ISO weeks with ≥ 1 session.

    The current week counts if it already has a session; if not, the streak is counted
    from last week (you still have days left to keep it alive).
    """
    weeks = {_monday(d) for d in session_dates if d <= today}
    if not weeks:
        return 0, 0
    this_monday = _monday(today)
    cursor = this_monday if this_monday in weeks else this_monday - timedelta(weeks=1)
    current = 0
    while cursor in weeks:
        current += 1
        cursor -= timedelta(weeks=1)
    longest, run, previous = 0, 0, None
    for m in sorted(weeks):
        run = run + 1 if previous is not None and (m - previous).days == 7 else 1
        longest = max(longest, run)
        previous = m
    return current, max(longest, current)


def heatmap(sessions, today, weeks=26):
    """Exactly `weeks` × 7 days, Monday first, ending on Sunday of the current week.

    Each day: {"date", "count", "kind"} where kind is strength/cardio/mixed/None.
    Days after today are always empty.
    """
    end = _monday(today) + timedelta(days=6)
    start = end - timedelta(days=weeks * 7 - 1)
    per_day = {}
    for s in sessions:
        if start <= s["date"] <= min(end, today):
            per_day.setdefault(s["date"], []).append(s["kind"])
    cells = []
    for i in range(weeks * 7):
        day = start + timedelta(days=i)
        kinds = per_day.get(day, []) if day <= today else []
        if not kinds:
            kind = None
        elif all(k == "strength" for k in kinds):
            kind = "strength"
        elif all(k == "cardio" for k in kinds):
            kind = "cardio"
        else:
            kind = "mixed"
        cells.append({"date": day.isoformat(), "count": len(kinds), "kind": kind})
    return cells


def build_consistency(sessions, today):
    past = [s for s in sessions if s["date"] <= today]
    last = past[-1] if past else None
    current, longest = streaks([s["date"] for s in past], today)
    start_30 = today - timedelta(days=29)
    return {
        "days_since_last": (today - last["date"]).days if last else None,
        "last_workout_date": last["date"].isoformat() if last else None,
        "last_workout_title": last["title"] if last else None,
        "streak_weeks": current,
        "longest_streak_weeks": longest,
        "workouts_last_30d": sum(1 for s in past if s["date"] >= start_30),
        "heatmap": heatmap(sessions, today),
    }


# ═══════════════════════════ Comeback ═══════════════════════════

def comeback_factor(days_off):
    """How much of the old load to use after a break (None = no deload needed).

    14–27 days: 80 %, 28–59 days: 75 %, 60+ days: 70 %. Strength is lost slowly, but
    tendons, coordination and tolerance for muscle damage fall faster, so the longer
    the break, the gentler the restart.
    """
    if days_off is None or days_off < COMEBACK_DAYS:
        return None
    if days_off < 28:
        return 0.8
    if days_off < 60:
        return 0.75
    return 0.7


def build_comeback(days_since_last, last_date):
    factor = comeback_factor(days_since_last)
    if factor is None:
        return {"active": False, "days_since_last": days_since_last, "title": "", "message": ""}
    since = f" (sist {fmt_date_no(last_date)})" if last_date else ""
    message = (
        f"Det har gått {_days_text(days_since_last)} siden forrige økt{since} – og det er helt "
        f"greit, livet med en liten en i huset går først. Nå bygger vi opp igjen smart: de første "
        f"øktene kjører vi på ca. 60–80 % av vektene fra sist, avhengig av manualene, og med "
        f"ett sett færre. "
        f"Grunnen er at nervesystemet mister litt av evnen til å rekruttere og koordinere "
        f"muskelfibrene (nevromuskulær effektivitet), og sener og bindevev tåler mindre "
        f"belastning enn før. Går du rett på gamle vekter, øker risikoen for kraftig stølhet "
        f"(DOMS) og belastningsskader. Den gode nyheten er muskelminnet: cellekjernene du "
        f"har bygget opp i muskelfibrene blir værende, så styrken kommer tilbake mye raskere "
        f"enn første gang. Regn med 2–3 uker før du er tilbake på gamle tall."
    )
    return {"active": True, "days_since_last": days_since_last,
            "title": "Velkommen tilbake!", "message": message}


# ═══════════════════════════ Balance (alternation) ═══════════════════════════

def build_balance(sessions, today, comeback_active=False):
    """Strength vs cardio in the last 28 days and what should come next."""
    start = today - timedelta(days=27)
    strength = cardio = 0
    for s in sessions:
        if start <= s["date"] <= today:
            if s["kind"] in ("strength", "mixed"):
                strength += 1
            if s["kind"] in ("cardio", "mixed"):
                cardio += 1
    total = strength + cardio
    pct = int(round(100 * strength / total)) if total else 50
    past = [s for s in sessions if s["date"] <= today]
    last_kind = _main_kind(past[-1]["kind"]) if past else None

    if comeback_active or last_kind is None:
        next_kind = "strength"
    else:
        next_kind = "cardio" if last_kind == "strength" else "strength"

    if total:
        head = (f"Siste 28 dager: {strength} styrkeøkter og {cardio} kondisøkter "
                f"({pct} % styrke). ")
    else:
        head = "Ingen økter de siste 28 dagene. "
    if comeback_active or last_kind is None:
        tail = ("Vi starter med styrke: helkroppsøkter gir mest igjen for tiden etter en pause "
                "og beskytter muskelmassen mens du går ned i vekt.")
    elif next_kind == "cardio":
        tail = ("Sist var styrke, så neste økt er kondis. Da får musklene ca. 48 timer til å "
                "reparere seg mens du fortsatt forbrenner kalorier.")
    else:
        tail = ("Sist var kondis, så neste økt er styrke. Vekslingen gir hvert system tid til "
                "å hente seg inn, og styrketrening bevarer muskelmassen i kaloriunderskudd.")
    return {"strength": strength, "cardio": cardio, "strength_pct": pct,
            "last_kind": last_kind, "next_kind": next_kind, "message": head + tail}


# ═══════════════════════════ Weight ═══════════════════════════

def linear_trend_per_week(points):
    """Least-squares slope of (date, kg) points, in kg per week. None if < 2 distinct dates.

    slope = Σ(x − x̄)(y − ȳ) / Σ(x − x̄)², where x is days and y is kg.
    """
    if len({d for d, _ in points}) < 2:
        return None
    origin = min(d for d, _ in points)
    xs = [(d - origin).days for d, _ in points]
    ys = [kg for _, kg in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope_per_day = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    return slope_per_day * 7


def build_weight(weights, today):
    """Weigh-ins, progress towards 90 kg, trend, ETA and BMI.

    The trend needs at least 4 weigh-ins spread over at least 14 days within the last
    42 days: day-to-day weight swings 1–2 kg with water and glycogen, so two points a few
    days apart say nothing about fat loss. The ETA uses the trend capped at ±1.5 kg/week
    (a realistic upper limit for fat loss). `stale_days` is set when the newest weigh-in
    is more than 14 days old.
    """
    by_date = {}
    for item in _as_list(weights):
        if not isinstance(item, dict):
            continue
        day, kg = _parse_date(item.get("date")), _num(item.get("kg"))
        if day is None or kg is None or kg <= 0 or day > today:
            continue
        by_date[day] = kg                      # the last weigh-in of a day wins
    entries = sorted(by_date.items())[-120:]
    result = {"start_kg": START_KG, "goal_kg": GOAL_KG, "current_kg": None, "lost_kg": None,
              "progress_pct": None, "trend_kg_per_week": None, "eta_date": None, "bmi": None,
              "stale_days": None, "entries": [{"date": d.isoformat(), "kg": round(kg, 1)} for d, kg in entries]}
    if not entries:
        return result
    current = entries[-1][1]
    result["current_kg"] = round(current, 1)
    result["lost_kg"] = round(START_KG - current, 1)
    progress = (START_KG - current) / (START_KG - GOAL_KG) * 100
    result["progress_pct"] = round(min(100.0, max(0.0, progress)), 1)
    result["bmi"] = round(current / HEIGHT_M ** 2, 1)

    age = (today - entries[-1][0]).days
    if age > STALE_WEIGHT_DAYS:
        result["stale_days"] = age

    recent = [(d, kg) for d, kg in entries if d >= today - timedelta(days=42)]
    span = (recent[-1][0] - recent[0][0]).days if recent else 0
    trend = linear_trend_per_week(recent) if len(recent) >= 4 and span >= 14 else None
    if trend is not None:
        result["trend_kg_per_week"] = round(trend, 2)
        eta_trend = max(-MAX_TREND_KG_PER_WEEK, trend)
        if eta_trend < -0.01 and current > GOAL_KG:
            weeks_left = (current - GOAL_KG) / -eta_trend
            if weeks_left <= 520:              # ignore silly forecasts (> 10 years)
                result["eta_date"] = (today + timedelta(days=round(weeks_left * 7))).isoformat()
    return result


# ═══════════════════════════ Recovery (Garmin) ═══════════════════════════

def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def readiness_score(day, previous_days):
    """Readiness 0–100 from sleep, body battery, resting HR and HRV.

    Each part is scored 0–100 and weighted. Missing parts are left out and the rest
    re-weighted, but at least two parts are required: one number alone (e.g. only body
    battery) is too noisy to steer training.
    - Sleep (35 %): 4 h -> 0, 7.5 h (goal) or more -> 100.
    - Body battery peak (25 %): used as-is (Garmin's own 0–100 energy estimate).
    - Resting HR (20 %): vs. the 7-day baseline. Every beat above baseline costs 10 points
      (a raised resting pulse is a classic sign of stress, illness or poor recovery).
    - HRV (20 %): vs. the 7-day baseline. Higher HRV = more parasympathetic ("rest and
      digest") activity; −10 % costs 20 points.
    Returns None when fewer than two parts are available.
    """
    parts = []
    sleep_seconds = _num(day.get("sleep_seconds"))
    if sleep_seconds is not None and sleep_seconds > 0:
        hours = sleep_seconds / 3600
        parts.append((0.35, _clamp((hours - 4) / (SLEEP_GOAL_HOURS - 4) * 100)))
    peak = _num(day.get("body_battery_peak"))
    if peak is not None:
        parts.append((0.25, _clamp(peak)))

    def baseline(key):
        values = [_num(d.get(key)) for d in previous_days[-7:]]
        values = [v for v in values if v]
        return sum(values) / len(values) if values else None

    rhr, rhr_base = _num(day.get("resting_hr")), baseline("resting_hr")
    if rhr and rhr_base:
        parts.append((0.20, _clamp(80 - 10 * (rhr - rhr_base))))
    hrv, hrv_base = _num(day.get("hrv_ms")), baseline("hrv_ms")
    if hrv and hrv_base:
        parts.append((0.20, _clamp(80 + (hrv / hrv_base - 1) * 200)))
    if len(parts) < 2:
        return None
    total_weight = sum(w for w, _ in parts)
    return int(round(sum(w * score for w, score in parts) / total_weight))


def readiness_label(score):
    if score is None:
        return None
    if score >= 70:
        return "Klar for hard økt"
    if score >= 45:
        return "Moderat"
    return "Ta det rolig"


def build_recovery(garmin_days, today):
    empty = {"connected": False, "status_text": "Garmin er ikke tilkoblet", "date": None,
             "readiness": None, "readiness_label": None, "sleep_hours": None, "deep_min": None,
             "rem_min": None, "body_battery_peak": None, "body_battery_low": None,
             "resting_hr": None, "hrv_ms": None, "spo2_avg": None, "respiration_avg": None,
             "history": []}
    if garmin_days is None:
        return empty
    days = _garmin_sorted(garmin_days, today)
    if not days:
        empty["status_text"] = ("Garmin er koblet til, men har ingen data ennå. "
                                "Synk klokka, så dukker søvn og restitusjon opp her.")
        return empty

    day_date, latest = days[-1]
    previous = [d for _, d in days[:-1]]
    score = readiness_score(latest, previous)

    def minutes(key):
        seconds = _num(latest.get(key))
        return int(round(seconds / 60)) if seconds is not None else None

    def whole(key):
        value = _num(latest.get(key))
        return int(round(value)) if value is not None else None

    sleep_seconds = _num(latest.get("sleep_seconds"))
    if day_date == today:
        status = "Oppdatert med data fra i dag"
    else:
        status = f"Siste data fra {fmt_date_no(day_date)}"
    history = []
    for d_date, d in days[-7:]:
        s = _num(d.get("sleep_seconds"))
        bb, hr = _num(d.get("body_battery_peak")), _num(d.get("resting_hr"))
        history.append({"date": d_date.isoformat(),
                        "sleep_hours": round(s / 3600, 1) if s is not None else None,
                        "body_battery_peak": int(round(bb)) if bb is not None else None,
                        "resting_hr": int(round(hr)) if hr is not None else None})
    return {
        "connected": True, "status_text": status, "date": day_date.isoformat(),
        "readiness": score, "readiness_label": readiness_label(score),
        "sleep_hours": round(sleep_seconds / 3600, 1) if sleep_seconds is not None else None,
        "deep_min": minutes("deep_seconds"), "rem_min": minutes("rem_seconds"),
        "body_battery_peak": whole("body_battery_peak"), "body_battery_low": whole("body_battery_low"),
        "resting_hr": whole("resting_hr"), "hrv_ms": whole("hrv_ms"),
        "spo2_avg": whole("spo2_avg"), "respiration_avg": whole("respiration_avg"),
        "history": history,
    }


# ═══════════════════════════ Activity ═══════════════════════════

def build_activity(garmin_days, runs, today):
    connected = bool(garmin_days)
    by_date = {d: g for d, g in _garmin_sorted(garmin_days, today)}
    steps_7d, steps_values, calorie_values = [], [], []
    if connected:
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            g = by_date.get(day, {})
            steps, calories = _num(g.get("steps")), _num(g.get("calories"))
            steps_7d.append({"date": day.isoformat(),
                             "steps": int(round(steps)) if steps is not None else None})
            if steps is not None:
                steps_values.append(steps)
            if calories is not None:
                calorie_values.append(calories)
    today_steps = _num(by_date.get(today, {}).get("steps")) if connected else None
    start_30 = today - timedelta(days=29)
    recent_runs = [r for r in runs if r["date"] <= today]
    return {
        "connected": connected,
        "steps_today": int(round(today_steps)) if today_steps is not None else None,
        "steps_avg_7d": int(round(sum(steps_values) / len(steps_values))) if steps_values else None,
        "calories_avg_7d": int(round(sum(calorie_values) / len(calorie_values))) if calorie_values else None,
        "steps_7d": steps_7d,
        "running": {
            "km_last_30d": round(float(sum(r["km"] for r in recent_runs if r["date"] >= start_30)), 1),
            "runs": [{"date": r["date"].isoformat(), "km": r["km"], "minutes": r["minutes"],
                      "pace": r["pace"]} for r in recent_runs[:8]],
        },
    }


# ═══════════════════════════ Progression (double progression) ═══════════════════════════

HEAVY_SET_PCT = 0.8          # sets at >= 80 % of the top weight count as "real" work sets
HOLD_MIN_DROP_REPS = 2       # reps must fall by at least this much …
HOLD_MIN_DROP_PCT = 0.10     # … and by about 10 % before we call it a bad day


def _describe(n_sets, reps, weight=None, seconds=None):
    """'4×12 @ 22 kg', '12/11/10 @ 22 kg', '3×15' or '3×30 s'."""
    if seconds is not None:
        return f"{n_sets}×{int(round(seconds))} s"
    if isinstance(reps, list):
        text = f"{n_sets}×{reps[0]}" if len(set(reps)) == 1 else "/".join(str(r) for r in reps)
    else:
        text = f"{n_sets}×{reps}"
    return f"{text} @ {fmt_kg(weight)} kg" if weight else text


def _describe_weighted(pairs):
    """What was actually lifted: '4×12 @ 22 kg' or, with mixed weights, '16 kg × 12, 22 kg × 6'."""
    weights = {w for w, _ in pairs}
    if len(weights) == 1:
        return _describe(len(pairs), [r for _, r in pairs], pairs[0][0])
    return ", ".join(f"{fmt_kg(w)} kg × {r}" for w, r in pairs)


def _summarize(sets):
    """Summarise one session's work sets for the progression rules. None if nothing usable.

    Only sets with real numbers count: a weighted set needs weight > 0 AND reps > 0,
    a bodyweight set needs reps > 0 and a timed set needs seconds > 0.
    - "weight" is the top weight, and "reps" are the reps done at that weight.
    - "sets" is how many sets were done at >= 80 % of the top weight (at least 2), so a
      pyramid like 16 kg × 12 + 22 kg × 6 still plans a sensible number of sets.
    """
    weighted = [(_set_weight(s), _set_reps(s)) for s in sets if _set_weight(s) and _set_reps(s)]
    if weighted:
        top = max(w for w, _ in weighted)
        top_reps = [r for w, r in weighted if abs(w - top) < 1e-6]
        heavy_sets = [w for w, _ in weighted if w >= HEAVY_SET_PCT * top - 1e-6]
        return {"type": "weighted", "weight": top, "reps": top_reps, "sets": max(2, len(heavy_sets)),
                "seconds": None, "text": _describe_weighted(weighted)}
    reps = [_set_reps(s) for s in sets if not _set_weight(s) and _set_reps(s)]
    if reps:
        return {"type": "bodyweight", "weight": None, "reps": reps, "sets": len(reps),
                "seconds": None, "text": _describe(len(reps), reps)}
    seconds = [_set_seconds(s) for s in sets if _set_seconds(s)]
    if seconds:
        average = sum(seconds) / len(seconds)
        return {"type": "time", "weight": None, "reps": [], "sets": len(seconds),
                "seconds": average, "text": _describe(len(seconds), None, seconds=average)}
    return None


def _plan(kind, next_text, reason, sets, reps, weight=None):
    """The result of one progression rule (schema fields + plan fields for the workout)."""
    return {"kind": kind, "next": next_text, "reason": reason,
            "plan_sets": sets, "plan_reps": reps, "plan_weight": weight}


def _rack_weight(title, kg):
    """Round a logged weight to real equipment. Returns (weight, short note or '')."""
    plan = ex.round_to_available(title, kg) or kg
    if abs(plan - kg) < 1e-6:
        return plan, ""
    cap = ex.weight_cap(title)
    if cap is not None and kg > cap:
        return plan, f"{fmt_kg(kg)} kg er over taket på {fmt_kg(cap)} kg du har satt, så vi går ned til {fmt_kg(plan)} kg. "
    return plan, f"{fmt_kg(kg)} kg finnes ikke blant vektene dine, så vi bruker {fmt_kg(plan)} kg. "


def _reps_fell(prev, last):
    """True when reps at the same load fell clearly (≥ 2 reps and ≈ 10 %) since last time.

    One rep less is normal day-to-day variation; a real drop usually means poor sleep
    or incomplete recovery, and then it is smarter to repeat than to push on.
    """
    if not prev or prev["type"] != last["type"] or last["type"] == "time":
        return False
    if prev["weight"] != last["weight"]:
        return False
    k = min(len(prev["reps"]), len(last["reps"]))
    if k == 0:
        return False
    before, after = sum(prev["reps"][:k]), sum(last["reps"][:k])
    return before - after >= max(HOLD_MIN_DROP_REPS, HOLD_MIN_DROP_PCT * before)


def _fewer_sets_text(new_sets, old_sets):
    return "og ett sett færre" if new_sets < old_sets else "og samme antall sett"


def _plan_time(last, factor, days_off):
    """Timed holds (Dead Hang): +5 seconds, or ~70 % of the time after a break."""
    n, sec = last["sets"], last["seconds"]
    if factor:
        new_sec = max(10, int(round(sec * factor / 5)) * 5)
        sets = max(2, n - 1)
        return _plan("deload", _describe(sets, None, seconds=new_sec),
                     f"{_days_text(days_off)} siden forrige styrkeøkt: vi starter på ca. {int(factor * 100)} % "
                     f"av tiden ({int(round(sec))} → {new_sec} s), så sener og grep får venne seg til "
                     f"belastningen igjen.", sets, f"{new_sec} s")
    new_sec = int(round(sec)) + 5
    return _plan("reps", _describe(n, None, seconds=new_sec),
                 f"Du holdt {int(round(sec))} s sist. Fem sekunder mer per sett er en liten, målbar "
                 f"økning – grepstyrke og senetoleranse bygges gradvis.", n, f"{new_sec} s")


def _plan_deload(title, last, factor, days_off):
    """Comeback: lighter load (60–80 % depending on the rack), fewer sets, a few more reps."""
    n = last["sets"]
    sets = max(2, n - 1)
    fewer = _fewer_sets_text(sets, n)
    if last["type"] == "bodyweight":
        avg_reps = sum(last["reps"]) / len(last["reps"])
        new_reps = max(5, int(round(avg_reps * factor)))
        return _plan("deload", _describe(sets, new_reps),
                     f"{_days_text(days_off)} siden forrige styrkeøkt: ca. {int(round(factor * 100))} % av "
                     f"repsene ({int(round(avg_reps))} → {new_reps}) {fewer}. Bindevevet tåler mindre "
                     f"etter en pause, og det reduserer stølheten de neste dagene.", sets, str(new_reps))
    old = last["weight"]
    new = ex.deload_weight(title, old, factor) or old
    ratio = new / old
    reps = 10 if ratio >= 0.68 else 12             # a lighter-than-planned weight gets 2 extra reps
    extra = " To ekstra reps veier opp for at nærmeste vekt er litt lett." if reps == 12 else ""
    return _plan("deload", _describe(sets, reps, new),
                 f"{_days_text(days_off)} siden forrige styrkeøkt: start på {fmt_kg(new)} kg i stedet for "
                 f"{fmt_kg(old)} kg (ca. {int(round(ratio * 100))} %) {fewer}.{extra} Nervesystem og sener "
                 f"trenger 1–2 uker på å tåle full last igjen, mens muskelminnet gjør at du er tilbake raskt.",
                 sets, str(reps), new)


def _plan_hold(title, prev, last):
    """Reps fell clearly: repeat the previous target at the same load."""
    n = last["sets"]
    target = int(round(sum(prev["reps"]) / len(prev["reps"])))
    weight, note = (None, "")
    if last["weight"]:
        weight, note = _rack_weight(title, last["weight"])
    load = f" på {fmt_kg(last['weight'])} kg" if last["weight"] else ""
    return _plan("hold", _describe(n, target, weight),
                 f"Repsene falt fra {'/'.join(map(str, prev['reps']))} til {'/'.join(map(str, last['reps']))}"
                 f"{load}. Det tyder oftere på dårlig søvn eller restitusjon enn tapt styrke, så vi holder "
                 f"belastningen og sikter på {target} reps per sett igjen før vi øker. {note}".strip(),
                 n, str(target), weight)


def _plan_bodyweight(last):
    """Bodyweight: +2 reps per set."""
    n = last["sets"]
    avg_reps = sum(last["reps"]) / len(last["reps"])
    new_reps = int(round(avg_reps)) + 2
    return _plan("reps", _describe(n, new_reps),
                 f"Kroppsvektøvelse: du tok i snitt {fmt_num(avg_reps)} reps sist. To reps mer per sett "
                 f"øker volumet ca. {int(round(2 / avg_reps * 100))} % – progressiv overbelastning uten "
                 f"ekstra vekt.", n, str(new_reps))


def _plan_at_max(title, weight, n, note):
    """All sets hit the top, but there is no heavier weight (top dumbbell or own cap)."""
    cap = ex.weight_cap(title)
    if cap is not None and weight >= cap - 1e-6:
        why_max = f"{fmt_kg(cap)} kg er taket du har satt"
    elif ex.load_kind(title) == "dumbbell":
        why_max = f"{fmt_kg(weight)} kg er den tyngste manualen du har"
    else:
        why_max = f"{fmt_kg(weight)} kg er det tyngste du har tilgjengelig"
    if n < MAX_SETS:
        return _plan("sets", _describe(n + 1, TOP_REPS, weight),
                     f"{note}Alle sett nådde {TOP_REPS} reps, men {why_max}. Vi øker volumet i stedet: "
                     f"{n} → {n + 1} sett. Når lasten ikke kan økes, er flere harde sett den neste "
                     f"driveren for muskelvekst.", n + 1, str(TOP_REPS), weight)
    new_reps = TOP_REPS + 3
    return _plan("reps", _describe(n, new_reps, weight),
                 f"{note}{why_max[0].upper() + why_max[1:]}, og du er allerede på {MAX_SETS} sett. Neste steg "
                 f"er flere reps ({new_reps}) og langsommere senking (3 sekunder) for mer tid under spenning.",
                 n, str(new_reps), weight)


def _plan_double_progression(title, last):
    """Weighted exercise: add reps until every set reaches 12, then add weight."""
    n, reps, logged = last["sets"], last["reps"], last["weight"]
    weight, note = _rack_weight(title, logged)
    cap = ex.weight_cap(title)
    at_cap = cap is not None and weight >= cap - 1e-6

    if min(reps) >= TOP_REPS:
        up = None if at_cap else ex.next_weight_up(title, logged)
        if up is None:
            return _plan_at_max(title, weight, n, note)
        jump_pct = (up - logged) / logged * 100
        if ex.load_kind(title) == "dumbbell" and jump_pct > BIG_JUMP_PCT and min(reps) < 15:
            return _plan("reps", _describe(n, 15, weight),
                         f"{note}Alle sett nådde {TOP_REPS} reps, men neste manual er {fmt_kg(up)} kg "
                         f"(+{int(round(jump_pct))} %) – for stort hopp på én gang. Vi strekker rep-rommet "
                         f"til 15 først, så blir overgangen til {fmt_kg(up)} kg håndterbar.", n, "15", weight)
        return _plan("weight", _describe(n, BOTTOM_REPS, up),
                     f"Alle {len(reps)} sett nådde {min(reps)} reps – dobbel progresjon sier mer vekt: "
                     f"{fmt_kg(logged)} → {fmt_kg(up)} kg (+{int(round(jump_pct))} %). Start på "
                     f"{BOTTOM_REPS} reps og bygg opp mot {TOP_REPS} igjen.", n, f"{BOTTOM_REPS}–{TOP_REPS}", up)

    target = min(TOP_REPS, min(reps) + 2)
    done = f"Du tok {_describe(len(reps), reps)} på {fmt_kg(logged)} kg. "
    if at_cap:
        cap_text = note or f"{fmt_kg(cap)} kg er taket du har satt. "
        reason = (f"{done}{cap_text}Her bygger vi videre med reps og deretter sett: "
                  f"sikt på {target} per sett.")
    else:
        reason = (f"{done}{note}Bli på vekta og legg på reps til alle sett når {TOP_REPS} – først da "
                  f"øker vi. Slik vokser volumet før belastningen, og sener og ledd rekker å henge med.")
    return _plan("reps", _describe(n, target, weight), reason, n, str(target), weight)


def progression_for(title, sessions, strength_days_off=None):
    """Next-session suggestion for one exercise, using double progression.

    `sessions` is [(date, [work sets]), …] oldest first. The rules, in order:
    timed hold -> +5 s; long break -> deload; clear drop in reps -> hold;
    bodyweight -> +2 reps; weighted -> double progression (reps first, then weight).
    Returns the schema fields (exercise, label, last, next, kind, reason) plus plan
    fields for the recommended workout (plan_sets, plan_reps, plan_weight), or None.
    """
    usable = [(d, _summarize(sets)) for d, sets in sessions]
    usable = [(d, s) for d, s in usable if s]
    if not usable:
        return None
    last_day, last = usable[-1]
    prev = usable[-2][1] if len(usable) >= 2 else None
    factor = comeback_factor(strength_days_off)

    if last["type"] == "time":
        plan = _plan_time(last, factor, strength_days_off)
    elif factor:
        plan = _plan_deload(title, last, factor, strength_days_off)
    elif _reps_fell(prev, last):
        plan = _plan_hold(title, prev, last)
    elif last["type"] == "bodyweight":
        plan = _plan_bodyweight(last)
    else:
        plan = _plan_double_progression(title, last)

    result = {"exercise": title, "label": ex.label(title), "last": last["text"],
              "last_date": last_day.isoformat()}
    result.update(plan)
    return result


def all_progressions(ex_sessions, strength_days_off):
    """{title: progression dict} for every strength exercise with usable data."""
    result = {}
    for title, sessions in ex_sessions.items():
        p = progression_for(title, sessions, strength_days_off)
        if p:
            result[title] = p
    return result


def _public_progression(p):
    return {k: p[k] for k in ("exercise", "label", "last", "next", "kind", "reason")}


# ═══════════════════════════ Recommendation ═══════════════════════════

FINISHERS = ("Hanging Knee Raise", "Single Leg Standing Calf Raise", "Dead Hang")


def _pick_for_pattern(candidates, last_dates):
    """The candidate trained most recently, or the first one if none were trained."""
    trained = [c for c in candidates if c in last_dates]
    if trained:
        return max(trained, key=lambda c: last_dates[c])
    return candidates[0]


def _exercise_entry(title, progressions, default_sets=3, default_reps="10–12", label_text=None, note=None):
    """One exercise in the recommended workout, with sets/reps/weight from the progression."""
    p = progressions.get(title)
    entry = {"name": title, "label": label_text or ex.label(title),
             "sets": default_sets, "reps": default_reps, "weight_kg": None,
             "note": note if note is not None else ex.cue(title)}
    if p:
        entry["sets"] = p["plan_sets"]
        entry["reps"] = p["plan_reps"] or default_reps
        entry["weight_kg"] = p["plan_weight"]
        if note is None and p["kind"] == "weight":
            entry["note"] = f"Ny vekt: {fmt_kg(p['plan_weight'])} kg! " + ex.cue(title)
    elif ex.equipment_kind(title) in ("dumbbell", "barbell"):
        entry["note"] = ("Ny øvelse: velg en vekt der du har 2–3 reps i reserve. " + ex.cue(title)).strip()
    return entry


def _estimate_minutes(exercises, warmup=8):
    """About 2.5 minutes per set (work + rest) plus warm-up, capped at 60."""
    total_sets = sum(e["sets"] if isinstance(e["sets"], int) else 3 for e in exercises)
    return int(min(60, warmup + round(total_sets * 2.5 / 5) * 5))


def _neglect_boost(chosen, neglected):
    """Extra exercise for the most neglected group the chosen exercises do not cover (or None)."""
    covered = {ex.group(t) for t in chosen}
    for n in neglected:
        extra = NEGLECT_BOOST.get(n["group"])
        if extra and n["group"] not in covered and extra not in chosen:
            return n["group"], extra
    return None, None


def _strength_exercises(progressions, last_dates, neglected, comeback):
    """One exercise per movement pattern; the calf finisher is swapped for a neglected group.

    Normal days put neglected groups first (while you are fresh). On a comeback day the
    pattern order is kept, and the swapped-in exercise gets 2 light sets.
    """
    chosen = [_pick_for_pattern(candidates, last_dates) for _, candidates in PATTERNS]
    boost_group, boost = _neglect_boost(chosen, neglected)
    if boost:
        calf = "Single Leg Standing Calf Raise"
        chosen[chosen.index(calf) if calf in chosen else len(chosen):] = [boost]

    if not comeback:
        rank = {n["group"]: i for i, n in enumerate(neglected)}
        original_order = list(chosen)
        chosen.sort(key=lambda t: (t in FINISHERS, rank.get(ex.group(t), len(rank)), original_order.index(t)))

    entries = [_exercise_entry(t, progressions) for t in chosen[:6]]
    if comeback:
        for e in entries:
            limit = 2 if (e["name"] == boost or e["name"] not in progressions) else 3
            if isinstance(e["sets"], int):
                e["sets"] = min(e["sets"], limit)
    return entries, boost_group, boost


def _common_reasons(ctx):
    """Reusable Norwegian sentences: the fat-loss goal and the recovery score."""
    current = ctx["weight"]["current_kg"]
    if current is not None and current > GOAL_KG:
        fat_loss = f"Målet er 90 kg: du er på {fmt_num(current)} kg nå, {fmt_num(current - GOAL_KG)} kg igjen."
    elif current is not None:
        fat_loss = f"Du er på {fmt_num(current)} kg – under målet på 90 kg. Nå handler det om å holde det."
    else:
        fat_loss = "Målet er å gå ned fra 102 til 90 kg."
    readiness_text = None
    if ctx["readiness"] is not None:
        sleep = ctx["sleep_hours"]
        sleep_part = f" etter {fmt_num(sleep)} t søvn" if sleep is not None else ""
        readiness_text = (f"Restitusjonsscoren er {ctx['readiness']}/100 "
                          f"({readiness_label(ctx['readiness']).lower()}){sleep_part}.")
    return fat_loss, readiness_text


def _alternation_reason(ctx):
    """'Sist var styrke (i går), så vekslingsprinsippet sier kondis nå.' or None."""
    last_kind, days_since = ctx["last_kind"], ctx["days_since_last"]
    if not last_kind or days_since is None:
        return None
    when = {0: "i dag", 1: "i går"}.get(days_since, f"for {_days_text(days_since)} siden")
    prev = "styrke" if last_kind == "strength" else "kondis"
    now_kind = "kondis" if ctx["next_kind"] == "cardio" else "styrke"
    return f"Sist var {prev} ({when}), så vekslingsprinsippet sier {now_kind} nå."


def _finish(kind, title, duration, intensity, why, exercises):
    """Assemble the recommendation dict (max 4 reasons, max 60 minutes)."""
    return {"kind": kind, "title": title, "duration_min": int(min(60, duration)), "intensity": intensity,
            "why": [w.strip() for w in why if w][:4], "exercises": exercises}


def _recovery_plan(ctx, fat_loss, readiness_text):
    exercises = [
        {"name": "Running", "label": "Rolig gange på løpebånd", "sets": 1, "reps": "30 min", "weight_kg": None,
         "note": "Sone 1–2: rask gange med 3–5 % stigning. Du skal kunne prate uanstrengt."},
        _exercise_entry("Dead Hang", {}, default_sets=3, default_reps="20–30 s"),
        _exercise_entry("Walking Lunge", {}, default_sets=2, default_reps="10 per bein",
                        note="Uten vekt, rolig og kontrollert – mest for hoftemobilitet."),
    ]
    why = [readiness_text,
           "Hard trening på dårlig restitusjon gir mer stresshormoner og høyere skaderisiko, men ikke mer "
           "fremgang. Rolig bevegelse øker blodgjennomstrømningen og gjør deg klarere til neste økt.",
           f"Rask gange forbrenner fortsatt ca. 200 kcal på 30 minutter. {fat_loss}"]
    return _finish("recovery", "Restitusjon – rolig gange og mobilitet", 40, "Lett", why, exercises)


def _comeback_plan(ctx, fat_loss, readiness_text):
    exercises, boost_group, boost = _strength_exercises(
        ctx["progressions"], ctx["last_dates"], ctx["neglected"], comeback=True)
    why = [f"Det er {_days_text(ctx['days_since_last'])} siden forrige økt, så vi starter på ca. 60–80 % "
           f"av vektene fra sist (avhengig av manualene), med færre sett."]
    if boost:
        days = next(n["days_since"] for n in ctx["neglected"] if n["group"] == boost_group)
        why.append(f"{neglect_sentence(boost_group, days)[:-1]}, så {ex.label(boost).lower()} "
                   f"tar plassen som siste øvelse – bare to lette sett.")
    why.append("Helkropp med knebøy, hoftehengsel, press og trekk vekker alle de store bevegelsesmønstrene "
               "igjen, uten at én muskelgruppe tar hele støyten (og all stølheten).")
    why.append(readiness_text)
    why.append(fat_loss)
    return _finish("strength", "Velkommen tilbake – helkropp på lett last",
                   min(45, _estimate_minutes(exercises)), "Lett til moderat", why, exercises)


def _strength_plan(ctx, fat_loss, readiness_text):
    first_session = ctx["days_since_last"] is None
    neglected = [] if first_session else ctx["neglected"]     # "never trained" means nothing yet
    exercises, _, _ = _strength_exercises(ctx["progressions"], ctx["last_dates"], neglected, comeback=False)
    if first_session:
        title = "Styrke – første helkroppsøkt"
    else:
        title = "Styrke – helkropp" + (f" med fokus på {neglected[0]['group']}" if neglected else "")
    why = [_alternation_reason(ctx)]
    if first_session:
        why.append("Ingen økter registrert ennå, så vi starter med én øvelse for hvert stort "
                   "bevegelsesmønster: knebøy, hoftehengsel, press og trekk.")
    if neglected:
        why.append(f"{neglect_sentence(neglected[0]['group'], neglected[0]['days_since'])[:-1]}, "
                   f"så vi starter økta der, mens du er uthvilt.")
    planned = [e["name"] for e in exercises]
    weight_ups = [p for p in ctx["progressions"].values() if p["kind"] == "weight" and p["exercise"] in planned]
    if ctx["strength_days_off"] is not None and ctx["strength_days_off"] >= COMEBACK_DAYS:
        why.append(f"Det er {_days_text(ctx['strength_days_off'])} siden forrige styrkeøkt, "
                   f"så vektene er satt ned for en myk start.")
    elif weight_ups:
        p = weight_ups[0]
        why.append(f"Dobbel progresjon: {p['label'].lower()} går opp til {fmt_kg(p['plan_weight'])} kg i dag.")
    why.append("Helkroppsstyrke i kaloriunderskudd sørger for at vekttapet kommer fra fett og ikke muskler. "
               + fat_loss)
    why.append(readiness_text)
    readiness = ctx["readiness"]
    intensity = "Hard" if readiness is not None and readiness >= 70 else "Moderat"
    return _finish("strength", title, _estimate_minutes(exercises), intensity, why, exercises)


def _run_text(run):
    """'4 km på 28 min (7:00 min/km)'."""
    text = f"{fmt_num(run['km'])} km"
    if run["minutes"]:
        text += f" på {fmt_num(run['minutes'], 0)} min"
    if run["pace"]:
        text += f" ({run['pace']} min/km)"
    return text


def _cardio_plan(ctx, fat_loss, readiness_text):
    """Alternate between intervals (after a long easy run) and zone 2 (after a short hard one)."""
    runs, readiness = ctx["runs"], ctx["readiness"]
    last_run = runs[0] if runs else None
    last_long = bool(last_run and (last_run["minutes"] or 0) >= 25)
    core = _exercise_entry("Hanging Knee Raise", ctx["progressions"])
    if last_long and (readiness is None or readiness >= 60):
        running = {"name": "Running", "label": "Intervaller på løpebånd", "sets": 6, "reps": "2 min",
                   "weight_kg": None,
                   "note": "10 min rolig oppvarming, så 6 × 2 min hardt (sone 4, ca. 85–90 % av makspuls) "
                           "med 90 sek gange mellom. Avslutt med 5 min rolig."}
        why = [_alternation_reason(ctx),
               f"Siste løpetur var rolige {_run_text(last_run)}, så nå veksler vi til intervaller.",
               "Intervaller øker det maksimale oksygenopptaket (VO₂maks) mer effektivt enn rolig løping, "
               "og gir en etterforbrenning (EPOC) som varer i timer.",
               readiness_text, fat_loss]
        return _finish("cardio", "Kondis – intervaller 6 × 2 min", 45, "Hard", why, [running, core])

    minutes = 30 if not last_run else (40 if (last_run["minutes"] or 0) >= 35 else 35)
    running = {"name": "Running", "label": "Sone 2-løp", "sets": 1, "reps": f"{minutes} min", "weight_kg": None,
               "note": "Sone 2: ca. 60–70 % av makspuls, du skal klare å snakke i hele setninger. "
                       "Gå gjerne i bakkene – det er pulsen som styrer."}
    if not last_run:
        run_reason = "Ingen løpeturer registrert ennå, så vi bygger grunnformen med rolig tempo først."
    elif last_long:
        run_reason = "Restitusjonsscoren er for lav for intervaller i dag, så vi velger rolig sone 2 i stedet."
    else:
        run_reason = (f"Siste løpetur var {_run_text(last_run)}. Forrige gang var kort og hard, "
                      f"så i dag veksler vi til rolig og jevnt.")
    why = [_alternation_reason(ctx), run_reason,
           "Rolig sone 2-trening bygger flere mitokondrier og kapillærer, og på lav intensitet dekkes en "
           "større andel av energien av fett.", readiness_text, fat_loss]
    return _finish("cardio", f"Kondis – sone 2-løp {minutes} min", minutes + 10, "Lett til moderat",
                   why, [running, core])


def build_recommendation(ctx):
    """Rule-based (deterministic) suggestion for the next workout.

    Priority: poor recovery score -> recovery day; long break -> gentle full-body comeback;
    otherwise follow the strength/cardio alternation. `ctx["readiness"]` is None when
    Garmin data is missing or too old, and is then simply not used.
    """
    fat_loss, readiness_text = _common_reasons(ctx)
    if ctx["readiness"] is not None and ctx["readiness"] < 45:
        return _recovery_plan(ctx, fat_loss, readiness_text)
    if ctx["comeback_active"]:
        return _comeback_plan(ctx, fat_loss, readiness_text)
    if ctx["next_kind"] == "strength":
        return _strength_plan(ctx, fat_loss, readiness_text)
    return _cardio_plan(ctx, fat_loss, readiness_text)


# ═══════════════════════════ Main entry point ═══════════════════════════

def _sources(sources, demo, garmin_days):
    result = {"hevy": "demo" if demo else "ok",
              "garmin": "demo" if demo else ("not_configured" if garmin_days is None else "ok"),
              "errors": []}
    if isinstance(sources, dict):
        for key in ("hevy", "garmin"):
            if sources.get(key):
                result[key] = str(sources[key])
        result["errors"] = [str(e) for e in _as_list(sources.get("errors"))]
    return result


def build_dashboard(workouts, template_muscles, garmin_days, weights, today, now,
                    name="", demo=False, sources=None):
    """Build the complete dashboard dict (see dashboard/SCHEMA.md).

    Args:
        workouts: raw HEVY workout objects (as in GET /v1/workouts -> "workouts").
        template_muscles: {exercise_template_id: HEVY primary_muscle_group}; may be {}.
        garmin_days: normalised Garmin days (newest last), or None when Garmin is not set up.
        weights: [{"date": "YYYY-MM-DD", "kg": 96.4}] in any order.
        today: the local date.
        now: timezone-aware local datetime (used for the greeting and timestamps).
            Its time zone is also used to turn HEVY's UTC start times into local dates,
            so a run at 23:30 UTC in September lands on the next day in Norway. A naive
            or missing `now` falls back to Europe/Oslo, the zone routes.py uses for `today`.
    """
    if isinstance(today, datetime):
        today = today.date()
    template_muscles = template_muscles if isinstance(template_muscles, dict) else {}
    if garmin_days is not None and not isinstance(garmin_days, list):
        garmin_days = []
    if not isinstance(now, datetime):
        now = datetime.combine(today, time(12, 0), tzinfo=LOCAL_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)
    tz = now.tzinfo

    workouts_clean = [w for w in normalize_workouts(workouts, template_muscles, tz) if w["date"] <= today]
    runs = collect_runs(workouts_clean, garmin_days, today)
    sessions = build_sessions(workouts_clean, runs)

    consistency = build_consistency(sessions, today)
    days_since = consistency["days_since_last"]
    last_date = _parse_date(consistency["last_workout_date"])
    comeback = build_comeback(days_since, last_date)
    balance = build_balance(sessions, today, comeback["active"])

    strength_dates = [w["date"] for w in workouts_clean if w["kind"] in ("strength", "mixed")]
    strength_days_off = (today - strength_dates[-1]).days if strength_dates else None

    ex_sessions = exercise_sessions(workouts_clean)
    template_ids = {}
    for w in workouts_clean:
        for e in w["exercises"]:
            if e["template_id"]:
                template_ids[e["title"]] = e["template_id"]
    last_dates = {title: sessions_[-1][0] for title, sessions_ in ex_sessions.items()}

    weight = build_weight(weights, today)
    recovery = build_recovery(garmin_days, today)
    neglected = neglected_groups(workouts_clean, today)
    progressions = all_progressions(ex_sessions, strength_days_off)

    # The coach only trusts recovery data from today or yesterday.
    recovery_date = _parse_date(recovery["date"])
    fresh = recovery_date is not None and (today - recovery_date).days <= STALE_RECOVERY_DAYS
    recommendation = build_recommendation({
        "readiness": recovery["readiness"] if fresh else None,
        "sleep_hours": recovery["sleep_hours"] if fresh else None,
        "days_since_last": days_since, "comeback_active": comeback["active"],
        "strength_days_off": strength_days_off, "last_kind": balance["last_kind"],
        "next_kind": balance["next_kind"], "neglected": neglected, "weight": weight,
        "progressions": progressions, "last_dates": last_dates, "runs": runs,
    })

    # Progression list: exercises in today's plan first, then the most recently trained.
    planned = [e["name"] for e in recommendation["exercises"] if e["name"] in progressions]
    others = sorted((t for t in progressions if t not in planned),
                    key=lambda t: last_dates[t], reverse=True)
    progression = [_public_progression(progressions[t]) for t in (planned + others)[:6]]

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "demo": bool(demo),
        "greeting": greeting(now.hour),
        "name": str(name or ""),
        "weight": weight,
        "consistency": consistency,
        "comeback": comeback,
        "balance": balance,
        "strength": {"lifts": key_lifts(ex_sessions, template_ids, template_muscles),
                     "recent_prs": find_prs(ex_sessions, today)},
        "volume": {"groups": list(ex.STRENGTH_GROUPS),
                   "weeks": weekly_volume(workouts_clean, today),
                   "last_28d_sets": sets_last_28d(workouts_clean, today)},
        "neglected": neglected,
        "recovery": recovery,
        "activity": build_activity(garmin_days, runs, today),
        "recommendation": recommendation,
        "progression": progression,
        "quote": quote_of_day(today),
        "sources": _sources(sources, demo, garmin_days),
    }
