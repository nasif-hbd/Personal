import json
import os
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
COURSES = json.loads((STATIC_DIR / "courses.json").read_text(encoding="utf-8"))

ANTHROPIC_MODEL = "claude-opus-4-8"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

_client = None


def anthropic_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentence overview of the plan and why it's sequenced this way.",
        },
        "weekly_hours": {
            "type": "integer",
            "description": "Recommended study hours per week, close to the learner's stated budget.",
        },
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "week": {"type": "integer", "description": "1-indexed week number in the plan."},
                    "course_id": {"type": "integer", "description": "Must be one of the numeric ids provided in the catalog."},
                    "goal": {"type": "string", "description": "One sentence: what to accomplish with this resource this week."},
                },
                "required": ["week", "course_id", "goal"],
                "additionalProperties": False,
            },
        },
        "notification_recommendation": {
            "type": "string",
            "description": "One sentence recommending when/how often to be reminded, tailored to the learner's stated preference.",
        },
    },
    "required": ["summary", "weekly_hours", "milestones", "notification_recommendation"],
    "additionalProperties": False,
}

PLAN_SYSTEM_PROMPT = (
    "You are the curriculum planner inside a personal learning app called Meridian. "
    "Given a learner's profile and a catalog of available courses, build a sequential, week-by-week study plan. "
    "Only reference course_id values that appear in the catalog provided — never invent one. "
    "Order resources so foundational material comes before advanced material, matching the learner's stated level. "
    "Keep the number of milestones realistic for the learner's weekly time budget "
    "(roughly 1 milestone per subject every 1-2 weeks, not one per course). "
    "Cover every subject the learner picked at least once."
)


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/status")
def status():
    return jsonify({
        "anthropicConfigured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "youtubeConfigured": bool(os.environ.get("YOUTUBE_API_KEY")),
    })


@app.get("/api/courses")
def courses():
    return jsonify(COURSES)


@app.get("/api/classification")
def classification():
    return jsonify(json.loads((STATIC_DIR / "classification.json").read_text(encoding="utf-8")))


@app.post("/api/plan")
def build_plan():
    body = request.get_json(silent=True) or {}
    learner = body.get("learner") or {}
    subjects = body.get("subjects") or []

    if not subjects:
        return jsonify({"error": "Pick at least one subject."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "The server has no ANTHROPIC_API_KEY configured. Add one to .env and restart."}), 503

    pool = [
        {"id": c["id"], "subject": c["subject"], "level": c["level"], "title": c["title"], "platform": c["platform"], "cost": c["cost"]}
        for c in COURSES
        if c["subject"] in subjects
    ]

    user_text = json.dumps({"learner": learner, "catalog": pool})

    client = anthropic_client()
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.RateLimitError:
        return jsonify({"error": "Rate limited by Anthropic — try again in a moment."}), 429
    except anthropic.AuthenticationError:
        return jsonify({"error": "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env."}), 401
    except anthropic.APIStatusError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    except anthropic.APIConnectionError:
        return jsonify({"error": "Couldn't reach Anthropic's API. Check your network connection."}), 502

    if response.stop_reason == "refusal":
        return jsonify({"error": "Claude declined to generate a plan for this request."}), 422

    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        return jsonify({"error": "No plan came back — try again."}), 502

    plan = json.loads(text_block.text)

    valid_ids = {c["id"] for c in COURSES}
    plan["milestones"] = sorted(
        (m for m in plan.get("milestones", []) if m.get("course_id") in valid_ids),
        key=lambda m: m["week"],
    )
    for m in plan["milestones"]:
        m["done"] = False

    return jsonify(plan)


@app.get("/api/youtube-search")
def youtube_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Missing search query."}), 400
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return jsonify({"error": "The server has no YOUTUBE_API_KEY configured. Add one to .env and restart."}), 503

    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "snippet", "type": "video", "maxResults": 8, "safeSearch": "strict", "q": query, "key": key},
            timeout=10,
        )
    except requests.RequestException:
        return jsonify({"error": "Couldn't reach YouTube's API."}), 502

    if not resp.ok:
        message = "HTTP " + str(resp.status_code)
        try:
            message = resp.json().get("error", {}).get("message", message)
        except ValueError:
            pass
        return jsonify({"error": message}), resp.status_code

    items = resp.json().get("items", [])
    results = [
        {
            "videoId": it["id"]["videoId"],
            "title": it["snippet"]["title"],
            "channel": it["snippet"]["channelTitle"],
            "thumb": (it["snippet"].get("thumbnails", {}).get("medium") or it["snippet"].get("thumbnails", {}).get("default") or {}).get("url"),
        }
        for it in items
    ]
    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
