"""Synthetic demo data for the dashboard.

Used when the environment variable FITCOACH_DEMO is set:
    FITCOACH_DEMO=1          → "active" scenario (last workout 2 days ago)
    FITCOACH_DEMO=comeback   → "comeback" scenario (last workout 99 days ago)

Everything here is MADE UP (the repository is public, so no real workouts are
copied). The data is generated with a fixed random seed (42), so it looks the
same every time, and all dates are relative to `today`.
"""
import os
import random
import uuid
from datetime import date, datetime, time, timedelta, timezone

# ─── Which demo scenario is active? ───

_SCENARIOS = {"1": "active", "active": "active", "comeback": "comeback"}


def demo_scenario() -> str | None:
    """Return "active" or "comeback" when FITCOACH_DEMO is set, otherwise None."""
    return _SCENARIOS.get(os.getenv("FITCOACH_DEMO", "").strip().lower())


# ─── Exercise templates (made-up ids) ───
# title (exactly as in HEVY) → (template id, HEVY primary muscle group)

TEMPLATES = {
    "Goblet Squat":                   ("DE0A1001", "quadriceps"),
    "Romanian Deadlift (Barbell)":    ("DE0A1002", "hamstrings"),
    "Floor Press (Dumbbell)":         ("DE0A1003", "chest"),
    "Dumbbell Row":                   ("DE0A1004", "upper_back"),
    "Shoulder Press (Dumbbell)":      ("DE0A1005", "shoulders"),
    "Bent Over Row (Barbell)":        ("DE0A1006", "upper_back"),
    "Squat (Barbell)":                ("DE0A1007", "quadriceps"),
    "Bench Press (Dumbbell)":         ("DE0A1008", "chest"),
    "Push Up":                        ("DE0A1009", "chest"),
    "Inverted Row":                   ("DE0A100A", "upper_back"),
    "Walking Lunge":                  ("DE0A100B", "quadriceps"),
    "Hanging Knee Raise":             ("DE0A100C", "abdominals"),
    "Dead Hang":                      ("DE0A100D", "forearms"),
    "Single Leg Standing Calf Raise": ("DE0A100E", "calves"),
    "Incline Chest Fly (Dumbbell)":   ("DE0A100F", "chest"),
    "Running":                        ("DE0A1010", "cardio"),
    # Not used in the demo workouts, but part of a real template list
    "Pull Up":                        ("DE0A1011", "lats"),
    "Bicep Curl (Dumbbell)":          ("DE0A1012", "biceps"),
    "Triceps Extension (Dumbbell)":   ("DE0A1013", "triceps"),
    "Hip Thrust (Barbell)":           ("DE0A1014", "glutes"),
    "Lateral Raise (Dumbbell)":       ("DE0A1015", "shoulders"),
}


def demo_template_muscles() -> dict[str, str]:
    """{template id: muscle group}, same shape as sources.fetch_template_muscles()."""
    return {tpl_id: muscle for tpl_id, muscle in TEMPLATES.values()}


# ─── How each exercise progresses over the 20 weeks ───
# "weights": the weights used over time (dumbbells: 1–10, 16, 22, 24 kg only).
# Reps climb from `lo` to `hi` at each weight, then the weight goes up
# ("double progression"). None as weight = bodyweight exercise.

