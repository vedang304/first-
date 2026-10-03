import json
import logging
import os
import re
import uuid
from pathlib import Path

from flask import (
    Blueprint, current_app, jsonify, render_template, request, session
)
from werkzeug.utils import secure_filename
from groq import Groq

BASE_DIR = Path(__file__).resolve().parent
SERVICE_FILE = BASE_DIR / "service_chandigarh.json"
if not SERVICE_FILE.is_file():
    SERVICE_FILE = BASE_DIR / "service.json"

UPLOAD_FOLDER = BASE_DIR / "uploads" / "ocr"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "pdf"}
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024

ocr_bp = Blueprint("ocr", __name__, url_prefix="/ocr")
logger = logging.getLogger(__name__)

_ocr_engine = None


def load_services():
    """Load the local service catalogue without preventing Flask startup."""
    try:
        with SERVICE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            raise ValueError("Service catalogue must be a JSON list.")
        return [
            item for item in data
            if isinstance(item, dict) and item.get("name")
        ]
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Could not load OCR service catalogue from %s", SERVICE_FILE)
        return []


def get_ocr_engine():
    """Initialize PaddleOCR only when the first document is submitted."""
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR
        _ocr_engine = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _ocr_engine


def allowed_file(filename):
    return (
        isinstance(filename, str)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def mask_sensitive_text(text):
    """Mask 12-digit Aadhaar-like sequences before sending OCR text to AI."""
    return re.sub(
        r"(?<!\d)(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)",
        r"XXXX XXXX \3",
        str(text),
    )


def find_service(service_name, state=None):
    """Match a service by its exact name or ID, optionally scoped by state."""
    wanted = str(service_name or "").strip().casefold()
    wanted_state = str(state or "").strip().casefold()
    services = load_services()

    matches = []
    for service in services:
        name = str(service.get("name", "")).strip()
        service_id = str(service.get("id", "")).strip()
        service_state = str(service.get("state", "")).strip().casefold()
        if wanted not in {name.casefold(), service_id.casefold()}:
            continue
        if wanted_state and service_state not in {
            wanted_state, "all india", "india", "central"
        }:
            continue
        matches.append(service)

    # Prefer the requested local state over a central/all-India entry.
    if wanted_state:
        for item in matches:
            if str(item.get("state", "")).strip().casefold() == wanted_state:
                return item
    return matches[0] if matches else None


def extract_text(file_path):
    """Run PaddleOCR and return recognized text in reading-result order."""
    engine = get_ocr_engine()
    results = engine.predict(str(file_path))
    extracted = []

    for result in results or []:
        data = getattr(result, "json", {})
        if callable(data):
            data = data()
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            continue

        payload = data.get("res", data)
        if not isinstance(payload, dict):
            continue

        texts = payload.get("rec_texts", [])
        if isinstance(texts, list):
            extracted.extend(str(value) for value in texts if value)

    return "\n".join(extracted).strip()


def parse_ai_json(raw):
    """Parse JSON from the model, tolerating a surrounding Markdown fence."""
    if not isinstance(raw, str):
        raise ValueError("AI returned an empty response.")
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI response did not contain JSON.")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI response JSON must be an object.")
    return value


def classify_document(extracted_text, required_documents):
    """Use Groq to suggest a document match; this is not authenticity checking."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    # Mask common Aadhaar-like numbers before any text leaves the server.
    safe_text = mask_sensitive_text(extracted_text)[:12000]
    requirements = [str(item) for item in required_documents]

    prompt = f"""
Classify the uploaded document using only the OCR text below.
Required document options:
{json.dumps(requirements, ensure_ascii=False)}

OCR text:
{safe_text}

Return only a JSON object with these keys:
{{
  "document_type": "short detected type or Unknown",
  "matched_requirement": "one exact item from required document options or null",
  "status": "Matched or Needs Review",
  "confidence": "High, Medium, or Low",
  "important_information": ["short visible details only"],
  "reason": "brief explanation"
}}

