
import json
import os
import re
import logging
from flask import current_app
from flask import (Blueprint,render_template,request,jsonify,session,current_app)
from groq import Groq

# ---------------- BASE CONFIG ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_FILE = os.path.join(BASE_DIR, "service.json")

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- LOAD SERVICES ----------------

try:
    with open(SERVICE_FILE, "r", encoding="utf-8") as file:
        services = json.load(file)

    if not isinstance(services, list):
        raise ValueError("service.json must contain a JSON list")

except (OSError, json.JSONDecodeError, ValueError):
    logger.exception("Could not load service.json")
    services = []


# ---------------- STATES ----------------

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu and Kashmir", "Ladakh"
]


# ---------------- AI PROMPT ----------------

SYSTEM_PROMPT = """
You are a helpful Indian government services assistant.
Reply in the same language as the user, using simple language
and concise answers.

Do not invent official requirements, fees, deadlines,
or website links. If unsure, say so and direct the user
to verify details on the relevant official portal.

For government services, clearly distinguish general
guidance from verified official information.
"""


# ---------------- DETECT STATE ----------------

def detect_state(message):
    message = message.lower()

    for state in STATES:
        pattern = r"\b" + re.escape(state.lower()) + r"\b"

        if re.search(pattern, message):
            return state

    return None


# ---------------- DETECT SERVICE ----------------

def detect_service(message):
    message = message.lower()

    for service in services:
        if not isinstance(service, dict):
            continue

        name = str(service.get("name", ""))
        keywords = service.get("keywords", [])

        if not isinstance(keywords, list):
            keywords = []

        terms = [name] + keywords

        for term in terms:
            term = str(term).strip().lower()

            if not term:
                continue

            pattern = r"\b" + re.escape(term) + r"\b"

            if re.search(pattern, message):
                return service

    return None


# ---------------- SERVICE ANSWER ----------------

def service_answer(service, state):
    name = str(service.get("name", "Government service"))
    service_state = str(service.get("state") or "Not specified")

    if state:
        matches = [
            item for item in services
            if isinstance(item, dict)
            and str(item.get("name", "")).lower() == name.lower()
            and str(item.get("state") or "").lower() == state.lower()
        ]

        if matches:
            service = matches[0]
            service_state = str(service.get("state") or state)

        elif service_state.lower() != "all india":
            return (
                f"I couldn't find {name} for {state} in the current "
                "service dataset. Please verify it on the official portal."
            )

    elif service_state.lower() == "all india":
        matches = [
            item for item in services
            if isinstance(item, dict)
            and str(item.get("name", "")).lower() == name.lower()
        ]

        if len(matches) > 1:
            return (
                f"{name} may vary by state. "
                "Which state are you applying in?"
            )

    lines = [
        f"Service: {name}",
        f"State: {service_state}",
        "",
        "Required documents:"
    ]

    docs = service.get("documents", [])

    if isinstance(docs, list) and docs:
        lines.extend(f"- {doc}" for doc in docs)
    else:
        lines.append(
            "- Check the official portal for applicable documents."
        )

    process = service.get("process", [])

    if isinstance(process, list) and process:
        lines.extend(["", "General process:"])
        lines.extend(f"- {step}" for step in process)

    fee = service.get("fee")

    if fee is not None:
        lines.extend(["", f"Fee: {fee}"])

    processing_time = service.get("processing_time")

    if processing_time:
        lines.extend(["", f"Processing time: {processing_time}"])

    official_url = service.get("official_url")

    if official_url:
        lines.extend(["", f"Portal: {official_url}"])

    lines.extend([
        "",
        "Please verify current requirements on the official portal."
    ])

    return "\n".join(lines)


# ---------------- ASSISTANT PAGE ----------------

@ai_bp.route("/")
def assistant():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    source, filename, _ = current_app.jinja_env.loader.get_source(
        current_app.jinja_env,
        "ai_assistant.html"
    )

    print("HTML FILE:", filename)
    print("MIC BUTTON EXISTS:", "mic-btn" in source)

    return render_template("ai_assistant.html")


# ---------------- CHAT API ----------------

@ai_bp.route("/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({
            "error": "Please log in first."
        }), 401

    data = request.get_json(silent=True) or {}
    message = data.get("message", "")

    if not isinstance(message, str):
        return jsonify({
            "error": "Invalid message."
        }), 400

    message = message.strip()

    if not message:
        return jsonify({
            "error": "Please enter a message."
        }), 400

    if len(message) > 1000:
        return jsonify({
            "error": "Message is too long (max 1000 characters)."
        }), 400

    try:
        # First check local government service dataset.
        service = detect_service(message)

        if service:
            answer = service_answer(
                service,
                detect_state(message)
            )

            return jsonify({"answer": answer})

        # Otherwise use Groq AI.
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            return jsonify({
                "error": "AI is not configured. Please check server settings."
            }), 503

        history = session.get("ai_history", [])

        if not isinstance(history, list):
            history = []

        # Keep only recent valid messages.
        history = [
            item for item in history[-12:]
            if isinstance(item, dict)
            and item.get("role") in ("user", "assistant")
            and isinstance(item.get("content"), str)
        ]

        client = Groq(api_key=api_key)

        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *history,
                {"role": "user", "content": message}
            ],
            max_tokens=300
        )

        answer = (
            response.choices[0].message.content
            or "Sorry, I couldn't generate a response."
        )

        # Store recent chat history in session.
        history.extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer}
        ])

        session["ai_history"] = history[-12:]

        return jsonify({"answer": answer})

    except Exception:
        logger.exception("AI assistant request failed")

        return jsonify({
            "error": "Something went wrong. Please try again later."
        }), 500