PLAN = {
    "Goblet Squat":                   {"weights": [16, 22, 24], "lo": 10, "hi": 12, "sets": 3},
    "Romanian Deadlift (Barbell)":    {"weights": [60, 65, 70, 75, 80, 85, 90], "lo": 6, "hi": 8, "sets": 3, "warmup": 40},
    "Floor Press (Dumbbell)":         {"weights": [16, 22], "lo": 8, "hi": 12, "sets": 3},
    "Dumbbell Row":                   {"weights": [16, 22, 24], "lo": 10, "hi": 12, "sets": 4},
    "Shoulder Press (Dumbbell)":      {"weights": [10, 16], "lo": 6, "hi": 12, "sets": 3},
    "Bent Over Row (Barbell)":        {"weights": [40, 45, 50, 55, 60], "lo": 8, "hi": 10, "sets": 3, "warmup": 30},
    "Squat (Barbell)":                {"weights": [50, 55, 60, 65, 70, 75, 80, 85], "lo": 5, "hi": 8, "sets": 3, "warmup": 40},
    "Bench Press (Dumbbell)":         {"weights": [16, 22, 24], "lo": 8, "hi": 10, "sets": 3},
    "Push Up":                        {"weights": [None], "lo": 12, "hi": 25, "sets": 3, "failure": True},
    "Inverted Row":                   {"weights": [None], "lo": 6, "hi": 12, "sets": 3},
    "Walking Lunge":                  {"weights": [10, 16], "lo": 16, "hi": 20, "sets": 3},
    "Hanging Knee Raise":             {"weights": [None], "lo": 10, "hi": 15, "sets": 3},
    "Single Leg Standing Calf Raise": {"weights": [10, 16], "lo": 12, "hi": 15, "sets": 3},
    "Incline Chest Fly (Dumbbell)":   {"weights": [8, 10], "lo": 10, "hi": 12, "sets": 3},
}

# Three strength sessions that rotate A → B → C
SESSIONS = {
    "A": ("Styrke A – Bein og press", [
        "Goblet Squat", "Romanian Deadlift (Barbell)", "Floor Press (Dumbbell)",
        "Walking Lunge", "Single Leg Standing Calf Raise", "Hanging Knee Raise",
    ]),
    "B": ("Styrke B – Rygg og skuldre", [
        "Dumbbell Row", "Shoulder Press (Dumbbell)", "Bent Over Row (Barbell)",
        "Inverted Row", "Incline Chest Fly (Dumbbell)", "Dead Hang",
    ]),
    "C": ("Styrke C – Helkropp", [
        "Squat (Barbell)", "Bench Press (Dumbbell)", "Dumbbell Row",
        "Push Up", "Romanian Deadlift (Barbell)", "Hanging Knee Raise",
    ]),
}

STRENGTH_NOTES = [
    "", "", "",
    "Sov dårlig i natt, men kom meg gjennom.",
    "Lillemann våknet tre ganger i natt. Lav energi, men gjennomført.",
    "God økt! Følte meg sterk i dag.",
    "Kort på tid, kjørte gjennom med korte pauser.",
    "Tung start, men ble bedre underveis.",
]
RUN_NOTES = [
    "", "",
    "Rolig tur på mølla, pratetempo hele veien.",
    "Jevnt tempo. Litt tunge bein etter styrke i går.",
    "Fin morgentur ute før resten av huset våknet.",
    "Prøvde å holde pulsen lav.",
]
EXERCISE_NOTES = ["", "", "", "", "God kontroll.", "Siste sett var tungt.", "Fin teknikk i dag."]

WEEKS = 20


# ─── Small helpers ───

def _set(index, kind="normal", weight=None, reps=None, distance=None, duration=None, rpe=None) -> dict:
    """One HEVY set with every key present (None when not used)."""
    return {
        "index": index, "type": kind, "weight_kg": weight, "reps": reps,
        "distance_meters": distance, "duration_seconds": duration,
        "rpe": rpe, "custom_metric": None,
    }


