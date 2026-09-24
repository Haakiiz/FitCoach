"""Facts about exercises: Norwegian names, muscle groups and the user's equipment.

HEVY names exercises in English ("Goblet Squat"). The dashboard shows them in Norwegian
and groups them into six simple *display groups* (bryst, rygg, skuldre, armer, bein, kjerne)
plus "kondis" for cardio.

This file also knows which weights exist in the home gym, so the coach never suggests
a dumbbell that is not on the rack:

- Dumbbells: 1–10 kg (1 kg steps), 16, 22 and 24 kg.
- Z-bar / barbell with plates: 2.5 kg steps.
- A self-imposed ceiling of 90 kg on Romanian Deadlift (Barbell).

Only the standard library is used, and every function is safe to call with odd input
(unknown titles, None, empty strings).
"""

# ─── Display groups ───
STRENGTH_GROUPS = ["bryst", "rygg", "skuldre", "armer", "bein", "kjerne"]
CARDIO_GROUP = "kondis"

# HEVY `primary_muscle_group` -> Norwegian display group.
# "full_body" and "other" are deliberately missing: for those we look at the title instead.
MUSCLE_TO_GROUP = {
    "chest": "bryst",
    "lats": "rygg",
    "upper_back": "rygg",
    "lower_back": "rygg",
    "traps": "rygg",
    "neck": "rygg",
    "shoulders": "skuldre",
    "biceps": "armer",
    "triceps": "armer",
    "forearms": "armer",
    "quadriceps": "bein",
    "hamstrings": "bein",
    "glutes": "bein",
    "calves": "bein",
    "adductors": "bein",
    "abductors": "bein",
    "abdominals": "kjerne",
    "cardio": "kondis",
}

