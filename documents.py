
import os
import uuid
import pytesseract
from PIL import Image
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, send_from_directory, abort
)
from werkzeug.utils import secure_filename
from db import get_connection

documents_bp = Blueprint("documents", __name__, url_prefix="/documents")

UPLOAD_FOLDER = os.path.join("uploads", "documents")
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def current_user_id():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return user_id


def get_user_document(document_id, user_id):
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, user_id, original_name, stored_name, uploaded_at
            FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user_id)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


@documents_bp.route("/")
def documents_home():
    if not current_user_id():
        return redirect(url_for("login"))
    return render_template("documents.html", mode="home")


@documents_bp.route("/upload", methods=["GET", "POST"])
def upload_document():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    error = None
    success = None

    if request.method == "POST":
        file = request.files.get("document")

        if not file or not file.filename:
            error = "Please choose a document."
        elif not allowed_file(file.filename):
            error = "Only PDF, JPG, JPEG and PNG files are allowed."
        else:
            file_bytes = file.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                error = "File size must be 10 MB or less."
            elif not file_bytes:
                error = "The selected file is empty."
            else:
                original_name = secure_filename(file.filename)
                extension = original_name.rsplit(".", 1)[1].lower()
                stored_name = f"{uuid.uuid4().hex}.{extension}"
                file_path = os.path.join(UPLOAD_FOLDER, stored_name)

                try:
                    with open(file_path, "wb") as saved_file:
                        saved_file.write(file_bytes)

                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO documents
                            (user_id, original_name, stored_name, uploaded_at)
                        VALUES (%s, %s, %s, NOW())
                        """,
                        (user_id, original_name, stored_name)
                    )
                    conn.commit()
                    success = "Document uploaded successfully."

                except Exception:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    error = "Upload failed. Please try again."
                finally:
                    try:
                        cursor.close()
                        conn.close()
                    except (NameError, UnboundLocalError):
                        pass

    return render_template(
        "documents.html",
        mode="upload",
        error=error,
        success=success
    )


@documents_bp.route("/files")
def my_files():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, original_name, stored_name, uploaded_at
            FROM documents
            WHERE user_id = %s
            ORDER BY uploaded_at DESC
            """,
            (user_id,)
        )
        documents = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "documents.html",
        mode="files",
        documents=documents
    )


@documents_bp.route("/<int:document_id>/view")
def view_document(document_id):
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    document = get_user_document(document_id, user_id)
    if not document:
        abort(404)

    return send_from_directory(
        UPLOAD_FOLDER,
        document["stored_name"],
        as_attachment=False
    )


@documents_bp.route("/<int:document_id>/delete", methods=["POST"])
def delete_document(document_id):
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    document = get_user_document(document_id, user_id)
    if not document:
        abort(404)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM documents WHERE id = %s AND user_id = %s",
            (document_id, user_id)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    file_path = os.path.join(UPLOAD_FOLDER, document["stored_name"])
    if os.path.isfile(file_path):
        os.remove(file_path)

    return redirect(url_for("documents.my_files"))



@documents_bp.route("/scan", methods=["GET"])
def scan_document():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    return render_template("documents.html", mode="scan")


@documents_bp.route("/scan/save", methods=["POST"])
def save_scanned_document():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    file = request.files.get("scanned_document")

    if not file or not file.filename:
        abort(400, description="No scanned image received.")

    if file.mimetype != "image/jpeg":
        abort(400, description="Only JPEG scans are supported.")

    file_bytes = file.read()
    if not file_bytes or len(file_bytes) > MAX_FILE_SIZE:
        abort(400, description="Image is empty or exceeds 10 MB.")

    original_name = f"Scanned_Document_{uuid.uuid4().hex[:8]}.jpg"
    stored_name = f"{uuid.uuid4().hex}.jpg"
    file_path = os.path.join(UPLOAD_FOLDER, stored_name)

    conn = None
    cursor = None

    try:
        with open(file_path, "wb") as saved_file:
            saved_file.write(file_bytes)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documents
                (user_id, original_name, stored_name, uploaded_at)
            VALUES (%s, %s, %s, NOW())
            """,
            (user_id, original_name, stored_name)
        )
        conn.commit()

    except Exception:
        if os.path.isfile(file_path):
            os.remove(file_path)
        abort(500, description="Could not save scanned document.")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("documents.my_files"))



@documents_bp.route("/ocr", methods=["GET", "POST"])
def ocr_document():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    error = None
    extracted_text = None

    # Windows mein Tesseract ka path
    pytesseract.pytesseract.tesseract_cmd = os.environ.get(
        "TESSERACT_CMD",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, original_name, stored_name, uploaded_at
            FROM documents
            WHERE user_id = %s
            ORDER BY uploaded_at DESC
            """,
            (user_id,)
        )
        all_documents = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    # OCR ke liye sirf images
    image_documents = [
        doc for doc in all_documents
        if doc["stored_name"].lower().endswith(
            (".jpg", ".jpeg", ".png")
        )
    ]

    if request.method == "POST":
        document_id = request.form.get("document_id", type=int)

        if not document_id:
            error = "Please select an image."
        else:
            document = get_user_document(document_id, user_id)

            if not document:
                abort(404)

            stored_name = document["stored_name"]

            if not stored_name.lower().endswith(
                (".jpg", ".jpeg", ".png")
            ):
                abort(400, description="Please select an image file.")

            file_path = os.path.join(UPLOAD_FOLDER, stored_name)

            try:
                with Image.open(file_path) as image:
                    image = image.convert("RGB")
                    extracted_text = pytesseract.image_to_string(
                        image,
                        lang="eng"
                    )

                if not extracted_text.strip():
                    error = "No readable text found in this image."

            except pytesseract.TesseractNotFoundError:
                error = (
                    "Tesseract OCR engine not found. "
                    "Please install it and check its path."
                )
            except Exception:
                error = (
                    "Could not process this image. "
                    "Please try another image."
                )

    return render_template(
        "documents.html",
        mode="ocr",
        image_documents=image_documents,
        extracted_text=extracted_text,
        error=error
    )



@documents_bp.route("/reader")
def pdf_reader():
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, original_name, stored_name, uploaded_at
            FROM documents
            WHERE user_id = %s
            ORDER BY uploaded_at DESC
            """,
            (user_id,)
        )
        all_documents = cursor.fetchall()
        
        # Sirf PDF files show hongi
        pdf_documents = [
            doc for doc in all_documents
            if doc["stored_name"].lower().endswith(".pdf")
        ]
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "documents.html",
        mode="reader",
        pdf_documents=pdf_documents
    )


@documents_bp.route("/reader/<int:document_id>")
def read_pdf(document_id):
    user_id = current_user_id()
    if not user_id:
        return redirect(url_for("login"))

    document = get_user_document(document_id, user_id)
    if not document:
        abort(404)

    # PDF ke alawa doosri file open nahi hogi
    if not document["stored_name"].lower().endswith(".pdf"):
        abort(404)

    return send_from_directory(
        UPLOAD_FOLDER,
        document["stored_name"],
        mimetype="application/pdf",
        as_attachment=False
    )