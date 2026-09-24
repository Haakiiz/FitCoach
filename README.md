# FitCoach HEVY Proxy

This repository contains a small FastAPI application that exposes a simplified API for the [HEVY](https://hevy.com) workout app. The proxy can be used by a Custom GPT ("fitcoach") to retrieve or log workout data on behalf of your account.

A detailed, step‑by‑step setup guide is provided in **HEVY_API_Instructions.txt**. The summary below explains how the proxy works and the basic steps to replicate the project on your own hardware.

## How It Works

- `hevy_proxy.py` implements a FastAPI server that forwards requests to the HEVY API using your personal API key. Endpoints include:
  - `GET /workouts` – retrieve recent workout history.
  - `POST /workouts` – create a workout entry.
  - `GET /exercise_templates/all` – return cached exercise template IDs.
- The same server also hosts a personal **dashboard** (see [Dashboard](#dashboard) below). These routes are hidden from `/openapi.json`, so the Custom GPT never sees them:
  - `GET /dashboard` – the dashboard web page.
  - `GET /dashboard/api` – the dashboard data as JSON (`?refresh=1` fetches fresh data instead of using the cache).
  - `POST /dashboard/weight` – save a weigh-in, e.g. `{"kg": 96.4, "date": "2026-09-24"}` (date is optional).
- The server expects your API key in a local `.hevy_env` file:
  ```
  HEVY_API_KEY=<YOUR_HEVY_API_KEY>
  ```
- `hevy_proxy.yaml` is an OpenAPI spec designed for the ChatGPT Actions interface. Point it to your running proxy and import it when configuring the custom GPT.

## Replicating the Project

1. **Clone this repository** on your Raspberry Pi or other machine.
2. **Create a Python environment** and install dependencies:
   ```sh
   python3 -m venv .env
   source .env/bin/activate
   pip install -r requirements.txt
   ```
3. **Add your HEVY API key** to a `.hevy_env` file in the project directory as shown above.
4. **Start the proxy**:
   ```sh
   uvicorn hevy_proxy:app --port 8000
   ```
5. Optionally, follow the instructions in `HEVY_API_Instructions.txt` to configure systemd services and an ngrok tunnel so the proxy runs continuously and is reachable over HTTPS.

For more detailed setup directions – including SSH access, systemd service files, and troubleshooting tips – see `HEVY_API_Instructions.txt`.

## Dashboard

The proxy also serves a live dashboard with your training, recovery and weight. It shows workouts from HEVY, sleep and body battery from Garmin (optional) and the weigh-ins you type in yourself.

### Open it

Start the proxy as usual (`uvicorn hevy_proxy:app --port 8000`) and open:

- on the Pi itself: <http://localhost:8000/dashboard>
- from your phone: `https://<your-ngrok-address>/dashboard?key=<DASHBOARD_TOKEN>`

The page caches data, so reloading is cheap: HEVY workouts are kept for 10 minutes and Garmin data for 30 minutes. The **Oppdater** button fetches fresh data.

### Protect it with DASHBOARD_TOKEN (strongly recommended)

Your ngrok address is public, so **anyone who knows it can see your dashboard** unless you set a token. Add a long random value to `.hevy_env`:

```
DASHBOARD_TOKEN=<a long random string>
```

You can make one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Restart the proxy afterwards.

Then open `/dashboard?key=<your token>` once. The browser gets a login cookie (valid for 90 days) and is sent on to `/dashboard`, so the key does not stay in the address bar. Without a valid key or cookie the page answers *401*. If no token is set, the dashboard is open and the proxy prints a warning when it starts.

### Connect Garmin (optional)

1. Add your Garmin Connect login to `.hevy_env` (this file is never committed):
   ```
   GARMIN_EMAIL=you@example.com
   GARMIN_PASSWORD=<your Garmin password>
   ```
2. Log in once from the terminal on the Pi, in the project folder with the virtual environment active:
   ```sh
   python -m dashboard.sources
   ```
   If your Garmin account uses two-step verification (MFA), this is where you type in the code Garmin sends you. The dashboard itself cannot ask for an MFA code, so this step is required when MFA is on.
3. The login tokens are saved in `~/.garminconnect` (change the folder with `GARMIN_TOKENSTORE`). After that, the dashboard logs in with the tokens and only uses the password if the tokens stop working.

Without `GARMIN_EMAIL` and `GARMIN_PASSWORD`, the dashboard simply shows "Garmin er ikke tilkoblet". If HEVY or Garmin cannot be reached, the page still loads and shows a short message about what failed.

### Weigh-ins

Use the weight form on the dashboard. Weigh-ins are stored in `weights.json` next to `hevy_proxy.py` (or the path in `WEIGHTS_PATH`). The weight must be between 30 and 250 kg and the date cannot be in the future. A new weigh-in on the same date replaces the old one. `weights.json` is personal, so it is listed in `.gitignore`.

### Demo mode (made-up data)

To try the dashboard without API keys or network calls:

```sh
FITCOACH_DEMO=1 uvicorn hevy_proxy:app --port 8000
```

Then open <http://localhost:8000/dashboard>. All data is synthetic. `FITCOACH_DEMO=comeback` shows what the page looks like after a long break (the last workout was 99 days ago). In demo mode, weigh-ins are kept in memory (or in `WEIGHTS_PATH` if you set it), and nothing is sent to HEVY or Garmin.

### Other settings

| Variable | Default | What it does |
|---|---|---|
| `DASHBOARD_TOKEN` | *(none)* | Access key for the dashboard (see above) |
| `DASHBOARD_NAME` | *(empty)* | Your name in the greeting, e.g. `Håkon` |
| `DASHBOARD_TZ` | `Europe/Oslo` | Time zone used for "today" and the greeting |
| `WEIGHTS_PATH` | `weights.json` | Where weigh-ins are stored |
| `GARMIN_TOKENSTORE` | `~/.garminconnect` | Where Garmin login tokens are stored |
| `FITCOACH_DEMO` | *(off)* | `1` or `comeback` for demo data |

### Tests

```sh
pip install pytest
pytest tests/
```

The tests run in demo mode with fake clients and never call HEVY or Garmin.
