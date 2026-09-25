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
  - `GET /dashboard/login`, `POST /dashboard/login` – a small login form (used when `DASHBOARD_TOKEN` is set).
  - `GET /dashboard/api` – the dashboard data as JSON. Send the header `X-FitCoach-Refresh: 1` to fetch fresh data instead of using the cache.
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
   After every `git pull`, run `pip install -r requirements.txt` again: new features (like the dashboard) can need new packages. If a dashboard package is missing, the proxy still starts and the GPT endpoints keep working, but it prints `[dashboard] DISABLED …` and `/dashboard` is not available.

   **Raspberry Pi note:** `garminconnect` needs `curl_cffi`, which ships ready-made packages for 64-bit systems. On the older 32-bit Raspberry Pi OS (`uname -m` prints `armv7l`) there may be no ready-made package, and `pip install` can fail while trying to build it. Use 64-bit Raspberry Pi OS (`uname -m` prints `aarch64`) if you want Garmin data. The rest of the proxy and the dashboard work without it.
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
- from anywhere (your phone on 4G): `https://<your-ngrok-address>/dashboard`. This needs `DASHBOARD_TOKEN`, see below.
- from another device on your home network: see the next section. It needs one extra setting.

#### From another device on your home network

By default uvicorn only listens on `127.0.0.1`, so only the Pi itself can connect (ngrok also runs on the Pi, so it still works). The systemd service in `HEVY_API_Instructions.txt` does the same. To reach the dashboard from your laptop or phone at home, e.g. `http://192.168.1.20:8000/dashboard`, uvicorn must listen on all network cards with `--host 0.0.0.0`:

```sh
uvicorn hevy_proxy:app --host 0.0.0.0 --port 8000
```

and in the systemd service file:

```
ExecStart=/home/<YOUR_PI_USER>/FitCoach/.env/bin/uvicorn hevy_proxy:app --host 0.0.0.0 --port 8000
```

**Be aware:** this makes the whole proxy reachable on your home network, including the GPT endpoints (`/workouts`), which can read and log workouts with your HEVY key and have no password. Only do this on a network you trust. Without `--host 0.0.0.0`, everything stays on the Pi and is reachable from outside only through ngrok.

Open the dashboard with the Pi's IP address or a name ending in `.local` (e.g. `http://raspberrypi.local:8000/dashboard`). If you use another name, for example `pi.hjemme` from your router, add it to `.hevy_env`, otherwise the dashboard answers "Ukjent adresse" (see the next section for why):

```
DASHBOARD_ALLOWED_HOSTS=pi.hjemme
```

The page caches data, so reloading is cheap: HEVY workouts are kept for 10 minutes and Garmin data for 30 minutes. The **Oppdater** button fetches fresh data.

### Who can see the dashboard?

Your ngrok address is public, so the dashboard is locked down by default:

| | Without `DASHBOARD_TOKEN` | With `DASHBOARD_TOKEN` |
|---|---|---|
| On the Pi / home network (direct, known host name; home network needs `--host 0.0.0.0`) | Open | Log in once |
| Through ngrok (internet) | Refused (the page explains how to set a token) | Log in once |
| Demo mode (`FITCOACH_DEMO`) | Open (only made-up data) | Log in once |

Without a token (and in demo mode) there is one more rule: the address in the browser must be `localhost`, an IP address, a name ending in `.local`, or a name listed in `DASHBOARD_ALLOWED_HOSTS` (comma-separated). This stops a trick called *DNS rebinding*, where a bad website points its own name at your Pi and reads the dashboard through your browser. Other names get *403 Ukjent adresse*. With `DASHBOARD_TOKEN` set, this rule is not needed and any name works.

