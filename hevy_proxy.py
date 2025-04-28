from dotenv import load_dotenv
import os
from fastapi import FastAPI, HTTPException
import httpx

# ─── Load environment from your custom file ───
load_dotenv(dotenv_path=".hevy_env")   # or whatever you renamed .env2 to

# ─── Grab the key into a Python variable ───
API_KEY = os.getenv("HEVY_API_KEY")
if not API_KEY:
    raise RuntimeError("Missing HEVY_API_KEY; check your .hevy_env file")

app = FastAPI()
BASE = "https://api.hevyapp.com/v1"


@app.get("/workouts")
async def get_workouts(
    page: int = 1,
    pageSize: int = 10,              # ← set default to 10 (max allowed)
    since: str | None = None
):
    # force it in range 1…10
    if pageSize < 1:
        pageSize = 1
    elif pageSize > 10:
        pageSize = 10

    params = {"page": page, "pageSize": pageSize}
    if since:
        params["since"] = since
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{BASE}/workouts",
            headers={"api-key": API_KEY, "accept": "application/json"},
            params=params
        )
    if resp.is_error:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()

