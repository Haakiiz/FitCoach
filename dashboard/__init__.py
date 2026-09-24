"""FitCoach dashboard.

A small live web page (served by the same FastAPI app as `hevy_proxy.py`) that shows
training history from HEVY, recovery data from Garmin and your weigh-ins.

Files in this package:
- sources.py    – fetches data from HEVY, Garmin and the local weights.json file
- demo_data.py  – synthetic (fake) data used when FITCOACH_DEMO is set
- analytics.py  – turns the raw data into the numbers shown on the page
- routes.py     – the web routes (/dashboard, /dashboard/api, /dashboard/weight)
- SCHEMA.md     – the data contract between the Python code and the HTML page
"""