"Direct" means the request comes from `127.0.0.1`, `::1` or a private home network (`10.x`, `172.16–31.x`, `192.168.x`) **and** has none of the headers that proxies and tunnels add (`X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `Forwarded`, `X-Real-IP`, `Via`, `CF-Connecting-IP`, `True-Client-IP`, `X-Client-IP`). ngrok always adds some of them, so requests through ngrok never count as local.

Other websites can't use your browser to change anything: saving a weigh-in only accepts real JSON (`Content-Type: application/json`), POST requests that another website sends (seen from the browser's `Origin` and `Sec-Fetch-Site` headers) are refused, and fresh data is only fetched when the request has the `X-FitCoach-Refresh: 1` header, which other websites cannot add. This matters most without a token, when access is based on your IP address.

### Set DASHBOARD_TOKEN (strongly recommended)

1. Make a long random key:
   ```sh
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Add it to `.hevy_env` and restart the proxy:
   ```
   DASHBOARD_TOKEN=<the long random key>
   ```
3. Open `/dashboard`. You are sent to a small login page (`/dashboard/login`). Paste the key and press **Logg inn**. The browser gets a login cookie that lasts 90 days.

The key is sent in the form (the request body), never in the address, so it stays out of uvicorn's access log and your browser history. Old links like `/dashboard?key=…` no longer log you in, and secrets such as `key=`, `token=`, `password=` or an `Authorization` value are shown as `***` in the access log. Wrong keys are slowed down per IP address (both in the login form and with `Authorization: Bearer`): the first 3 wrong keys are free (typos happen), then that address must wait 2, 4, 8 … seconds (at most 5 minutes) between tries and gets *429 Too Many Requests* until then. Other addresses are not affected, so a stranger can't lock you out. Be honest with yourself about the limits: someone with many IP addresses is not stopped by this. The real protection is that the key is long and random.

**The ngrok inspector can still see it.** ngrok's local web inspector (<http://127.0.0.1:4040> on the Pi) records full requests, including the login form body, the cookie and `Authorization` headers. Only people with access to the Pi can open it, but you can turn it off by adding `inspect: false` to the tunnel in your ngrok config (`ngrok config edit`), for example:

```yaml
tunnels:
  fitcoach:
    proto: http
    addr: 8000
    inspect: false
```

The cookie holds a fingerprint of the key (an HMAC), not the key itself. **To log out every browser** (for example if you lose your phone), change `DASHBOARD_TOKEN` in `.hevy_env` and restart the proxy.

For scripts or `curl`, send the key as a header instead:

```sh
curl -H "Authorization: Bearer <your key>" https://<your-ngrok-address>/dashboard/api

# the same, but skip the caches and fetch fresh data
curl -H "Authorization: Bearer <your key>" -H "X-FitCoach-Refresh: 1" https://<your-ngrok-address>/dashboard/api
```

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

To protect your Garmin account, the dashboard is careful:

- Only one Garmin download runs at a time. It fetches up to 4 days at once. If it takes longer than 45 seconds, the page stops waiting while the download finishes in the background, and the next page load uses the result. While it runs, no new download is started, not even with **Oppdater**. A download that hangs for more than 10 minutes is given up, so a new one can start.
- After a Garmin error it waits 20 minutes before asking Garmin again (unless you press **Oppdater**).
- While Garmin is busy or failing, the page shows the last good Garmin data. If that data is more than 30 minutes old, the page says how old it is (for example "Viser Garmin-data fra 2 timer og 5 minutter siden."). Data older than 24 hours is not shown.

HEVY errors are remembered for 2 minutes too, so a HEVY outage doesn't mean a new download attempt on every page load. **Oppdater** tries again straight away.
- If a login with your e-mail and password fails (wrong password, MFA, "too many requests"), it does **not** try the password again by itself, because repeated attempts can send MFA e-mails or lock the account. The saved tokens are still tried. Press **Oppdater** or restart the proxy to allow one new password login. **Oppdater** can do this at most once every 10 minutes. If MFA is the problem, run `python -m dashboard.sources` again.

### Weigh-ins

Use the weight form on the dashboard. Weigh-ins are stored in `weights.json` next to `hevy_proxy.py` (or the path in `WEIGHTS_PATH`). A typed weight must look like `96`, `96,4` or `96.45`. The weight must be between 30 and 250 kg, and the date must be in the year 2000 or later and not in the future. A new weigh-in on the same date replaces the old one. `weights.json` is personal, so it is listed in `.gitignore`.

### Demo mode (made-up data)

To try the dashboard without API keys or network calls:

```sh
FITCOACH_DEMO=1 uvicorn hevy_proxy:app --port 8000
```

Then open <http://localhost:8000/dashboard>. All data is synthetic. `FITCOACH_DEMO=comeback` shows what the page looks like after a long break (the last workout was 99 days ago). In demo mode, weigh-ins are kept in memory only (never written to disk, not even to `WEIGHTS_PATH`), and nothing is sent to HEVY or Garmin. The GPT endpoints (`/workouts`) answer *503* in demo mode when no `HEVY_API_KEY` is set.

### Other settings

| Variable | Default | What it does |
|---|---|---|
| `DASHBOARD_TOKEN` | *(none)* | Access key for the dashboard. Without it, only direct local requests get in (see above) |
| `DASHBOARD_NAME` | *(empty)* | Your name in the greeting, e.g. `Håkon` |
| `DASHBOARD_TZ` | `Europe/Oslo` | Time zone used for "today" and the greeting |
| `DASHBOARD_ALLOWED_HOSTS` | *(empty)* | Extra host names allowed without a token, comma-separated (e.g. `pi.hjemme`) |
| `WEIGHTS_PATH` | `weights.json` | Where weigh-ins are stored (not used in demo mode) |
| `GARMIN_TOKENSTORE` | `~/.garminconnect` | Where Garmin login tokens are stored |
| `FITCOACH_DEMO` | *(off)* | `1` or `comeback` for demo data |

### Tests

```sh
pip install pytest
pytest tests/
```

The tests run in demo mode with fake clients and never call HEVY or Garmin.