# ─── Known exercises ───
# Keys are HEVY titles *verbatim*. "muscle" uses HEVY's own muscle names.
# "equipment" is one of: "dumbbell", "barbell", "bodyweight", "cardio".
# "cue" is a short coaching tip with the reason behind it (shown in the recommended workout).
EXERCISES = {
    "Goblet Squat": {
        "label": "Goblet-knebøy", "muscle": "quadriceps", "equipment": "dumbbell",
        "cue": "Hold manualen tett inntil brystet. Motvekten foran lar deg sitte dypt med "
               "oppreist overkropp, og det gir lang bevegelsesbane for lår og sete.",
    },
    "Romanian Deadlift (Barbell)": {
        "label": "Rumensk markløft (stang)", "muscle": "hamstrings", "equipment": "barbell",
        "cue": "Skyv hofta bakover med lett bøy i knærne og stangen tett inntil beina. "
               "Baksida av låret belastes i strukket posisjon, og det gir mye vekst.",
    },
    "Romanian Deadlift (Dumbbell)": {
        "label": "Rumensk markløft (manualer)", "muscle": "hamstrings", "equipment": "dumbbell",
        "cue": "Manualene glir langs forsiden av beina mens hofta går bakover. Stopp når du "
               "kjenner strekk bak på låret, og hold ryggen nøytral hele veien.",
    },
    "Floor Press (Dumbbell)": {
        "label": "Gulvpress med manualer", "muscle": "chest", "equipment": "dumbbell",
        "cue": "La albuene lande mykt i gulvet, ca. 45° ut fra kroppen. Gulvet korter ned "
               "bevegelsen og skåner skuldrene, mens triceps jobber hardt i toppen.",
    },
    "Dumbbell Row": {
        "label": "Enarms roing", "muscle": "upper_back", "equipment": "dumbbell",
        "cue": "Trekk albuen mot hofta, ikke rett opp. Da jobber den store ryggmuskelen "
               "(latissimus) gjennom hele banen i stedet for at biceps tar over.",
    },
    "Shoulder Press (Dumbbell)": {
        "label": "Skulderpress med manualer", "muscle": "shoulders", "equipment": "dumbbell",
        "cue": "Stram sete og mage så korsryggen ikke svaier, og press rett opp. "
               "Fremre og midtre deltamuskel gjør jobben.",
    },
    "Bent Over Row (Barbell)": {
        "label": "Foroverbøyd roing med stang", "muscle": "upper_back", "equipment": "barbell",
        "cue": "Len overkroppen ca. 45° fremover med nøytral rygg. Trekk stangen mot navlen "
               "og klem skulderbladene sammen i toppen.",
    },
    "Squat (Barbell)": {
        "label": "Knebøy med stang", "muscle": "quadriceps", "equipment": "barbell",
        "cue": "Pust inn og spenn magen før hver rep. Trykket i buken stabiliserer "
               "korsryggen under tung last.",
    },
    "Bench Press (Dumbbell)": {
        "label": "Benkpress med manualer", "muscle": "chest", "equipment": "dumbbell",
        "cue": "Manualer gir lengre bevegelsesbane enn stang og lar hver arm jobbe for seg, "
               "og det jevner ut forskjeller mellom sidene.",
    },
    "Bench Press (Barbell)": {
        "label": "Benkpress med stang", "muscle": "chest", "equipment": "barbell",
        "cue": "Samle skulderbladene og trykk dem ned, med føttene godt i gulvet. "
               "Senk stangen kontrollert til nedre del av brystet.",
    },
    "Push Up": {
        "label": "Armhevinger", "muscle": "chest", "equipment": "bodyweight",
        "cue": "Hold kroppen rett fra hæl til hode, som en planke i bevegelse, "
               "og senk brystet nesten helt ned til gulvet.",
    },
    "Inverted Row": {
        "label": "Omvendt roing", "muscle": "upper_back", "equipment": "bodyweight",
        "cue": "Jo mer vannrett kroppen er, jo tyngre blir det. Klem skulderbladene "
               "sammen i toppen.",
    },
    "Walking Lunge": {
        "label": "Gående utfall", "muscle": "quadriceps", "equipment": "bodyweight",
        "cue": "Ta lange, kontrollerte steg og la det bakre kneet nesten nå gulvet. Ett bein "
               "om gangen avslører og retter opp ubalanse mellom sidene.",
    },
    "Hanging Knee Raise": {
        "label": "Hengende kneløft", "muscle": "abdominals", "equipment": "bodyweight",
        "cue": "Løft knærne ved å rulle bekkenet opp, ikke bare ved å bøye i hofta. "
               "Da jobber magemusklene og ikke bare hoftebøyerne.",
    },
    "Dead Hang": {
        "label": "Heng i stang", "muscle": "forearms", "equipment": "bodyweight",
        "cue": "Heng passivt med avslappede skuldre. Det styrker grepet og avlaster "
               "ryggraden, noe som er fint etter en dag med babybæring.",
    },
    "Single Leg Standing Calf Raise": {
        "label": "Ettbeins tåhev", "muscle": "calves", "equipment": "bodyweight",
        "cue": "Gå helt ned for strekk og helt opp på tå, med en liten pause på toppen. "
               "Leggene svarer godt på full bevegelsesbane og mange reps.",
    },
    "Incline Chest Fly (Dumbbell)": {
        "label": "Skrå flyes med manualer", "muscle": "chest", "equipment": "dumbbell",
        "cue": "Ha lett bøy i albuene og åpne armene til du kjenner strekk over brystet. "
               "Skrå benk flytter mer av jobben til øvre del av brystet.",
    },
    "Running": {
        "label": "Løping", "muscle": "cardio", "equipment": "cardio",
        "cue": "Sett løpebåndet på 1 % stigning. Det ligner luftmotstanden du har når du løper ute.",
    },
    # Common extras, so other logged exercises also get a Norwegian name.
    "Deadlift (Barbell)": {"label": "Markløft med stang", "muscle": "lower_back", "equipment": "barbell"},
    "Overhead Press (Barbell)": {"label": "Skulderpress med stang", "muscle": "shoulders", "equipment": "barbell"},
    "Hip Thrust (Barbell)": {"label": "Hoftehev med stang", "muscle": "glutes", "equipment": "barbell"},
    "Bicep Curl (Dumbbell)": {"label": "Bicepscurl med manualer", "muscle": "biceps", "equipment": "dumbbell"},
    "Hammer Curl (Dumbbell)": {"label": "Hammercurl", "muscle": "biceps", "equipment": "dumbbell"},
    "Triceps Extension (Dumbbell)": {"label": "Tricepsekstensjon med manual", "muscle": "triceps", "equipment": "dumbbell"},
    "Lateral Raise (Dumbbell)": {"label": "Sidehev med manualer", "muscle": "shoulders", "equipment": "dumbbell"},
    "Bulgarian Split Squat": {"label": "Bulgarsk utfall", "muscle": "quadriceps", "equipment": "bodyweight"},
    "Pull Up": {"label": "Pull-ups", "muscle": "lats", "equipment": "bodyweight"},
    "Chin Up": {"label": "Chin-ups", "muscle": "lats", "equipment": "bodyweight"},
    "Plank": {"label": "Planke", "muscle": "abdominals", "equipment": "bodyweight"},
    "Crunch": {"label": "Situps (crunch)", "muscle": "abdominals", "equipment": "bodyweight"},
    "Walking": {"label": "Gange", "muscle": "cardio", "equipment": "cardio"},
}

