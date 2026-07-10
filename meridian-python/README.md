# Meridian (Python / Flask)

Same app as the static HTML build, but the Claude and YouTube API calls run
server-side instead of in the browser — your keys never leave the server or
appear in a network request the browser can see.

## Setup

```bash
cd meridian-python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# then edit .env and add your ANTHROPIC_API_KEY (required)
# and YOUTUBE_API_KEY (optional, enables "Find more videos")
```

## Run

```bash
python app.py
```

Open http://127.0.0.1:5000. The Settings page shows whether each key is
configured; "Re-check status" after editing `.env` and restarting.

## Layout

- `app.py` — Flask app: serves the frontend and three API routes
  (`/api/plan`, `/api/youtube-search`, `/api/status`, plus read-only
  `/api/courses` and `/api/classification`).
- `static/index.html` — the frontend (identical UI/UX to the standalone
  build, minus the client-side key inputs).
- `static/courses.json`, `static/classification.json` — the course
  directory data, served to the frontend and used server-side to build
  the AI Coach's prompt.
