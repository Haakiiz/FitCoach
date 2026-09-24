# Dashboard data contract

`dashboard/analytics.py::build_dashboard(...)` returns **one JSON-serialisable dict**.
The HTML page (`dashboard/templates/dashboard.html`) and `GET /dashboard/api` both use exactly
this shape. All dates are `"YYYY-MM-DD"` strings, all weights are kilograms (float), all text shown
to the user is **Norwegian**. Any value marked `|null` can be missing — the UI must show a friendly
empty state, never `null`/`NaN`.

```jsonc
{
  "generated_at": "2026-09-24T21:15:00+02:00",
  "today": "2026-09-24",
  "demo": false,                       // true when FITCOACH_DEMO is set (fake data)
  "greeting": "God kveld",             // based on local hour
  "name": "Håkon",                     // from DASHBOARD_NAME env, may be ""

  "weight": {
    "start_kg": 102.0, "goal_kg": 90.0,
    "current_kg": 96.4|null,           // latest weigh-in
    "lost_kg": 5.6|null,
    "progress_pct": 46.7|null,         // 0–100, (start-current)/(start-goal)
    "trend_kg_per_week": -0.45|null,   // linear fit over last 42 days, needs >=2 points
    "eta_date": "2027-03-01"|null,     // only when trend is negative
    "bmi": 28.2|null,
    "entries": [{"date": "2026-09-20", "kg": 96.4}]   // ascending, max 120
  },

  "consistency": {
    "days_since_last": 3|null,         // null = no workouts at all
    "last_workout_date": "2026-09-21"|null,
    "last_workout_title": "…"|null,
    "streak_weeks": 4,                 // consecutive ISO weeks with >=1 workout (current week counts if it has one)
    "longest_streak_weeks": 9,
    "workouts_last_30d": 11,
    "heatmap": [                       // exactly 26 full weeks, Monday-first, oldest first
      {"date": "2026-03-30", "count": 0, "kind": null}   // kind: "strength"|"cardio"|"mixed"|null
    ]
  },

  "comeback": {                        // active when days_since_last >= 14
    "active": true, "days_since_last": 99,
    "title": "Velkommen tilbake!",
    "message": "…"                      // encouraging, explains the deload
  },

  "balance": {                         // last 28 days
    "strength": 6, "cardio": 4,
    "strength_pct": 60,                // 0–100, null-safe (0/0 -> 50)
    "last_kind": "strength"|"cardio"|null,
    "next_kind": "cardio",             // what alternation says comes next
    "message": "…"
  },

  "strength": {
    "lifts": [                         // max 6 key lifts, sorted by most recently trained
      {
        "exercise": "Romanian Deadlift (Barbell)",
        "label": "Rumensk markløft",   // Norwegian display name (fallback = exercise)
        "muscle_group": "hamstrings",
        "best_e1rm_kg": 108.0,          // Epley: w * (1 + reps/30), best ever
        "current_e1rm_kg": 108.0,       // best in most recent session
        "change_pct": 12.5|null,        // current vs first recorded
        "last_top_set": "90 kg × 6",
        "history": [{"date": "2026-04-19", "e1rm_kg": 96.0}]  // ascending, one per session
      }
    ],
    "recent_prs": [                    // PRs within last 60 days, newest first, max 5
      {"exercise": "…", "label": "…", "date": "2026-05-17", "value": "90 kg × 6", "e1rm_kg": 108.0}
    ]
  },

  "volume": {
    "groups": ["bryst", "rygg", "skuldre", "armer", "bein", "kjerne"],  // Norwegian display groups
    "weeks": [                          // last 8 ISO weeks, oldest first
      {"week_start": "2026-08-03", "total_kg": 8450, "by_group": {"bryst": 2100, "rygg": 0, "…": 0}}
    ],
    "last_28d_sets": {"bryst": 12, "rygg": 16, "skuldre": 4, "armer": 0, "bein": 14, "kjerne": 3}
  },

  "neglected": [                       // groups not trained in >= 7 days, most neglected first
    {"group": "skuldre", "days_since": 21|null, "message": "…"}   // null = never trained
  ],

  "recovery": {
    "connected": false,                // Garmin configured AND returned data
    "status_text": "Garmin er ikke tilkoblet",
    "date": "2026-09-24"|null,
    "readiness": 72|null,              // 0–100
    "readiness_label": "Klar for hard økt"|"Moderat"|"Ta det rolig"|null,
    "sleep_hours": 6.2|null, "deep_min": 64|null, "rem_min": 117|null,
    "body_battery_peak": 79|null, "body_battery_low": 19|null,
    "resting_hr": 54|null, "hrv_ms": 48|null,
    "spo2_avg": 93|null, "respiration_avg": 12|null,
    "history": [{"date": "…", "sleep_hours": 6.2|null, "body_battery_peak": 79|null, "resting_hr": 54|null}]  // last 7 days
  },

  "activity": {
    "connected": false,
    "steps_today": 8200|null, "steps_avg_7d": 7600|null, "calories_avg_7d": 2650|null,
    "steps_7d": [{"date": "…", "steps": 8200|null}],
    "running": {                        // from HEVY "Running"/cardio sets (+ Garmin runs when connected)
      "km_last_30d": 12.0,
      "runs": [{"date": "2026-06-06", "km": 4.0, "minutes": 28.0, "pace": "7:00"}]   // newest first, max 8
    }
  },

  "recommendation": {
    "kind": "strength"|"cardio"|"recovery",
    "title": "Styrke B – Rygg og skuldre",
    "duration_min": 50,
    "intensity": "Moderat",
    "why": ["…", "…"],                 // 2–4 short Norwegian reasons
    "exercises": [
      {"name": "Dumbbell Row", "label": "Enarms roing", "sets": 4, "reps": "12", "weight_kg": 22|null, "note": "…"}
    ]
  },

  "progression": [                     // max 6
    {"exercise": "…", "label": "…", "last": "4×10 @ 22 kg", "next": "4×12 @ 22 kg",
     "kind": "weight"|"reps"|"sets"|"hold"|"deload", "reason": "…"}
  ],

  "quote": {"text": "…", "author": "David Goggins"},

  "sources": {"hevy": "ok"|"error"|"demo", "garmin": "ok"|"not_configured"|"error"|"demo", "errors": ["…"]}
}
```

## Inputs to `build_dashboard`

```python
build_dashboard(
    workouts: list[dict],          # raw HEVY workout objects (same shape as GET /v1/workouts -> "workouts")
    template_muscles: dict[str, str],  # exercise_template_id -> HEVY primary_muscle_group (e.g. "chest"); may be {}
    garmin_days: list[dict] | None,    # normalised Garmin days (see below), newest last; None = not configured
    weights: list[dict],           # [{"date": "YYYY-MM-DD", "kg": 96.4}], any order
    today: datetime.date,
    now: datetime.datetime,        # timezone-aware local time (for greeting)
    name: str = "",
    demo: bool = False,
    sources: dict | None = None,   # passed through to output["sources"]
) -> dict
```

Normalised Garmin day (produced by `dashboard/sources.py`, every key present, `None` when missing):

```python
{"date": "2026-09-24",
 "sleep_seconds": 22320, "deep_seconds": 3840, "rem_seconds": 7020, "light_seconds": 11400, "awake_seconds": 60,
 "body_battery_peak": 79, "body_battery_low": 19,
 "resting_hr": 54, "hrv_ms": 48, "spo2_avg": 93, "respiration_avg": 12,
 "steps": 8200, "calories": 2650,
 "runs": [{"km": 5.1, "minutes": 31.5}]}
```