# ─── Keyword fallback for unknown titles ───
# Checked in this order; the first matching keyword wins. The order matters:
# "leg raise" must hit kjerne before "leg" hits bein, "shoulder press" must hit skuldre
# before "press" hits bryst, and "rowing machine" must be cardio before "row" is rygg.
_GROUP_KEYWORDS = [
    ("kondis", ["running", "treadmill", "jogging", "cycling", "bike", "elliptical",
                "jump rope", "rowing machine", "stair", "swim", "sprint", "hiit", "ski erg"]),
    ("kjerne", ["crunch", "plank", "sit up", "situp", "knee raise", "leg raise", "ab wheel",
                "russian twist", "hollow", "dead bug", "pallof", "v up", "toes to bar", "oblique"]),
    ("bein", ["leg curl", "leg extension", "leg press", "calf", "hip thrust", "glute"]),
    ("skuldre", ["shoulder", "lateral raise", "front raise", "overhead press", "arnold",
                 "face pull", "rear delt", "military press", "upright row"]),
    ("armer", ["curl", "tricep", "skull crusher", "dip", "wrist", "dead hang", "farmer"]),
    ("rygg", ["row", "pull up", "pullup", "chin up", "pulldown", "pull down", "shrug",
              "back extension", "good morning", "superman", "pullover"]),
    ("bryst", ["bench", "chest", "push up", "pushup", "fly", "floor press", "press"]),
    ("bein", ["squat", "lunge", "deadlift", "step up", "split", "leg", "hip", "box jump"]),
]

# ─── The user's equipment ───
DUMBBELLS_KG = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 22, 24]
BARBELL_STEP_KG = 2.5
BARBELL_MIN_KG = 10.0      # an empty Z-bar weighs roughly 10 kg
WEIGHT_CAPS_KG = {"Romanian Deadlift (Barbell)": 90.0}   # the user's own "ikke mer enn 90 kg"


def _clean(title):
    """Return the title as a stripped string ('' for None)."""
    return str(title or "").strip()


def _known(title):
    """Look up a known exercise, ignoring upper/lower case. Returns a dict or None."""
    title = _clean(title)
    if title in EXERCISES:
        return EXERCISES[title]
    lower = title.lower()
    for key, info in EXERCISES.items():
        if key.lower() == lower:
            return info
    return None


def canonical_title(title):
    """Return the exact HEVY spelling for a known title ('goblet squat' -> 'Goblet Squat')."""
    lower = _clean(title).lower()
    for key in EXERCISES:
        if key.lower() == lower:
            return key
    return _clean(title)


def label(title):
    """Norwegian display name. Unknown exercises keep their HEVY title."""
    info = _known(title)
    return info["label"] if info else _clean(title)


def cue(title):
    """A short Norwegian technique tip for the exercise ('' when we have none)."""
    info = _known(title)
    return (info or {}).get("cue", "")


def _group_from_keywords(title):
    """Guess the display group from words in the title. Returns None if nothing matches."""
    lower = " " + _clean(title).lower().replace("-", " ") + " "
    for group_name, words in _GROUP_KEYWORDS:
        for word in words:
            if word in lower:
                return group_name
    return None


def group(title, template_id=None, template_muscles=None):
    """Which display group an exercise belongs to ('bryst', …, 'kjerne', 'kondis' or None).

    Order of trust:
    1. HEVY's own primary muscle for the template (most precise), unless it is
       "full_body"/"other", which say nothing useful about where the load goes.
    2. Our table of known titles.
    3. Keywords in the title.
    """
    muscles = template_muscles or {}
    hevy_muscle = muscles.get(template_id) if template_id else None
    if hevy_muscle in MUSCLE_TO_GROUP:
        return MUSCLE_TO_GROUP[hevy_muscle]
    info = _known(title)
    if info:
        return MUSCLE_TO_GROUP.get(info["muscle"])
    return _group_from_keywords(title)


