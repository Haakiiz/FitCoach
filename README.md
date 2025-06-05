# FitCoach HEVY Proxy

This repository contains a small FastAPI application that exposes a simplified API for the [HEVY](https://hevy.com) workout app. The proxy can be used by a Custom GPT ("fitcoach") to retrieve or log workout data on behalf of your account.

A detailed, step‑by‑step setup guide is provided in **HEVY_API_Instructions.txt**. The summary below explains how the proxy works and the basic steps to replicate the project on your own hardware.

## How It Works

- `hevy_proxy.py` implements a FastAPI server that forwards requests to the HEVY API using your personal API key. Endpoints include:
  - `GET /workouts` – retrieve recent workout history.
  - `POST /workouts` – create a workout entry.
  - `GET /exercise_templates/all` – return cached exercise template IDs.
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