def _iso(moment: datetime) -> str:
    """HEVY-style timestamp, e.g. 2026-09-22T05:12:00+00:00."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _exercise(title: str, index: int, sets: list[dict], notes: str = "", superset_id=None) -> dict:
    return {
        "index": index,
        "title": title,
        "notes": notes,
        "exercise_template_id": TEMPLATES[title][0],
        "superset_id": superset_id,
        "sets": sets,
    }


def _strength_sets(title: str, t: float, rng: random.Random) -> tuple[list[dict], str]:
    """Sets for one exercise at progress `t` (0 = 20 weeks ago, 1 = latest session)."""
    plan = PLAN[title]
    weights = plan["weights"]

    # Where are we on the weight ladder, and how far up the rep range?
    position = min(t, 0.999) * len(weights)
    step = int(position)
    within = position - step
    weight = weights[step]
    reps = plan["lo"] + round(within * (plan["hi"] - plan["lo"]))
    reps = max(plan["lo"], reps - rng.choice([0, 0, 0, 1]))   # an off day now and then
    if weights == [None]:                                    # bodyweight: reps grow the whole time
        reps = plan["lo"] + round(t * (plan["hi"] - plan["lo"])) - rng.choice([0, 0, 1])

    sets = []
    if plan.get("warmup"):
        sets.append(_set(0, "warmup", float(plan["warmup"]), 10))
    for n in range(plan["sets"]):
        set_reps = max(1, reps - (1 if n == plan["sets"] - 1 and rng.random() < 0.4 else 0))
        rpe = rng.choice([None, None, 7.0, 7.5, 8.0, 8.5, 9.0]) if n == plan["sets"] - 1 else None
        kind = "failure" if plan.get("failure") and n == plan["sets"] - 1 else "normal"
        sets.append(_set(len(sets), kind, float(weight) if weight is not None else None, set_reps, rpe=rpe))

    # A Norwegian note, like a real training log
    if title == "Romanian Deadlift (Barbell)" and weight == 90:
        note = "90 kg er taket, jobber med flere reps nå."
    elif weight is not None and within < 1 / (plan["hi"] - plan["lo"] + 1) and step > 0:
        note = "Ny vekt i dag, tungt men kontrollert."
    elif reps >= plan["hi"]:
        note = "Føltes lett, kan øke vekten neste gang."
    else:
        note = rng.choice(EXERCISE_NOTES)
    return sets, note


def _dead_hang_sets(t: float, rng: random.Random) -> list[dict]:
    seconds = 30 + round(t * 30) - rng.choice([0, 0, 5])
    return [_set(n, duration=seconds - n * 5) for n in range(3)]


# ─── Workouts ───

def demo_workouts(today: date, scenario: str = "active") -> list[dict]:
    """About 20 weeks of made-up HEVY workouts, newest first (like GET /v1/workouts).

    3–4 sessions a week, alternating strength and running. In "active" the
    last workout is 2 days before `today`, in "comeback" it is 99 days before.
    Shoulders (only trained in session B) are left alone for the last 16 days.
    """
    rng = random.Random(42)
    last_day = today - timedelta(days=99 if scenario == "comeback" else 2)
    first_day = last_day - timedelta(weeks=WEEKS)

    # 1) Pick 3–4 training days in each week, walking backwards from the last one
    last_monday = last_day - timedelta(days=last_day.weekday())
    picked = {last_day}
    for week in range(WEEKS):
        if week == 11:
            continue                             # a sick week with the little one: no training
        monday = last_monday - timedelta(weeks=week)
        for weekday in rng.sample(range(7), rng.choice([3, 4])):
            day = monday + timedelta(days=weekday)
            if first_day <= day <= last_day:
                picked.add(day)
    days = sorted(picked)

    # 2) Build each workout
    workouts = []
    strength_count = 0
    span = (last_day - first_day).days or 1
    for i, day in enumerate(days):
        t = (day - first_day).days / span       # 0.0 → 1.0 through the plan
        hour, minute = rng.choice([(4, 30), (4, 45), (5, 0), (18, 15), (18, 45), (19, 0)])
        start = datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)
        start += timedelta(minutes=rng.randint(0, 12))

        if i % 2 == 1:
            # ── Cardio: one Running exercise ──
            km = min(8.0, max(4.0, 4.0 + t * 3.0 + rng.uniform(-0.8, 0.8)))
            meters = round(km * 1000, -1)
            pace = min(435, max(360, 435 - t * 55 + rng.uniform(-12, 12)))   # seconds per km
            seconds = int(round(meters / 1000 * pace))
            exercises = [_exercise("Running", 0, [_set(0, distance=meters, duration=seconds)],
                                   notes=rng.choice(["", "", "Mølle, 1 % stigning."]))]
            title = f"Løpetur {km:.1f} km".replace(".", ",")
            description = rng.choice(RUN_NOTES)
            end = start + timedelta(seconds=seconds + 300)
        else:
            # ── Strength: rotate A, B, C (no B the last 16 days → shoulders neglected) ──
            key = "ABC"[strength_count % 3]
            strength_count += 1
            if key == "B" and (last_day - day).days < 16:
                key = "C"
            title, titles = SESSIONS[key]
            exercises = []
            for idx, ex_title in enumerate(titles):
                if ex_title == "Dead Hang":
                    sets, note = _dead_hang_sets(t, rng), ""
                else:
                    sets, note = _strength_sets(ex_title, t, rng)
                superset = 0 if key == "B" and ex_title in ("Inverted Row", "Incline Chest Fly (Dumbbell)") else None
                exercises.append(_exercise(ex_title, idx, sets, note, superset))
            description = rng.choice(STRENGTH_NOTES)
            end = start + timedelta(minutes=rng.randint(45, 60))

        workouts.append({
            "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "title": title,
            "description": description,
            "start_time": _iso(start),
            "end_time": _iso(end),
            "updated_at": _iso(end),
            "created_at": _iso(end),
            "exercises": exercises,
        })

    workouts.reverse()   # HEVY returns the newest workout first
    return workouts


# ─── Garmin ───

def demo_garmin_days(today: date, scenario: str = "active") -> list[dict]:
    """7 made-up Garmin days in the normalised format (SCHEMA.md), newest last.

    Short, broken sleep (new dad), so body battery and HRV follow the sleep.
    Runs are copied from the demo workouts so both sources agree.
    """
    rng = random.Random(42)
    runs_by_date: dict[str, list[dict]] = {}
    if scenario != "comeback":
        for w in demo_workouts(today, scenario):
            for ex in w["exercises"]:
                if ex["title"] == "Running":
                    s = ex["sets"][0]
                    runs_by_date.setdefault(w["start_time"][:10], []).append(
                        {"km": round(s["distance_meters"] / 1000, 2), "minutes": round(s["duration_seconds"] / 60, 1)}
                    )

    days = []
    for back in range(6, -1, -1):
        ds = (today - timedelta(days=back)).isoformat()
        hours = rng.uniform(5.5, 7.5)
        sleep = int(hours * 3600) // 60 * 60
        deep = int(sleep * rng.uniform(0.14, 0.20)) // 60 * 60
        rem = int(sleep * rng.uniform(0.19, 0.25)) // 60 * 60
        runs = runs_by_date.get(ds, [])
        steps = rng.randint(4000, 8000) if scenario == "comeback" else rng.randint(5500, 10500)
        steps += int(sum(r["km"] for r in runs) * 1300)
        if back == 0:
            steps = int(steps * 0.6)   # today is not over yet
        days.append({
            "date": ds,
            "sleep_seconds": sleep,
            "deep_seconds": deep,
            "rem_seconds": rem,
            "light_seconds": sleep - deep - rem,
            "awake_seconds": rng.randint(5, 45) * 60,
            "body_battery_peak": max(30, min(95, round(45 + (hours - 5.5) * 15 + rng.uniform(-5, 5)))),
            "body_battery_low": rng.randint(10, 25),
            "resting_hr": round(58 - (hours - 5.5) * 2 + rng.uniform(-1, 1)),
            "hrv_ms": round(40 + (hours - 5.5) * 5 + rng.uniform(-3, 3)),
            "spo2_avg": rng.randint(92, 96),
            "respiration_avg": round(rng.uniform(12.0, 14.5), 1),
            "steps": steps,
            "calories": rng.randint(2350, 2900),
            "runs": runs,
        })
    return days


# ─── Weigh-ins ───

def demo_weights(today: date) -> list[dict]:
    """Made-up weigh-ins: 102 kg about 20 weeks ago down to 96.4 kg now,
    2–3 times a week with a little day-to-day noise. Oldest first."""
    rng = random.Random(42)
    start = today - timedelta(weeks=WEEKS)
    total_days = (today - start).days

    dates = []
    for week in range(WEEKS + 1):
        week_start = start + timedelta(weeks=week)
        for offset in sorted(rng.sample(range(7), rng.choice([2, 3]))):
            d = week_start + timedelta(days=offset)
            if start < d < today - timedelta(days=1):
                dates.append(d)

    entries = [{"date": start.isoformat(), "kg": 102.0}]
    for d in dates:
        progress = (d - start).days / total_days
        kg = 102.0 - 5.6 * progress ** 0.9 + rng.gauss(0, 0.25)
        entries.append({"date": d.isoformat(), "kg": round(kg, 1)})
    entries.append({"date": (today - timedelta(days=1)).isoformat(), "kg": 96.4})
    return entries