def muscle(title, template_id=None, template_muscles=None):
    """HEVY-style primary muscle (e.g. 'hamstrings'), or None when unknown."""
    muscles = template_muscles or {}
    if template_id and muscles.get(template_id):
        return muscles[template_id]
    info = _known(title)
    return info["muscle"] if info else None


def equipment_kind(title):
    """'dumbbell', 'barbell', 'bodyweight' or 'cardio' for an exercise title."""
    info = _known(title)
    if info:
        return info["equipment"]
    lower = _clean(title).lower()
    if _group_from_keywords(title) == "kondis":
        return "cardio"
    if "dumbbell" in lower or "goblet" in lower or "kettlebell" in lower:
        return "dumbbell"
    if "barbell" in lower or "ez bar" in lower or "z bar" in lower or "z-bar" in lower:
        return "barbell"
    return "bodyweight"


def is_cardio(exercise, template_muscles=None):
    """True when a HEVY exercise (dict with title/sets) is cardio.

    An exercise counts as cardio if its group is "kondis", or if any set has a distance.
    (Dead Hang only has a duration, so it is *not* cardio.)
    """
    if not isinstance(exercise, dict):
        return False
    if group(exercise.get("title"), exercise.get("exercise_template_id"), template_muscles) == "kondis":
        return True
    for s in exercise.get("sets") or []:
        if isinstance(s, dict) and (s.get("distance_meters") or 0) > 0:
            return True
    return False


def weight_cap(title):
    """The user's own upper limit for an exercise in kg, or None."""
    return WEIGHT_CAPS_KG.get(canonical_title(title))


def _rack_kind(title):
    """Which set of weights to round to. Bodyweight moves are loaded by holding dumbbells."""
    kind = equipment_kind(title)
    return "dumbbell" if kind == "bodyweight" else kind


def round_to_available(title, kg):
    """Round a wanted weight to the closest weight that actually exists (and respects caps).

    Examples: Goblet Squat 16.5 kg -> 16 kg; Romanian Deadlift (Barbell) 67 kg -> 67.5 kg;
    Romanian Deadlift (Barbell) 100 kg -> 90 kg (the user's cap). Ties round down, because
    a slightly lighter weight is the safer mistake. Returns None for cardio or bad input.
    """
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        return None
    if kg <= 0:
        return None
    kind = _rack_kind(title)
    if kind == "cardio":
        return None
    if kind == "dumbbell":
        best = min(DUMBBELLS_KG, key=lambda d: (abs(d - kg), d))
        result = float(best)
    else:
        steps = round(kg / BARBELL_STEP_KG - 1e-9)      # tiny nudge so exact halves round down
        result = max(BARBELL_MIN_KG, steps * BARBELL_STEP_KG)
    cap = weight_cap(title)
    if cap is not None:
        result = min(result, cap)
    return result


def next_weight_up(title, kg):
    """The next heavier weight that exists, or None if you are at the top (or at your cap).

    Dumbbells jump along the rack (…, 9, 10, 16, 22, 24 kg); barbells go up 2.5 kg.
    """
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        return None
    kind = _rack_kind(title)
    if kind == "cardio":
        return None
    if kind == "dumbbell":
        heavier = [float(d) for d in DUMBBELLS_KG if d > kg + 1e-9]
        candidate = heavier[0] if heavier else None
    else:
        candidate = max(BARBELL_MIN_KG, (int(kg / BARBELL_STEP_KG + 1e-9) + 1) * BARBELL_STEP_KG)
    cap = weight_cap(title)
    if candidate is not None and cap is not None and candidate > cap + 1e-9:
        return None
    return candidate


def next_weight_down(title, kg):
    """The next lighter weight that exists, or None if there is nothing lighter."""
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        return None
    kind = _rack_kind(title)
    if kind == "cardio":
        return None
    if kind == "dumbbell":
        lighter = [float(d) for d in DUMBBELLS_KG if d < kg - 1e-9]
        return lighter[-1] if lighter else None
    candidate = (int(kg / BARBELL_STEP_KG - 1e-9)) * BARBELL_STEP_KG
    if abs(candidate - kg) < 1e-9:
        candidate -= BARBELL_STEP_KG
    return candidate if candidate >= BARBELL_MIN_KG else None


def max_available(title):
    """The heaviest usable weight for an exercise (dumbbell rack top or cap), or None if unlimited."""
    cap = weight_cap(title)
    if cap is not None:
        return cap
    if _rack_kind(title) == "dumbbell":
        return float(DUMBBELLS_KG[-1])
    return None