Rules:
- matched_requirement must be exactly one of the listed options, or null.
- Use Needs Review if the text is unclear or no match is supported.
- Never claim the document is genuine, legally valid, or officially verified.
- Do not infer missing information. Keep the response concise.
"""
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[
            {
                "role": "system",
                "content": "You classify document text and return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=500,
    )
    raw = response.choices[0].message.content or ""
    result = parse_ai_json(raw)

    # Enforce safe, predictable values from model output.
    matched = result.get("matched_requirement")
    if matched not in requirements:
        matched = None
    status = result.get("status")
    if status not in {"Matched", "Needs Review"} or not matched:
        status = "Needs Review"
    confidence = result.get("confidence")
    if confidence not in {"High", "Medium", "Low"}:
        confidence = "Low"
    important = result.get("important_information", [])
    if not isinstance(important, list):
        important = []
    important = [mask_sensitive_text(str(x))[:300] for x in important[:8]]

    return {
        "document_type": str(result.get("document_type") or "Unknown")[:120],
        "matched_requirement": matched,
        "status": status,
        "confidence": confidence,
        "important_information": important,
        "reason": str(result.get("reason") or "")[:500],
    }


def build_validation(required_documents, uploaded_results):
    matched_documents = []
    needs_review = []

    for result in uploaded_results:
        matched = result.get("matched_requirement")
        if matched in required_documents and result.get("status") == "Matched":
            if matched not in matched_documents:
                matched_documents.append(matched)
        else:
            needs_review.append(result.get("filename", "Uploaded file"))

    missing_documents = [
        item for item in required_documents if item not in matched_documents
    ]
    total = len(required_documents)
    completed = len(matched_documents)
    percentage = round(completed / total * 100) if total else 0

    if total and completed == total:
        overall_status = "All required documents matched"
    elif completed:
        overall_status = "Documents remaining"
    else:
        overall_status = "No required documents matched"

    return {
        "overall_status": overall_status,
        "completed_documents": completed,
        "total_documents": total,
        "completion_percentage": percentage,
        "matched_documents": matched_documents,
        "missing_documents": missing_documents,
        "needs_review": needs_review,
    }


@ocr_bp.route("/", methods=["GET"])
def ocr_home():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    services = load_services()
    return render_template("ocr_upload.html", services=services)


@ocr_bp.route("/validate-documents", methods=["POST"])
def validate_documents():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Please log in first."}), 401

    service_name = request.form.get("service_name", "").strip()
    state = request.form.get("state", "").strip() or None
    files = request.files.getlist("files")

    if not service_name:
        return jsonify({"success": False, "error": "Select a government service."}), 400
    if not files or all(not f.filename for f in files):
        return jsonify({"success": False, "error": "Upload at least one file."}), 400
    if len(files) > MAX_FILES:
        return jsonify({"success": False, "error": f"Upload up to {MAX_FILES} files at a time."}), 400

    service = find_service(service_name, state)
    if not service:
        return jsonify({
            "success": False,
            "error": "Service not found in the local service catalogue.",
        }), 404

    required_documents = service.get("documents", [])
    if not isinstance(required_documents, list) or not required_documents:
        return jsonify({
            "success": False,
            "error": "No required documents are listed for this service.",
        }), 400

    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    uploaded_results = []
    saved_paths = []

    try:
        for upload in files:
            original_name = secure_filename(upload.filename or "")
            if not original_name or not allowed_file(original_name):
                return jsonify({
                    "success": False,
                    "error": "Use PNG, JPG, JPEG, WEBP, BMP, TIFF, or PDF files.",
                }), 400

            # Flask's configured request limit is 10 MB; enforce a per-file cap too.
            content = upload.read(MAX_FILE_BYTES + 1)
            if not content:
                return jsonify({"success": False, "error": f"{original_name} is empty."}), 400
            if len(content) > MAX_FILE_BYTES:
                return jsonify({
                    "success": False,
                    "error": f"{original_name} exceeds the 10 MB per-file limit.",
                }), 413

            suffix = Path(original_name).suffix.lower()
            stored_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex}{suffix}"
            stored_path.write_bytes(content)
            saved_paths.append(stored_path)

            try:
                text = extract_text(stored_path)
            except Exception:
                logger.exception("OCR failed for uploaded document")
                uploaded_results.append({
                    "filename": original_name,
                    "document_type": "Unknown",
                    "matched_requirement": None,
                    "status": "Needs Review",
                    "confidence": "Low",
                    "important_information": [],
                    "reason": "OCR could not read this file. Try a clearer image or PDF.",
                })
                continue

            if not text:
                uploaded_results.append({
                    "filename": original_name,
                    "document_type": "Unknown",
                    "matched_requirement": None,
                    "status": "Needs Review",
                    "confidence": "Low",
                    "important_information": [],
                    "reason": "No readable text was detected.",
                })
                continue

            try:
                result = classify_document(text, required_documents)
            except Exception:
                logger.exception("AI classification failed")
                result = {
                    "document_type": "Unknown",
                    "matched_requirement": None,
                    "status": "Needs Review",
                    "confidence": "Low",
                    "important_information": [],
                    "reason": "AI analysis was unavailable; please review manually.",
                }

            result["filename"] = original_name
            uploaded_results.append(result)

        validation = build_validation(required_documents, uploaded_results)
        return jsonify({
            "success": True,
            "service": {
                "name": service.get("name", service_name),
                "state": service.get("state", "Not specified"),
            },
            "required_documents": required_documents,
            "uploaded_documents": uploaded_results,
            "validation": validation,
            "notice": (
                "This is an AI-assisted text match only. It does not verify "
                "document authenticity or replace official review."
            ),
        })
    finally:
        # Uploaded files are temporary and removed after processing.
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary OCR upload: %s", path)
