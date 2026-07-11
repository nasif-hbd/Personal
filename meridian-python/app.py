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

# Condensed once at import time — reused by /api/chat on every turn (cached server-side
# by Anthropic's prompt cache since it's an unchanging block; see cache_control below).
_CHAT_CATALOG = [
    {"id": c["id"], "subject": c["subject"], "level": c["level"], "title": c["title"], "platform": c["platform"], "cost": c["cost"]}
    for c in COURSES
]

CHAT_SYSTEM_PROMPT = (
    "You are Meridian's curriculum planner, chatting directly with a learner to build a personalized, "
    "sequential study plan drawn from the course catalog below. Ask brief, natural questions — one or two "
    "at a time, not a form — to learn: their age or grade/level, which subjects they want covered, what they "
    "want to get out of it, how many hours per week they can study, and how they'd like to be reminded "
    "(before each session, a daily digest, or off — and roughly what time of day). Keep replies short and "
    "conversational. Once you have enough to build a good plan, call create_learning_plan — do not call it "
    "prematurely. Only reference course_id values present in the catalog. Order milestones so foundational "
    "material precedes advanced material for the learner's level, and cover every subject they mentioned."
)

CREATE_PLAN_TOOL = {
    "name": "create_learning_plan",
    "description": "Create the learner's sequential study plan once you have their subjects, level, goal, weekly time budget, and reminder preference.",
    "input_schema": {
        "type": "object",
        "properties": {
            "learner_name": {"type": "string", "description": "The learner's name, if they gave one."},
            "weekly_hours": {"type": "integer", "description": "Recommended study hours per week."},
            "reminder_preference": {"type": "string", "enum": ["Before each session", "Daily digest", "Off"]},
            "reminder_time": {"type": "string", "description": "24-hour local time to send reminders, e.g. 18:00."},
            "summary": {"type": "string", "description": "2-3 sentence overview of the plan and why it's sequenced this way."},
            "notification_recommendation": {"type": "string", "description": "One sentence recommending when/how often to remind this learner."},
            "milestones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "week": {"type": "integer"},
                        "course_id": {"type": "integer", "description": "Must be one of the numeric ids in the catalog."},
                        "goal": {"type": "string"},
                    },
                    "required": ["week", "course_id", "goal"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["weekly_hours", "reminder_preference", "summary", "notification_recommendation", "milestones"],
        "additionalProperties": False,
    },
}


def finalize_milestones(raw_milestones):
    valid_ids = {c["id"] for c in COURSES}
    milestones = sorted(
        (dict(m) for m in (raw_milestones or []) if m.get("course_id") in valid_ids),
        key=lambda m: m["week"],
    )
    for m in milestones:
        m["done"] = False
    return milestones


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
    plan["milestones"] = finalize_milestones(plan.get("milestones"))
    return jsonify(plan)


@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not messages or not isinstance(messages, list):
        return jsonify({"error": "No messages provided."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "The server has no ANTHROPIC_API_KEY configured. Add one to .env and restart."}), 503

    system = [
        {"type": "text", "text": CHAT_SYSTEM_PROMPT},
        {"type": "text", "text": json.dumps(_CHAT_CATALOG), "cache_control": {"type": "ephemeral"}},
    ]

    client = anthropic_client()
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            thinking={"type": "adaptive"},
            tools=[CREATE_PLAN_TOOL],
            tool_choice={"type": "auto"},
            system=system,
            messages=messages,
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
        return jsonify({"type": "message", "reply": "Sorry, I can't help with that request."})

    tool_use = next((b for b in response.content if b.type == "tool_use" and b.name == "create_learning_plan"), None)
    if tool_use is not None:
        raw = tool_use.input or {}
        plan = {
            "summary": raw.get("summary", ""),
            "weekly_hours": raw.get("weekly_hours"),
            "milestones": finalize_milestones(raw.get("milestones")),
            "notification_recommendation": raw.get("notification_recommendation", ""),
            "learner_name": raw.get("learner_name", ""),
            "reminder_preference": raw.get("reminder_preference", "Before each session"),
            "reminder_time": raw.get("reminder_time", "18:00"),
        }
        return jsonify({"type": "plan", "plan": plan})

    text_block = next((b for b in response.content if b.type == "text"), None)
    reply = text_block.text if text_block else "Could you tell me a bit more?"
    return jsonify({"type": "message", "reply": reply})


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
