from benefits import benefits_bp
from ai_assistant import ai_bp
import os
import re
import logging
from ocr_validator import ocr_bp
from flask import (Flask, render_template, request, redirect,url_for, session, flash, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
from mysql.connector import IntegrityError
from dotenv import load_dotenv

from health_care import health_bp
from db import get_connection
from documents import documents_bp


# -------------------- APP CONFIGURATION --------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("SECRET_KEY missing in .env")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

logging.basicConfig(level=logging.INFO)

# Register Blueprints
app.register_blueprint(documents_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(health_bp)
app.register_blueprint(benefits_bp)
app.register_blueprint(ocr_bp)

# -------------------- HOME --------------------

@app.route("/")
def home():
    return render_template("index.html")


# -------------------- SIGNUP --------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not name or not email or not password:
            flash("Please fill all required fields.", "error")
            return render_template("signup.html")

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Please enter a valid email.", "error")
            return render_template("signup.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("signup.html")

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("signup.html")

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            password_hash = generate_password_hash(password)

            cursor.execute(
                """
                INSERT INTO users (full_name, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (name, email, password_hash)
            )
            conn.commit()

            user_id = cursor.lastrowid
            session.clear()
            session["user_id"] = user_id
            session["user_name"] = name

            return redirect(url_for("dashboard"))

        except IntegrityError:
            flash("This email is already registered.", "error")

        except Exception:
            app.logger.exception("Signup failed")
            flash(
                "Database connection failed. Check MySQL settings.",
                "error"
            )

        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()

    return render_template("signup.html")


# -------------------- LOGIN --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                "SELECT * FROM users WHERE email = %s",
                (email,)
            )
            user = cursor.fetchone()

            if user and check_password_hash(
                user["password_hash"], password
            ):
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = user["full_name"]

                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "error")

        except Exception:
            app.logger.exception("Login failed")
            flash(
                "Database connection failed. Check MySQL settings.",
                "error"
            )

        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()

    return render_template("login.html")


# -------------------- GUEST --------------------

@app.route("/guest")
def guest():
    return render_template("dashboard.html")


# -------------------- DASHBOARD --------------------

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("index.html")


# -------------------- GLOBAL SEARCH --------------------

@app.route("/search")
def global_search():
    query = request.args.get("q", "").strip()

    if len(query) < 2:
        return jsonify({"results": []})

    query = query[:100]
    search_term = f"%{query}%"
    results = []

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # Search approved hospitals
        cursor.execute(
            """
            SELECT id, name, sector, address, speciality
            FROM hospitals
            WHERE status = 'approved'
              AND (
                  name LIKE %s OR
                  sector LIKE %s OR
                  address LIKE %s OR
                  speciality LIKE %s
              )
            ORDER BY name
            LIMIT 8
            """,
            (search_term, search_term, search_term, search_term)
        )

        hospitals = cursor.fetchall()

        for hospital in hospitals:
            details = [
                hospital.get("sector"),
                hospital.get("address"),
                hospital.get("speciality")
            ]

            results.append({
                "type": "Hospital",
                "title": hospital["name"],
                "description": " • ".join(
                    str(value) for value in details if value
                ),
                "url": url_for("health.health_home")
            })

        # Search only current user's appointments
        user_id = session.get("user_id")

        if user_id:
            cursor.execute(
                """
                SELECT
                    a.id,
                    a.appointment_date,
                    a.appointment_time,
                    a.status,
                    a.reason,
                    h.name AS hospital_name
                FROM appointments a
                JOIN hospitals h ON a.hospital_id = h.id
                WHERE a.user_id = %s
                  AND (
                      h.name LIKE %s OR
                      a.status LIKE %s OR
                      a.reason LIKE %s
                  )
                ORDER BY a.appointment_date DESC
                LIMIT 8
                """,
                (user_id, search_term, search_term, search_term)
            )

            appointments = cursor.fetchall()

            for appointment in appointments:
                results.append({
                    "type": "Appointment",
                    "title": appointment["hospital_name"],
                    "description": (
                        f"Date: {appointment['appointment_date']} • "
                        f"Time: {appointment['appointment_time']} • "
                        f"Status: {appointment['status']}"
                    ),
                    "url": url_for("health.my_appointments")
                })

        return jsonify({"results": results[:15]})

    except Exception:
        app.logger.exception("Global search failed")
        return jsonify({
            "error": "Search is temporarily unavailable."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# -------------------- LOGOUT --------------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# -------------------- RUN APP --------------------

if __name__ == "__main__":
    app.run(debug=False)