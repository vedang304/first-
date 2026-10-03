import json
import logging
import os
import re

from flask import (
    Blueprint, current_app, jsonify, render_template,
    request, session,
)
from groq import Groq

# ---------------- BASE CONFIG ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICE_FILE = os.path.join(BASE_DIR, "service_chandigarh.json")
if not os.path.isfile(SERVICE_FILE):
    SERVICE_FILE = os.path.join(BASE_DIR, "service.json")

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")
logger = logging.getLogger(__name__)

# ---------------- LOAD SERVICES ----------------

def load_services():
    try:
        with open(SERVICE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("Service JSON must contain a list.")

        valid_services = [
            item for item in data
            if isinstance(item, dict) and item.get("name")
        ]
        logger.info("Loaded %d services", len(valid_services))
        return valid_services

    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Could not load service data")
        return []


services = load_services()

# ---------------- AI PROMPT ----------------

SYSTEM_PROMPT = """
You are CivicLoop, a helpful assistant for Chandigarh residents.
Reply in the same language as the user, using simple and concise language.
You can answer general questions, but do not pretend to be a government authority.

Do not invent official requirements, fees, deadlines, eligibility rules,
processing times, or website links. If information is uncertain, say so and
direct the user to verify it on the relevant official portal.

When answering from the local service dataset, treat it as general guidance
and remind the user to verify the latest details on the linked official portal.
Do not claim that an application has been submitted, approved, or booked.
"""

# ---------------- STATE DETECTION ----------------

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chandigarh", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu and Kashmir", "Ladakh",
]


def detect_state(message):
    message_lower = message.lower()

    for state in sorted(STATES, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(state.lower()) + r"(?!\w)"
        if re.search(pattern, message_lower):
            return state

    return None


# ---------------- SERVICE DETECTION ----------------

def detect_service(message):
    message_lower = message.lower()
    matches = []

    for service in services:
        name = str(service.get("name", "")).strip()
        keywords = service.get("keywords", [])

        if not isinstance(keywords, list):
            keywords = []

        terms = [name] + [str(keyword).strip() for keyword in keywords]

        for term in terms:
            if not term:
                continue

            pattern = r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)"
            if re.search(pattern, message_lower):
                matches.append((len(term), service))
                break

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


# ---------------- SERVICE ANSWER ----------------

def service_answer(service, state=None):
    name = str(service.get("name", "Government service"))
    service_state = str(service.get("state") or "Not specified").strip()

    central_ids = {
        "pan_card_chandigarh",
        "aadhaar_services_chandigarh",
        "voter_id_chandigarh",
        "passport_chandigarh",
    }
    service_id = str(service.get("id", ""))

    if state and state.lower() != "chandigarh" and service_id not in central_ids:
        return (
            f"The current CivicLoop service dataset has Chandigarh information "
            f"for {name}, not {state}. Please check the relevant state's official "
            "portal for its current requirements."
        )

    lines = [
        f"Service: {name}",
        f"Area: {service_state}",
        "",
        "Required documents (check the current official checklist):",
    ]

    documents = service.get("documents", [])
    if isinstance(documents, list) and documents:
        lines.extend(f"- {str(document)}" for document in documents)
    else:
        lines.append("- Please check the official portal for applicable documents.")

    process = service.get("process", [])
    if isinstance(process, list) and process:
        lines.extend(["", "General process:"])
        lines.extend(f"- {str(step)}" for step in process)

    fee = service.get("fee")
    if fee:
        lines.extend(["", f"Fee: {fee}"])

    processing_time = service.get("processing_time")
    if processing_time:
        lines.extend(["", f"Processing time: {processing_time}"])

    official_url = service.get("official_url")
    if official_url:
        lines.extend(["", f"Official portal: {official_url}"])

    notes = service.get("notes")
    if notes:
        lines.extend(["", f"Note: {notes}"])

    lines.extend([
        "",
        "Please verify the latest eligibility, documents, fees, and steps on the official portal.",
    ])

    return "\n".join(lines)


# ---------------- ASSISTANT PAGE ----------------

@ai_bp.route("/", methods=["GET"])
def assistant():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    return render_template("ai_assistant.html")


# ---------------- CHAT API ----------------

@ai_bp.route("/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Please send a valid JSON request."}), 400

    message = data.get("message", "")
    if not isinstance(message, str):
        return jsonify({"error": "Invalid message."}), 400

    message = message.strip()
    if not message:
        return jsonify({"error": "Please enter a message."}), 400

    if len(message) > 1000:
        return jsonify({"error": "Message is too long (max 1000 characters)."}), 400

    try:
        # Search local government service dataset first.
        service = detect_service(message)
        if service:
            answer = service_answer(service, detect_state(message))
            return jsonify({"answer": answer})

        # General questions use Groq.
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            logger.error("GROQ_API_KEY is not configured.")
            return jsonify({
                "error": "AI is not configured. Please check server settings."
            }), 503

        history = session.get("ai_history", [])
        if not isinstance(history, list):
            history = []

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
                {"role": "user", "content": message},
            ],
            max_tokens=300,
        )

        answer = (
            response.choices[0].message.content
            or "Sorry, I couldn't generate a response."
        )

        history.extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ])
        session["ai_history"] = history[-12:]
        session.modified = True

        return jsonify({"answer": answer})

    except Exception:
        logger.exception("AI assistant request failed")
        return jsonify({
            "error": "Something went wrong. Please try again later."
        }), 500


# ---------------- CLEAR CHAT HISTORY ----------------

@ai_bp.route("/clear", methods=["POST"])
def clear_chat():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    session.pop("ai_history", None)
    session.modified = True
    return jsonify({"message": "Chat history cleared."})