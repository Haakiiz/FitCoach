"""Tests for the dashboard analytics (run with: python -m pytest tests).

All data here is small and synthetic, built with the helper functions below, so each
test shows exactly which situation it checks.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from dashboard import analytics as an
from dashboard import exercise_info as ex
from dashboard.quotes import QUOTES, quote_of_day

TZ = timezone(timedelta(hours=2))
TODAY = date(2026, 9, 24)          # a Thursday
NOW = datetime(2026, 9, 24, 21, 15, tzinfo=TZ)

# The exercises the user actually does (HEVY titles, verbatim).
USER_TITLES = {
    "Goblet Squat", "Romanian Deadlift (Barbell)", "Romanian Deadlift (Dumbbell)",
    "Floor Press (Dumbbell)", "Dumbbell Row", "Shoulder Press (Dumbbell)",
    "Bent Over Row (Barbell)", "Squat (Barbell)", "Bench Press (Dumbbell)",
    "Bench Press (Barbell)", "Push Up", "Inverted Row", "Walking Lunge",
    "Hanging Knee Raise", "Dead Hang", "Single Leg Standing Calf Raise",
    "Incline Chest Fly (Dumbbell)", "Running",
}


# ─── Fixture builders ───

def wset(weight, reps, kind="normal"):
    """One HEVY set with weight and reps."""
    return {"index": 0, "type": kind, "weight_kg": weight, "reps": reps,
            "distance_meters": None, "duration_seconds": None, "rpe": None}


def tset(seconds):
    """One timed set (e.g. Dead Hang)."""
    return {"index": 0, "type": "normal", "weight_kg": None, "reps": None,
            "distance_meters": None, "duration_seconds": seconds, "rpe": None}


def exercise(title, sets):
    return {"index": 0, "title": title, "notes": "", "exercise_template_id": None, "sets": sets}


def workout(day, exercises, title="Økt"):
    start = f"{day.isoformat()}T16:00:00+00:00"
    return {"id": day.isoformat(), "title": title, "description": "", "start_time": start,
            "end_time": f"{day.isoformat()}T17:00:00+00:00", "exercises": exercises}


def run_workout(day, km=5.0, minutes=30.0):
    return workout(day, [exercise("Running", [{
        "index": 0, "type": "normal", "weight_kg": None, "reps": None,
        "distance_meters": km * 1000, "duration_seconds": minutes * 60, "rpe": None}])], "Løping")


def strength_workout(day, row_kg=22, row_reps=12):
    return workout(day, [
        exercise("Goblet Squat", [wset(10, 10, "warmup")] + [wset(22, 10)] * 4),
        exercise("Romanian Deadlift (Barbell)", [wset(80, 10)] * 3),
        exercise("Floor Press (Dumbbell)", [wset(16, 10)] * 3),
        exercise("Dumbbell Row", [wset(row_kg, row_reps)] * 4),
        exercise("Hanging Knee Raise", [wset(None, 12)] * 3),
    ], "Styrke helkropp")


def garmin_day(day, sleep_h=7.5, bb=85, rhr=52, hrv=55):
    return {"date": day.isoformat(), "sleep_seconds": sleep_h * 3600 if sleep_h is not None else None,
            "deep_seconds": 3600, "rem_seconds": 6000, "light_seconds": 14000, "awake_seconds": 300,
            "body_battery_peak": bb, "body_battery_low": 20, "resting_hr": rhr, "hrv_ms": hrv,
            "spo2_avg": 95, "respiration_avg": 13, "steps": 9000, "calories": 2700, "runs": []}


def build(workouts=(), garmin=None, weights=(), today=TODAY, now=NOW):
    return an.build_dashboard(list(workouts), {}, garmin, list(weights), today, now, name="Håkon")


def sessions(pairs):
    """[(date, [sets])] for progression_for, from (date, sets) pairs."""
    return [(d, s) for d, s in pairs]


# ─── e1RM ───

def test_epley_formula():
    assert an.epley(100, 10) == pytest.approx(133.3, abs=0.05)
    assert an.epley(90, 6) == pytest.approx(108.0)
    assert an.epley(100, 1) == pytest.approx(103.3, abs=0.05)


def test_epley_rejects_unusable_sets():
    assert an.epley(100, 0) is None
    assert an.epley(100, 16) is None       # too many reps for a strength estimate
    assert an.epley(None, 5) is None       # bodyweight
    assert an.epley(0, 5) is None


def test_warmup_sets_are_ignored_for_e1rm_and_volume():
    w = workout(TODAY - timedelta(days=1), [
        exercise("Goblet Squat", [wset(24, 10, "warmup"), wset(16, 10)]),
    ])
    out = build([w])
    lift = out["strength"]["lifts"][0]
    assert lift["last_top_set"] == "16 kg × 10"
    assert out["volume"]["weeks"][-1]["by_group"]["bein"] == 160


def test_pr_detection_skips_first_session():
    d1, d2 = TODAY - timedelta(days=10), TODAY - timedelta(days=3)
    out = build([
        workout(d1, [exercise("Squat (Barbell)", [wset(80, 8)])]),
        workout(d2, [exercise("Squat (Barbell)", [wset(85, 8)])]),
    ])
    prs = out["strength"]["recent_prs"]
    assert len(prs) == 1
    assert prs[0]["date"] == d2.isoformat() and prs[0]["value"] == "85 kg × 8"


# ─── Consistency ───

def test_streak_with_gap():
    monday = TODAY - timedelta(days=TODAY.weekday())
    dates = [monday,                          # this week
             monday - timedelta(weeks=1),     # last week
             monday - timedelta(weeks=3),     # gap before this one
             monday - timedelta(weeks=4),
             monday - timedelta(weeks=5)]
    current, longest = an.streaks(dates, TODAY)
    assert current == 2
    assert longest == 3


def test_streak_counts_from_last_week_when_this_week_is_empty():
    monday = TODAY - timedelta(days=TODAY.weekday())
    dates = [monday - timedelta(weeks=1), monday - timedelta(weeks=2)]
    assert an.streaks(dates, TODAY) == (2, 2)


def test_heatmap_is_26_weeks_monday_first():
    out = build([strength_workout(TODAY - timedelta(days=2)), run_workout(TODAY)])
    cells = out["consistency"]["heatmap"]
    assert len(cells) == 182
    first, last = date.fromisoformat(cells[0]["date"]), date.fromisoformat(cells[-1]["date"])
    assert first.weekday() == 0
    assert last.weekday() == 6 and last >= TODAY and (last - TODAY).days < 7
    by_date = {c["date"]: c for c in cells}
    assert by_date[TODAY.isoformat()]["kind"] == "cardio"
    assert by_date[(TODAY - timedelta(days=2)).isoformat()]["kind"] == "strength"
    for c in cells:
        if date.fromisoformat(c["date"]) > TODAY:
            assert c["count"] == 0 and c["kind"] is None


def test_mixed_day_in_heatmap():
    day = TODAY - timedelta(days=1)
    out = build([strength_workout(day), run_workout(day)])
    cell = [c for c in out["consistency"]["heatmap"] if c["date"] == day.isoformat()][0]
    assert cell["count"] == 2 and cell["kind"] == "mixed"


# ─── Progression (double progression) ───

def test_progression_22_to_24_kg():
    p = an.progression_for("Dumbbell Row", sessions([
        (TODAY - timedelta(days=9), [wset(22, 10)] * 4),
        (TODAY - timedelta(days=2), [wset(22, 12)] * 4),
    ]), strength_days_off=2)
    assert p["kind"] == "weight"
    assert p["last"] == "4×12 @ 22 kg"
    assert "24 kg" in p["next"]
    assert "22 → 24 kg (+9 %)" in p["reason"]


def test_progression_adds_reps_below_top_of_range():
    p = an.progression_for("Dumbbell Row", sessions([(TODAY - timedelta(days=2), [wset(22, 10)] * 4)]), 2)
    assert p["kind"] == "reps" and p["next"] == "4×12 @ 22 kg"


def test_rdl_cap_90_gives_no_weight_increase():
    assert ex.next_weight_up("Romanian Deadlift (Barbell)", 90) is None
    assert ex.round_to_available("Romanian Deadlift (Barbell)", 100) == 90
    p = an.progression_for("Romanian Deadlift (Barbell)",
                           sessions([(TODAY - timedelta(days=2), [wset(90, 12)] * 3)]), 2)
    assert p["kind"] in ("sets", "reps")
    assert p["kind"] != "weight"
    assert "@ 90 kg" in p["next"]
    assert "90 kg" in p["reason"]


def test_heaviest_dumbbell_adds_a_set():
    p = an.progression_for("Goblet Squat", sessions([(TODAY - timedelta(days=2), [wset(24, 12)] * 4)]), 2)
    assert p["kind"] == "sets" and p["next"] == "5×12 @ 24 kg"


def test_big_dumbbell_jump_extends_rep_range_first():
    p = an.progression_for("Floor Press (Dumbbell)", sessions([(TODAY - timedelta(days=2), [wset(10, 12)] * 3)]), 2)
    assert p["kind"] == "reps" and "15" in p["next"] and "16 kg" in p["reason"]


def test_reps_fell_gives_hold():
    p = an.progression_for("Dumbbell Row", sessions([
        (TODAY - timedelta(days=9), [wset(22, 11)] * 4),
        (TODAY - timedelta(days=2), [wset(22, 9)] * 4),
    ]), 2)
    assert p["kind"] == "hold" and "22 kg" in p["next"]


def test_bodyweight_and_timed_progression():
    push = an.progression_for("Push Up", sessions([(TODAY - timedelta(days=2), [wset(None, 15)] * 3)]), 2)
    assert push["kind"] == "reps" and push["next"] == "3×17"
    hang = an.progression_for("Dead Hang", sessions([(TODAY - timedelta(days=2), [tset(30)] * 3)]), 2)
    assert hang["next"] == "3×35 s"


def test_deload_after_99_day_break():
    last = date(2026, 6, 17)
    assert (TODAY - last).days == 99
    out = build([strength_workout(last - timedelta(days=4)), strength_workout(last)])
    assert out["comeback"]["active"] is True
    assert out["comeback"]["days_since_last"] == 99
    assert out["comeback"]["title"] == "Velkommen tilbake!"
    assert "nevromuskulær" in out["comeback"]["message"]
    assert out["progression"], "expected progression suggestions"
    assert all(p["kind"] == "deload" for p in out["progression"])
    row = [p for p in out["progression"] if p["exercise"] == "Dumbbell Row"][0]
    assert row["next"] == "3×10 @ 16 kg"                     # 22 kg × 0.7 -> 16 kg dumbbell
    rdl = [p for p in out["progression"] if p["exercise"] == "Romanian Deadlift (Barbell)"][0]
    assert rdl["next"].endswith("@ 55 kg")                    # 80 kg × 0.7 = 56 -> 55 kg
    rec = out["recommendation"]
    assert rec["kind"] == "strength" and "Comeback" in rec["title"]
    assert "99 dager" in " ".join(rec["why"])
    weights = {e["name"]: e["weight_kg"] for e in rec["exercises"]}
    assert weights["Dumbbell Row"] == 16
    assert rec["duration_min"] <= 60


def test_no_comeback_after_short_break():
    out = build([strength_workout(TODAY - timedelta(days=5))])
    assert out["comeback"]["active"] is False
    assert all(p["kind"] != "deload" for p in out["progression"])


# ─── Balance and recommendation ───

def test_alternation_strength_then_cardio():
    out = build([run_workout(TODAY - timedelta(days=3)), strength_workout(TODAY - timedelta(days=1))])
    assert out["balance"]["last_kind"] == "strength"
    assert out["balance"]["next_kind"] == "cardio"
    assert out["recommendation"]["kind"] == "cardio"
    assert any("styrke" in w and "kondis" in w for w in out["recommendation"]["why"])


def test_alternation_cardio_then_strength():
    out = build([strength_workout(TODAY - timedelta(days=3)), run_workout(TODAY - timedelta(days=1))])
    assert out["balance"]["next_kind"] == "strength"
    rec = out["recommendation"]
    assert rec["kind"] == "strength"
    assert 2 <= len(rec["why"]) <= 4
    assert rec["duration_min"] <= 60
    row = [e for e in rec["exercises"] if e["name"] == "Dumbbell Row"][0]
    assert row["weight_kg"] == 24                             # 4×12 @ 22 kg last time


def test_balance_counts_last_28_days():
    out = build([strength_workout(TODAY - timedelta(days=d)) for d in (2, 6, 10)]
                + [run_workout(TODAY - timedelta(days=d)) for d in (4, 40)])
    bal = out["balance"]
    assert (bal["strength"], bal["cardio"], bal["strength_pct"]) == (3, 1, 75)


def test_low_readiness_gives_recovery():
    garmin = [garmin_day(TODAY - timedelta(days=i)) for i in range(6, 0, -1)]
    garmin.append(garmin_day(TODAY, sleep_h=4.2, bb=25, rhr=60, hrv=40))
    out = build([run_workout(TODAY - timedelta(days=1))], garmin=garmin)
    assert out["recovery"]["readiness"] < 45
    assert out["recovery"]["readiness_label"] == "Ta det rolig"
    assert out["recommendation"]["kind"] == "recovery"


def test_neglected_groups_sorted():
    w = workout(TODAY - timedelta(days=10), [exercise("Shoulder Press (Dumbbell)", [wset(10, 10)])])
    w2 = workout(TODAY - timedelta(days=2), [exercise("Goblet Squat", [wset(22, 10)])])
    groups = [n["group"] for n in build([w, w2])["neglected"]]
    assert "bein" not in groups
    assert groups[-1] == "skuldre"                            # 10 days: trained, but least neglected
    assert set(groups[:-1]) == {"bryst", "rygg", "armer", "kjerne"}   # never trained come first


@pytest.mark.parametrize("scenario", ["empty", "comeback", "strength", "cardio", "recovery"])
def test_recommended_exercises_are_known_hevy_titles(scenario):
    workouts, garmin = [], None
    if scenario == "comeback":
        workouts = [strength_workout(date(2026, 6, 17))]
    elif scenario == "strength":
        workouts = [run_workout(TODAY - timedelta(days=1))]
    elif scenario == "cardio":
        workouts = [strength_workout(TODAY - timedelta(days=1))]
    elif scenario == "recovery":
        garmin = [garmin_day(TODAY, sleep_h=4, bb=15)]
    rec = build(workouts, garmin=garmin)["recommendation"]
    assert rec["exercises"]
    for e in rec["exercises"]:
        assert e["name"] in USER_TITLES
        assert e["label"] and e["label"] != e["name"]      # Norwegian label


# ─── Recovery, activity and weight ───

def test_readiness_none_without_garmin():
    for garmin in (None, []):
        rec = build([], garmin=garmin)["recovery"]
        assert rec["connected"] is False
        assert rec["readiness"] is None and rec["readiness_label"] is None
        assert rec["status_text"]


def test_readiness_high_with_good_data():
    garmin = [garmin_day(TODAY - timedelta(days=i)) for i in range(7, -1, -1)]
    rec = build([], garmin=garmin)["recovery"]
    assert rec["connected"] is True
    assert rec["readiness"] >= 70 and rec["readiness_label"] == "Klar for hard økt"
    assert rec["sleep_hours"] == 7.5
    assert len(rec["history"]) == 7


def test_readiness_ignores_missing_parts():
    day = garmin_day(TODAY, sleep_h=None, bb=None, rhr=None, hrv=None)
    assert an.readiness_score(day, []) is None
    day["sleep_seconds"] = 7.5 * 3600
    assert an.readiness_score(day, []) == 100


def test_runs_merge_hevy_and_garmin_without_duplicates():
    g_same = garmin_day(TODAY - timedelta(days=1))
    g_same["runs"] = [{"km": 5.0, "minutes": 30.0}]           # same run as in HEVY -> skipped
    g_other = garmin_day(TODAY)
    g_other["runs"] = [{"km": 6.0, "minutes": 36.0}]
    out = build([run_workout(TODAY - timedelta(days=1), km=4.0, minutes=28.0)], garmin=[g_same, g_other])
    runs = out["activity"]["running"]["runs"]
    assert [r["km"] for r in runs] == [6.0, 4.0]
    assert runs[1]["pace"] == "7:00"
    assert out["activity"]["running"]["km_last_30d"] == 10.0
    assert out["activity"]["steps_today"] == 9000


def test_weight_trend_eta_and_bmi():
    weights = [{"date": (TODAY - timedelta(days=7 * k)).isoformat(), "kg": 96.0 + 0.5 * k} for k in range(6)]
    w = build([], weights=weights)["weight"]
    assert w["current_kg"] == 96.0 and w["lost_kg"] == 6.0
    assert w["progress_pct"] == 50.0
    assert w["trend_kg_per_week"] == pytest.approx(-0.5)
    assert w["eta_date"] == (TODAY + timedelta(weeks=12)).isoformat()
    assert w["bmi"] == pytest.approx(28.0, abs=0.05)


def test_weight_progress_is_clamped_and_no_eta_when_gaining():
    weights = [{"date": (TODAY - timedelta(days=10)).isoformat(), "kg": 103},
               {"date": TODAY.isoformat(), "kg": 104}]
    w = build([], weights=weights)["weight"]
    assert w["progress_pct"] == 0.0
    assert w["trend_kg_per_week"] > 0 and w["eta_date"] is None


# ─── Robustness and contract ───

SCHEMA_KEYS = {
    "generated_at", "today", "demo", "greeting", "name", "weight", "consistency", "comeback",
    "balance", "strength", "volume", "neglected", "recovery", "activity", "recommendation",
    "progression", "quote", "sources",
}
NESTED_KEYS = {
    "weight": {"start_kg", "goal_kg", "current_kg", "lost_kg", "progress_pct", "trend_kg_per_week",
               "eta_date", "bmi", "entries"},
    "consistency": {"days_since_last", "last_workout_date", "last_workout_title", "streak_weeks",
                    "longest_streak_weeks", "workouts_last_30d", "heatmap"},
    "comeback": {"active", "days_since_last", "title", "message"},
    "balance": {"strength", "cardio", "strength_pct", "last_kind", "next_kind", "message"},
    "strength": {"lifts", "recent_prs"},
    "volume": {"groups", "weeks", "last_28d_sets"},
    "recovery": {"connected", "status_text", "date", "readiness", "readiness_label", "sleep_hours",
                 "deep_min", "rem_min", "body_battery_peak", "body_battery_low", "resting_hr",
                 "hrv_ms", "spo2_avg", "respiration_avg", "history"},
    "activity": {"connected", "steps_today", "steps_avg_7d", "calories_avg_7d", "steps_7d", "running"},
    "recommendation": {"kind", "title", "duration_min", "intensity", "why", "exercises"},
    "quote": {"text", "author"},
    "sources": {"hevy", "garmin", "errors"},
}


def test_empty_input_has_all_schema_keys():
    out = an.build_dashboard([], {}, None, [], TODAY, NOW)
    assert set(out) == SCHEMA_KEYS
    for key, expected in NESTED_KEYS.items():
        assert set(out[key]) == expected, key
    assert out["consistency"]["days_since_last"] is None
    assert len(out["consistency"]["heatmap"]) == 182
    assert out["comeback"]["active"] is False
    assert out["balance"]["strength_pct"] == 50 and out["balance"]["next_kind"] == "strength"
    assert out["weight"]["current_kg"] is None and out["weight"]["entries"] == []
    assert len(out["volume"]["weeks"]) == 8
    assert out["sources"]["garmin"] == "not_configured"
    assert 2 <= len(out["recommendation"]["why"]) <= 4
    assert out["greeting"] == "God kveld"


def test_messy_input_does_not_crash():
    messy = [
        None, {}, {"start_time": "not a date"},
        {"start_time": "2026-09-20T08:00:00Z", "exercises": None},
        {"start_time": "2026-09-21T08:00:00+00:00", "exercises": [
            None, {"title": None, "sets": None},
            {"title": "Mystery Machine Press", "sets": [None, {"type": None, "weight_kg": "x", "reps": None}]},
            {"title": "Dumbbell Row", "sets": [{"weight_kg": 22, "reps": 12}]},
        ]},
    ]
    garmin = [None, {"date": None}, {"date": TODAY.isoformat(), "runs": [None, {"km": None}]}]
    weights = [None, {"date": "bad", "kg": 90}, {"date": TODAY.isoformat(), "kg": None}]
    out = an.build_dashboard(messy, None, garmin, weights, TODAY, NOW)
    json.dumps(out)
    assert out["consistency"]["last_workout_date"] == "2026-09-21"


def test_output_is_json_serialisable():
    garmin = [garmin_day(TODAY - timedelta(days=i)) for i in range(6, -1, -1)]
    weights = [{"date": (TODAY - timedelta(days=3 * k)).isoformat(), "kg": 97 + 0.2 * k} for k in range(10)]
    workouts = [strength_workout(TODAY - timedelta(days=d)) for d in (1, 5, 9)] + \
               [run_workout(TODAY - timedelta(days=d)) for d in (3, 7)]
    out = build(workouts, garmin=garmin, weights=weights)
    text = json.dumps(out, ensure_ascii=False)
    for bad in ("NaN", "Infinity", "undefined"):
        assert bad not in text
    assert json.loads(text)["today"] == "2026-09-24"


def test_sources_passthrough_and_demo():
    out = an.build_dashboard([], {}, None, [], TODAY, NOW, demo=True,
                             sources={"hevy": "error", "errors": ["HEVY svarte ikke"]})
    assert out["demo"] is True
    assert out["sources"] == {"hevy": "error", "garmin": "demo", "errors": ["HEVY svarte ikke"]}


@pytest.mark.parametrize("hour,text", [(6, "God morgen"), (12, "God dag"), (20, "God kveld"),
                                       (23, "God natt"), (3, "God natt")])
def test_greeting(hour, text):
    assert an.greeting(hour) == text


# ─── exercise_info and quotes ───

def test_group_mapping_and_fallbacks():
    muscles = {"t1": "lats", "t2": "full_body"}
    assert ex.group("Whatever", "t1", muscles) == "rygg"
    assert ex.group("Goblet Squat", "t2", muscles) == "bein"         # full_body -> title lookup
    assert ex.group("Cable Lateral Raise") == "skuldre"               # keyword fallback
    assert ex.group("Hanging Leg Raise") == "kjerne"
    assert ex.group("Running") == "kondis"
    assert ex.label("Romanian Deadlift (Barbell)") == "Rumensk markløft (stang)"
    assert ex.label("Unknown Thing") == "Unknown Thing"


def test_equipment_and_available_weights():
    assert ex.equipment_kind("Goblet Squat") == "dumbbell"
    assert ex.equipment_kind("Squat (Barbell)") == "barbell"
    assert ex.equipment_kind("Push Up") == "bodyweight"
    assert ex.equipment_kind("Running") == "cardio"
    assert ex.next_weight_up("Goblet Squat", 10) == 16
    assert ex.next_weight_up("Goblet Squat", 22) == 24
    assert ex.next_weight_up("Goblet Squat", 24) is None
    assert ex.next_weight_up("Squat (Barbell)", 60) == 62.5
    assert ex.round_to_available("Goblet Squat", 16.5) == 16
    assert ex.round_to_available("Squat (Barbell)", 56) == 55
    assert ex.is_cardio(exercise("Dead Hang", [tset(30)])) is False
    assert ex.is_cardio({"title": "Treadmill Walk", "sets": []}) is True


def test_quote_of_day_is_deterministic():
    assert len(QUOTES) >= 25
    assert quote_of_day(TODAY) == quote_of_day(TODAY)
    assert quote_of_day(TODAY) != quote_of_day(TODAY + timedelta(days=1))
    for q in QUOTES:
        assert q["text"] and q["author"]
