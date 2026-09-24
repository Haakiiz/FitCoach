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

from datetime import date, datetime, timedelta, timezone

from . import exercise_info as ex
from .quotes import quote_of_day

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
    """Convert to float, or None for None/strings/NaN/bool."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:          # NaN is the only value not equal to itself
        return None
    return result


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
    """HEVY ISO timestamp -> local date (using the timezone of `now`), or None."""
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
    """'God morgen' 05–10, 'God dag' 10–17, 'God kveld' 17–23, otherwise 'God natt'."""
    if 5 <= hour < 10:
        return "God morgen"
    if 10 <= hour < 17:
        return "God dag"
    if 17 <= hour < 23:
        return "God kveld"
    return "God natt"


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


def normalize_workouts(workouts, template_muscles=None, tz=None):
    """Clean raw HEVY workouts into a sorted list (oldest first) of simple dicts.

    Each item: {"date", "title", "kind", "exercises": [{"title", "template_id", "group",
    "cardio", "sets": [work sets only]}]}. Workouts without a readable start_time are skipped.
    """
    cleaned = []
    for w in workouts or []:
        if not isinstance(w, dict):
            continue
        day = _parse_datetime(w.get("start_time"), tz)
        if day is None:
            continue
        exercises = []
        for e in w.get("exercises") or []:
            if not isinstance(e, dict):
                continue
            title = ex.canonical_title(e.get("title"))
            template_id = e.get("exercise_template_id")
            sets = [s for s in (e.get("sets") or []) if is_work_set(s)]
            exercises.append({
                "title": title,
                "template_id": template_id,
                "group": ex.group(title, template_id, template_muscles),
                "cardio": ex.is_cardio(e, template_muscles),
                "sets": sets,
            })
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
        for r in g.get("runs") or []:
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


def neglected_groups(workouts, today):
    """Groups not trained for ≥ 7 days (or never), most neglected first."""
    last = last_trained_per_group(workouts, today)
    items = []
    for g in ex.STRENGTH_GROUPS:
        days_since = (today - last[g]).days if g in last else None
        if days_since is not None and days_since < NEGLECTED_DAYS:
            continue
        name = g.capitalize()
        if days_since is None:
            message = f"{name} er ikke trent ennå. {NEGLECT_HINTS[g]}"
        else:
            message = f"{name} er ikke trent på {_days_text(days_since)}. {NEGLECT_HINTS[g]}"
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
    pct = int(round(factor * 100))
    since = f" (sist {fmt_date_no(last_date)})" if last_date else ""
    message = (
        f"Det har gått {_days_text(days_since_last)} siden forrige økt{since} – og det er helt "
        f"greit, livet med en liten en i huset går først. Nå bygger vi opp igjen smart: de første "
        f"øktene kjører vi på ca. {pct} % av vektene fra sist, med ett sett færre. "
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
        tail = ("Sist var kondis, så neste økt er styrke. Beina er uthvilte nok til tunge "
                "løft, og styrketrening bevarer muskelmassen i kaloriunderskudd.")
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
    by_date = {}
    for item in weights or []:
        if not isinstance(item, dict):
            continue
        day, kg = _parse_date(item.get("date")), _num(item.get("kg"))
        if day is None or kg is None or kg <= 0 or day > today:
            continue
        by_date[day] = kg                      # the last weigh-in of a day wins
    entries = sorted(by_date.items())[-120:]
    result = {"start_kg": START_KG, "goal_kg": GOAL_KG, "current_kg": None, "lost_kg": None,
              "progress_pct": None, "trend_kg_per_week": None, "eta_date": None, "bmi": None,
              "entries": [{"date": d.isoformat(), "kg": round(kg, 1)} for d, kg in entries]}
    if not entries:
        return result
    current = entries[-1][1]
    result["current_kg"] = round(current, 1)
    result["lost_kg"] = round(START_KG - current, 1)
    progress = (START_KG - current) / (START_KG - GOAL_KG) * 100
    result["progress_pct"] = round(min(100.0, max(0.0, progress)), 1)
    result["bmi"] = round(current / HEIGHT_M ** 2, 1)

    recent = [(d, kg) for d, kg in entries if d >= today - timedelta(days=42)]
    trend = linear_trend_per_week(recent)
    if trend is not None:
        result["trend_kg_per_week"] = round(trend, 2)
        if trend < -0.01 and current > GOAL_KG:
            weeks_left = (current - GOAL_KG) / -trend
            if weeks_left <= 520:              # ignore silly forecasts (> 10 years)
                result["eta_date"] = (today + timedelta(days=round(weeks_left * 7))).isoformat()
    return result


# ═══════════════════════════ Recovery (Garmin) ═══════════════════════════

def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def readiness_score(day, previous_days):
    """Readiness 0–100 from sleep, body battery, resting HR and HRV.

    Each part is scored 0–100 and weighted; missing parts are left out and the rest
    re-weighted, so two good signals are enough for a score.
    - Sleep (35 %): 4 h -> 0, 7.5 h (goal) or more -> 100.
    - Body battery peak (25 %): used as-is (Garmin's own 0–100 energy estimate).
    - Resting HR (20 %): vs. the 7-day baseline. Every beat above baseline costs 10 points
      (a raised resting pulse is a classic sign of stress, illness or poor recovery).
    - HRV (20 %): vs. the 7-day baseline. Higher HRV = more parasympathetic ("rest and
      digest") activity; −10 % costs 20 points.
    Returns None when no part is available.
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
    if not parts:
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

def _describe(n_sets, reps, weight=None, seconds=None):
    """'4×12 @ 22 kg', '12/11/10 @ 22 kg', '3×15' or '3×30 s'."""
    if seconds is not None:
        return f"{n_sets}×{int(round(seconds))} s"
    if isinstance(reps, list):
        text = f"{n_sets}×{reps[0]}" if len(set(reps)) == 1 else "/".join(str(r) for r in reps)
    else:
        text = f"{n_sets}×{reps}"
    return f"{text} @ {fmt_kg(weight)} kg" if weight else text


def _summarize(sets):
    """Summarise one session's work sets: kind, weight, reps list and seconds."""
    weights = [_set_weight(s) for s in sets if _set_weight(s)]
    if weights:
        top = max(weights)
        at_top = [s for s in sets if _set_weight(s) and abs(_set_weight(s) - top) < 1e-6]
        reps = [_set_reps(s) or 0 for s in at_top]
        return {"type": "weighted", "weight": top, "reps": reps, "sets": len(at_top), "seconds": None}
    reps = [_set_reps(s) for s in sets if _set_reps(s)]
    if reps:
        return {"type": "bodyweight", "weight": None, "reps": reps, "sets": len(reps), "seconds": None}
    seconds = [_set_seconds(s) for s in sets if _set_seconds(s)]
    if seconds:
        return {"type": "time", "weight": None, "reps": [], "sets": len(seconds),
                "seconds": sum(seconds) / len(seconds)}
    return None


def progression_for(title, sessions, strength_days_off=None):
    """Next-session suggestion for one exercise using double progression.

    `sessions` is [(date, [work sets]), …] oldest first. Returns a dict with the schema
    fields (exercise, label, last, next, kind, reason) plus plan fields used by the
    recommendation (plan_sets, plan_reps, plan_weight). None if nothing usable.
    """
    usable = [(d, _summarize(sets)) for d, sets in sessions]
    usable = [(d, s) for d, s in usable if s]
    if not usable:
        return None
    last_day, last = usable[-1]
    prev = usable[-2][1] if len(usable) >= 2 else None
    name = ex.label(title)
    n = last["sets"]
    result = {"exercise": title, "label": name, "last": "", "next": "", "kind": "reps", "reason": "",
              "plan_sets": n, "plan_reps": None, "plan_weight": None, "last_date": last_day.isoformat()}

    def done(kind, next_text, reason, sets, reps, weight=None):
        result.update({"kind": kind, "next": next_text, "reason": reason,
                       "plan_sets": sets, "plan_reps": reps, "plan_weight": weight})
        return result

    factor = comeback_factor(strength_days_off)

    # ── Time-based holds (Dead Hang): +5 seconds ──
    if last["type"] == "time":
        sec = last["seconds"]
        result["last"] = _describe(n, None, seconds=sec)
        if factor:
            new_sec = max(10, int(round(sec * factor / 5)) * 5)
            sets = max(2, n - 1)
            return done("deload", _describe(sets, None, seconds=new_sec),
                        f"{_days_text(strength_days_off)} siden forrige styrkeøkt: vi starter på ca. {int(factor * 100)} % "
                        f"av tiden ({int(sec)} → {new_sec} s), så sener og grep får venne seg til belastningen igjen.",
                        sets, f"{new_sec} s")
        new_sec = int(round(sec)) + 5
        return done("reps", _describe(n, None, seconds=new_sec),
                    f"Du holdt {int(round(sec))} s sist. Fem sekunder mer per sett er en liten, målbar "
                    f"økning – grepstyrke og senetoleranse bygges gradvis.",
                    n, f"{new_sec} s")

    reps = last["reps"]
    avg_reps = sum(reps) / len(reps)
    result["last"] = _describe(n, reps, last["weight"])

    # ── Comeback: deload ──
    if factor:
        sets = max(2, n - 1)
        pct = int(round(factor * 100))
        if last["type"] == "bodyweight":
            new_reps = max(5, int(round(avg_reps * factor)))
            return done("deload", _describe(sets, new_reps),
                        f"{_days_text(strength_days_off)} siden forrige styrkeøkt: ca. {pct} % av repsene "
                        f"({int(round(avg_reps))} → {new_reps}) og ett sett færre. Bindevevet tåler mindre "
                        f"etter en pause, og det reduserer stølheten de neste dagene.",
                        sets, str(new_reps))
        old = last["weight"]
        new = ex.round_to_available(title, old * factor) or old
        if new >= old:                       # rounding must never give a heavier "deload"
            new = ex.next_weight_down(title, old) or old
        real_pct = int(round(new / old * 100))
        return done("deload", _describe(sets, 10, new),
                    f"{_days_text(strength_days_off)} siden forrige styrkeøkt: start på {fmt_kg(new)} kg i stedet for "
                    f"{fmt_kg(old)} kg (ca. {real_pct} %) og ett sett færre. Nervesystem og sener trenger "
                    f"1–2 uker på å tåle full last igjen, mens muskelminnet gjør at du er tilbake raskt.",
                    sets, "10", new)

    # ── Reps fell since last time (same weight): hold ──
    if prev and prev["type"] == last["type"] and prev["weight"] == last["weight"] \
            and sum(reps) < sum(prev["reps"]) and not (prev["seconds"] or last["seconds"]):
        target = _describe(n, prev["reps"], last["weight"])
        weight_text = f" på {fmt_kg(last['weight'])} kg" if last["weight"] else ""
        return done("hold", target,
                    f"Repsene falt fra {sum(prev['reps'])} til {sum(reps)} totalt{weight_text}. Det tyder "
                    f"oftere på dårlig søvn eller restitusjon enn tapt styrke, så vi holder belastningen "
                    f"og sikter på å gjenta forrige økt før vi øker.",
                    n, str(max(prev["reps"])), last["weight"])

    # ── Bodyweight: +2 reps ──
    if last["type"] == "bodyweight":
        new_reps = int(round(avg_reps)) + 2
        return done("reps", _describe(n, new_reps),
                    f"Kroppsvektøvelse: du tok i snitt {fmt_num(avg_reps)} reps sist. To reps mer per sett "
                    f"øker volumet ca. {int(round(2 / avg_reps * 100))} % – progressiv overbelastning "
                    f"uten ekstra vekt.",
                    n, str(new_reps))

    # ── Weighted: double progression ──
    weight = last["weight"]
    all_top = min(reps) >= TOP_REPS
    if all_top:
        up = ex.next_weight_up(title, weight)
        if up is None:
            cap = ex.weight_cap(title)
            why_max = (f"Du har satt et tak på {fmt_kg(cap)} kg på {name.lower()}"
                       if cap is not None and weight >= cap - 1e-6
                       else f"{fmt_kg(weight)} kg er den tyngste manualen du har")
            if n < MAX_SETS:
                return done("sets", _describe(n + 1, TOP_REPS, weight),
                            f"Alle {n} sett nådde {TOP_REPS} reps, men {why_max[0].lower() + why_max[1:]}. "
                            f"Vi øker volumet i stedet: {n} → {n + 1} sett. Når lasten ikke kan økes, "
                            f"er flere harde sett den neste driveren for muskelvekst.",
                            n + 1, str(TOP_REPS), weight)
            new_reps = min(20, max(reps) + 2)
            return done("reps", _describe(n, new_reps, weight),
                        f"{why_max}, og du er allerede på {MAX_SETS} sett. Neste steg er flere reps "
                        f"({new_reps}) eller langsommere senking (3 sekunder) for mer tid under spenning.",
                        n, str(new_reps), weight)
        jump_pct = (up - weight) / weight * 100
        if jump_pct > BIG_JUMP_PCT and min(reps) < 15:
            return done("reps", _describe(n, 15, weight),
                        f"Alle {n} sett nådde {TOP_REPS} reps, men neste manual er {fmt_kg(up)} kg "
                        f"(+{int(round(jump_pct))} %) – for stort hopp på én gang. Vi strekker rep-rommet "
                        f"til 15 først, så blir overgangen til {fmt_kg(up)} kg håndterbar.",
                        n, "15", weight)
        return done("weight", _describe(n, BOTTOM_REPS, up),
                    f"Alle {n} sett nådde {TOP_REPS} reps – dobbel progresjon sier mer vekt: "
                    f"{fmt_kg(weight)} → {fmt_kg(up)} kg (+{int(round(jump_pct))} %). Start på "
                    f"{BOTTOM_REPS} reps og bygg opp mot {TOP_REPS} igjen.",
                    n, f"{BOTTOM_REPS}–{TOP_REPS}", up)

    target = min(TOP_REPS, min(reps) + 2)
    return done("reps", _describe(n, target, weight),
                f"Du tok {_describe(n, reps)} på {fmt_kg(weight)} kg. Bli på vekta og legg "
                f"på reps til alle sett når {TOP_REPS} – først da øker vi. Slik vokser volumet før "
                f"belastningen, og sener og ledd rekker å henge med.",
                n, str(target), weight)


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

def _pick_for_pattern(candidates, last_dates):
    """The candidate trained most recently, or the first one if none were trained."""
    trained = [c for c in candidates if c in last_dates]
    if trained:
        return max(trained, key=lambda c: last_dates[c])
    return candidates[0]


def _exercise_entry(title, progressions, default_sets=3, default_reps="10–12", label_text=None, note=None):
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


def _strength_exercises(progressions, last_dates, neglected, comeback):
    """Pick one exercise per movement pattern and put neglected groups first."""
    chosen = [_pick_for_pattern(candidates, last_dates) for _, candidates in PATTERNS]
    neglected_names = [n["group"] for n in neglected]
    # Add one extra exercise for the most neglected group not already well covered.
    for g in neglected_names:
        extra = NEGLECT_BOOST.get(g)
        if extra and extra not in chosen and not comeback:
            chosen.append(extra)
            break
    rank = {g: i for i, g in enumerate(neglected_names)}
    original_order = list(chosen)

    def order(title):
        title_group = ex.group(title)
        is_finisher = title in ("Hanging Knee Raise", "Single Leg Standing Calf Raise", "Dead Hang")
        return (is_finisher, rank.get(title_group, len(rank)), original_order.index(title))

    chosen.sort(key=order)
    if len(chosen) > 6:
        chosen = [c for c in chosen if c != "Single Leg Standing Calf Raise"][:6]
    entries = [_exercise_entry(t, progressions) for t in chosen]
    if comeback:
        for title, e in zip(chosen, entries):
            limit = 3 if title in progressions else 2      # untrained moves: just 2 easy sets
            if isinstance(e["sets"], int):
                e["sets"] = min(e["sets"], limit)
    return entries


def build_recommendation(ctx):
    """Rule-based (deterministic) suggestion for the next workout.

    Priority: poor readiness -> recovery; long break -> comeback full-body at ~70 %;
    otherwise follow the strength/cardio alternation.
    """
    readiness = ctx["readiness"]
    days_since = ctx["days_since_last"]
    neglected = ctx["neglected"]
    weight = ctx["weight"]
    progressions = ctx["progressions"]

    # Reasons we can reuse.
    current = weight["current_kg"]
    if current is not None and current > GOAL_KG:
        fat_loss = (f"Målet er 90 kg: du er på {fmt_num(current)} kg nå, "
                    f"{fmt_num(current - GOAL_KG)} kg igjen.")
    elif current is not None:
        fat_loss = f"Du er på {fmt_num(current)} kg – under målet på 90 kg. Nå handler det om å holde det."
    else:
        fat_loss = "Målet er å gå ned fra 102 til 90 kg."
    readiness_text = None
    if readiness is not None:
        sleep = ctx["sleep_hours"]
        sleep_part = f" etter {fmt_num(sleep)} t søvn" if sleep is not None else ""
        readiness_text = f"Readiness er {readiness}/100 ({readiness_label(readiness).lower()}){sleep_part}."

    # ── 1. Recovery day ──
    if readiness is not None and readiness < 45:
        exercises = [
            {"name": "Running", "label": "Rolig gange på løpebånd", "sets": 1, "reps": "30 min",
             "weight_kg": None,
             "note": "Sone 1–2: rask gange med 3–5 % stigning. Du skal kunne prate uanstrengt."},
            _exercise_entry("Dead Hang", {}, default_sets=3, default_reps="20–30 s"),
            _exercise_entry("Walking Lunge", {}, default_sets=2, default_reps="10 per bein",
                            note="Uten vekt, rolig og kontrollert – mest for hoftemobilitet."),
        ]
        why = [readiness_text,
               "Hard trening på dårlig restitusjon gir mer stresshormoner og høyere skaderisiko, "
               "men ikke mer fremgang. Rolig bevegelse øker blodgjennomstrømningen og gjør deg "
               "klarere til neste økt.",
               f"Rask gange forbrenner fortsatt ca. 200 kcal på 30 minutter. {fat_loss}".strip()]
        return {"kind": "recovery", "title": "Restitusjon – rolig gange og mobilitet",
                "duration_min": 40, "intensity": "Lett", "why": [w for w in why if w][:4],
                "exercises": exercises}

    # ── 2. Comeback ──
    if ctx["comeback_active"]:
        exercises = _strength_exercises(progressions, ctx["last_dates"], [], comeback=True)
        pct = int(round((comeback_factor(days_since) or 0.7) * 100))
        why = [f"Det er {_days_text(days_since)} siden forrige økt, så vi starter på ca. {pct} % "
               f"av vektene fra sist, med færre sett.",
               "Helkropp med knebøy, hoftehengsel, press og trekk vekker alle de store "
               "bevegelsesmønstrene igjen, uten at én muskelgruppe tar hele støyten (og all stølheten).",
               readiness_text,
               "Store muskelgrupper i bevegelse gir høyt kaloriforbruk og beskytter muskelmassen "
               "mens du går ned mot 90 kg."]
        return {"kind": "strength", "title": "Comeback – helkropp på lett last",
                "duration_min": min(45, _estimate_minutes(exercises)), "intensity": "Lett til moderat",
                "why": [w for w in why if w][:4], "exercises": exercises}

    last_kind = ctx["last_kind"]
    alternation = None
    if last_kind and days_since is not None:
        when = {0: "i dag", 1: "i går"}.get(days_since, f"for {_days_text(days_since)} siden")
        prev = "styrke" if last_kind == "strength" else "kondis"
        now_kind = "kondis" if ctx["next_kind"] == "cardio" else "styrke"
        alternation = f"Sist var {prev} ({when}), så vekslingsprinsippet sier {now_kind} nå."

    # ── 3a. Strength ──
    if ctx["next_kind"] == "strength":
        exercises = _strength_exercises(progressions, ctx["last_dates"], neglected, comeback=False)
        title = "Styrke – helkropp" + (f" med fokus på {neglected[0]['group']}" if neglected else "")
        why = [alternation]
        if neglected:
            n0 = neglected[0]
            ago = ("ikke fått direkte trening ennå" if n0["days_since"] is None
                   else f"ikke vært trent på {_days_text(n0['days_since'])}")
            why.append(f"{n0['group'].capitalize()} har {ago}, så vi starter økta der, mens du er uthvilt.")
        weight_ups = [p for p in progressions.values() if p["kind"] == "weight"
                      and p["exercise"] in [e["name"] for e in exercises]]
        if ctx["strength_days_off"] is not None and ctx["strength_days_off"] >= COMEBACK_DAYS:
            why.append(f"Det er {_days_text(ctx['strength_days_off'])} siden forrige styrkeøkt, "
                       f"så vektene er satt ned for en myk start.")
        elif weight_ups:
            p = weight_ups[0]
            why.append(f"Dobbel progresjon: {p['label'].lower()} går opp til {fmt_kg(p['plan_weight'])} kg i dag.")
        why.append("Helkroppsstyrke i kaloriunderskudd sørger for at vekttapet kommer fra fett og "
                   "ikke muskler. " + fat_loss)
        why.append(readiness_text)
        why = [w.strip() for w in why if w][:4]
        intensity = "Hard" if readiness is not None and readiness >= 70 else "Moderat"
        return {"kind": "strength", "title": title, "duration_min": _estimate_minutes(exercises),
                "intensity": intensity, "why": why, "exercises": exercises}

    # ── 3b. Cardio: zone 2 or intervals ──
    runs = ctx["runs"]
    last_run = runs[0] if runs else None
    last_long = bool(last_run and (last_run["minutes"] or 0) >= 25)
    intervals = last_long and (readiness is None or readiness >= 60)
    run_text = ""
    if last_run:
        run_text = f"{fmt_num(last_run['km'])} km"
        if last_run["minutes"]:
            run_text += f" på {fmt_num(last_run['minutes'], 0)} min"
        if last_run["pace"]:
            run_text += f" ({last_run['pace']} min/km)"
    if intervals:
        exercises = [
            {"name": "Running", "label": "Intervaller på løpebånd", "sets": 6, "reps": "2 min",
             "weight_kg": None,
             "note": "10 min rolig oppvarming, så 6 × 2 min hardt (sone 4, ca. 85–90 % av makspuls) "
                     "med 90 sek gange mellom. Avslutt med 5 min rolig."},
            _exercise_entry("Hanging Knee Raise", progressions),
        ]
        title = "Kondis – intervaller 6 × 2 min"
        duration = 45
        science = ("Intervaller øker det maksimale oksygenopptaket (VO₂maks) mer effektivt enn rolig "
                   "løping, og gir en etterforbrenning (EPOC) som varer i timer.")
        intensity = "Hard"
        last_text = f"Siste løpetur var rolige {run_text}, så nå veksler vi til intervaller."
    else:
        if not last_run:
            minutes = 30
        else:
            minutes = 40 if (last_run["minutes"] or 0) >= 35 else 35
        exercises = [
            {"name": "Running", "label": "Sone 2-løp", "sets": 1, "reps": f"{minutes} min",
             "weight_kg": None,
             "note": "Sone 2: ca. 60–70 % av makspuls, du skal klare å snakke i hele setninger. "
                     "Gå gjerne i bakkene – det er pulsen som styrer."},
            _exercise_entry("Hanging Knee Raise", progressions),
        ]
        title = f"Kondis – sone 2-løp {minutes} min"
        duration = minutes + 10
        science = ("Rolig sone 2-trening bygger flere mitokondrier og kapillærer, og på lav "
                   "intensitet dekkes en større andel av energien av fett.")
        intensity = "Lett til moderat"
        if last_run:
            last_text = (f"Siste løpetur var {run_text}. Forrige gang var kort og hard, "
                         f"så i dag veksler vi til rolig og jevnt.")
        else:
            last_text = "Ingen løpeturer registrert ennå, så vi bygger grunnformen med rolig tempo først."
        if readiness is not None and readiness < 60 and last_long:
            last_text = "Readiness er for lav for intervaller i dag, så vi velger rolig sone 2 i stedet."
    why = [alternation, last_text, science, readiness_text, fat_loss or None]
    why = [w.strip() for w in why if w][:4]
    return {"kind": "cardio", "title": title, "duration_min": min(60, duration),
            "intensity": intensity, "why": why, "exercises": exercises}


# ═══════════════════════════ Main entry point ═══════════════════════════

def _sources(sources, demo, garmin_days):
    result = {"hevy": "demo" if demo else "ok",
              "garmin": "demo" if demo else ("not_configured" if garmin_days is None else "ok"),
              "errors": []}
    if isinstance(sources, dict):
        for key in ("hevy", "garmin"):
            if sources.get(key):
                result[key] = str(sources[key])
        result["errors"] = [str(e) for e in (sources.get("errors") or [])]
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
    """
    if isinstance(today, datetime):
        today = today.date()
    template_muscles = template_muscles if isinstance(template_muscles, dict) else {}
    tz = now.tzinfo if isinstance(now, datetime) else None

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

    recommendation = build_recommendation({
        "readiness": recovery["readiness"], "sleep_hours": recovery["sleep_hours"],
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

    if isinstance(now, datetime):
        generated_at, hour = now.isoformat(timespec="seconds"), now.hour
    else:
        generated_at, hour = datetime.combine(today, datetime.min.time()).isoformat(), 12

    return {
        "generated_at": generated_at,
        "today": today.isoformat(),
        "demo": bool(demo),
        "greeting": greeting(hour),
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
