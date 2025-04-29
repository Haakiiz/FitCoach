from dotenv import load_dotenv
import os
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, status
import httpx

# ─── Load environment ───
load_dotenv(dotenv_path=".hevy_env")

HEVY_API_KEY = os.getenv("HEVY_API_KEY")
if not HEVY_API_KEY:
    raise RuntimeError("Missing HEVY_API_KEY; check your .hevy_env file")

HEVY_API_URL = "https://api.hevyapp.com/v1"

# ─── Setup FastAPI ───
app = FastAPI()

# ─── Template Caching ───
TEMPLATE_CACHE_PATH = Path(__file__).with_name("exercise_cache.json")
TEMPLATE_URL = f"{HEVY_API_URL}/exercise_templates"
TEMPLATE_CACHE: dict[str, str] = {}

async def refresh_templates():
    """Pull all exercise templates from Hevy and cache them."""
    page = 1
    query = {"page_size": 100}  # MAX 100 is allowed by Hevy
    cache: dict[str, str] = {}

    async with httpx.AsyncClient(
        headers={"api-key": HEVY_API_KEY},
        timeout=10,
    ) as client:
        while True:
            resp = await client.get(TEMPLATE_URL, params={**query, "page": page})
            if resp.status_code == 404:
                break  # no more pages
            resp.raise_for_status()
            chunk = resp.json().get("exercise_templates", [])
            if not chunk:
                break
            for tpl in chunk:
                cache[tpl["title"].lower()] = tpl["id"]
            page += 1

    TEMPLATE_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    TEMPLATE_CACHE.clear()
    TEMPLATE_CACHE.update(cache)
    print(f"[templates] cached {len(cache)} exercise templates.")

@app.on_event("startup")
async def startup_event():
    await refresh_templates()

    async def _ticker():
        while True:
            await asyncio.sleep(86_400)  # 24 hours
            try:
                await refresh_templates()
            except Exception as e:
                print("[templates] refresh failed:", e)
    asyncio.create_task(_ticker())

@app.get("/exercise_templates/all", operation_id="allTemplates")
async def all_templates():
    """Return the cached exercise templates under 'templates' key."""
    return {"templates": TEMPLATE_CACHE}


@app.get("/workouts", operation_id="getRecentWorkouts")
async def get_workouts(
        page: int = 1,
        pageSize: int = 10,
        since: str | None = None
):
    """Retrieve past workouts."""
    if pageSize < 1:
        pageSize = 1
    elif pageSize > 10:
        pageSize = 10

    params = {"page": page, "pageSize": pageSize}
    if since:
        params["since"] = since

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{HEVY_API_URL}/workouts",
            headers={"api-key": HEVY_API_KEY, "accept": "application/json"},
            params=params,
        )
    if resp.is_error:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.post("/workouts", status_code=status.HTTP_201_CREATED, operation_id="logWorkout")
async def log_workout(request: Request):
    """Create a workout in Hevy."""
    payload = await request.json()

    try:
        for ex in payload["workout"]["exercises"]:
            # 1. Inject exercise_template_id if missing
            if "exercise_template_id" not in ex or not ex["exercise_template_id"]:
                ex_id = TEMPLATE_CACHE.get(ex["title"].lower())
                if not ex_id:
                    raise ValueError(f"No template ID for '{ex['title']}'")
                ex["exercise_template_id"] = ex_id

            # 2. Remove forbidden fields at exercise level
            for forbidden in ("index", "title", "supersets_id"):
                if forbidden in ex:
                    del ex[forbidden]

            # 3. Clean each set
            if "sets" in ex:
                for s in ex["sets"]:
                    if "index" in s:
                        del s["index"]

    except (KeyError, TypeError) as err:
        raise HTTPException(status_code=400, detail=f"Malformed workout JSON: {err}")
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{HEVY_API_URL}/workouts",
            json=payload,
            headers={
                "api-key": HEVY_API_KEY,
                "content-type": "application/json",
            },
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
