from datetime import date, time
from flask import (
    Blueprint, render_template, session,
    redirect, url_for, request, flash, current_app
)
from db import get_connection

health_bp = Blueprint(
    "health",
    __name__,
    url_prefix="/health"
)


# ---------------- HEALTH & CARE HOME ----------------

@health_bp.route("/")
def health_home():
    return render_template("health_care.html")


# ---------------- ADD HOSPITAL ----------------

@health_bp.route("/add", methods=["GET", "POST"])
def add_hospital():
    # Login required to add a hospital
    if "user_id" not in session:
        flash("Please login to add a hospital.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        sector = request.form.get("sector", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        speciality = request.form.get("speciality", "").strip()

        # Required field validation
        if not all([name, sector, address, phone]):
            flash("Please fill all required fields.", "error")
            return render_template("add_hospital.html")

        # Basic input validation
        if len(name) > 150 or len(sector) > 50:
            flash("Hospital name or sector is too long.", "error")
            return render_template("add_hospital.html")

        if len(address) > 255 or len(phone) > 15:
            flash("Address or phone number is too long.", "error")
            return render_template("add_hospital.html")

        if len(speciality) > 150:
            flash("Speciality is too long.", "error")
            return render_template("add_hospital.html")

        conn = None
        cursor = None

        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO hospitals
                (user_id, name, sector, address, phone, speciality)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    session["user_id"],
                    name,
                    sector,
                    address,
                    phone,
                    speciality or None
                )
            )

            conn.commit()

            flash(
                "Hospital submitted successfully! "
                "It will be visible after verification.",
                "success"
            )
            return redirect(url_for("health.health_home"))

        except Exception:
            current_app.logger.exception("Add hospital failed")

            if conn:
                conn.rollback()

            flash(
                "Could not save hospital. Please try again.",
                "error"
            )

        finally:
            if cursor:
                cursor.close()

            if conn and conn.is_connected():
                conn.close()

    return render_template("add_hospital.html")


# ---------------- FIND HOSPITALS ----------------

@health_bp.route("/find")
def find_hospitals():
    # Public access: show only approved hospitals
    conn = None
    cursor = None
    hospitals = []

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        search = request.args.get("q", "").strip()

        if search:
            search_term = f"%{search}%"

            cursor.execute(
                """
                SELECT id, name, sector, address, phone, speciality
                FROM hospitals
                WHERE status = 'approved'
                AND (
                    name LIKE %s
                    OR sector LIKE %s
                    OR address LIKE %s
                    OR speciality LIKE %s
                )
                ORDER BY name
                """,
                (search_term, search_term, search_term, search_term)
            )
        else:
            cursor.execute(
                """
                SELECT id, name, sector, address, phone, speciality
                FROM hospitals
                WHERE status = 'approved'
                ORDER BY name
                """
            )

        hospitals = cursor.fetchall()

    except Exception:
        current_app.logger.exception("Find hospitals failed")
        flash("Unable to load hospitals right now.", "error")

    finally:
        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()

    return render_template(
        "find_hospitals.html",
        hospitals=hospitals,
        search=request.args.get("q", "").strip()
    )


@health_bp.route("/my-hospitals")
def my_hospitals():
    if "user_id" not in session:
        flash("Please login first.", "error")
        return redirect(url_for("login"))

    conn = None
    cursor = None
    hospitals = []

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, name, sector, address, phone, speciality, status
            FROM hospitals
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (session["user_id"],))

        hospitals = cursor.fetchall()

    except Exception:
        current_app.logger.exception("My hospitals failed")
        flash("Unable to load your hospitals.", "error")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    return render_template("my_hospitals.html", hospitals=hospitals)


@health_bp.route("/delete/<int:hospital_id>", methods=["POST"])
def delete_hospital(hospital_id):
    if "user_id" not in session:
        flash("Please login first.", "error")
        return redirect(url_for("login"))

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM hospitals
            WHERE id = %s AND user_id = %s
        """, (hospital_id, session["user_id"]))

        if cursor.rowcount == 0:
            flash("Hospital not found or you don't own it.", "error")
        else:
            conn.commit()
            flash("Hospital deleted successfully.", "success")

    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("Delete hospital failed")
        flash("Could not delete hospital. Please try again.", "error")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    return redirect(url_for("health.my_hospitals"))

@health_bp.route("/appointment", methods=["GET", "POST"])
def appointment():
    if "user_id" not in session:
        flash("Please login to book an appointment.", "error")
        return redirect(url_for("login"))

    conn = None
    cursor = None
    hospitals = []

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, name, sector, address
            FROM hospitals
            WHERE status = 'approved'
            ORDER BY name
        """)
        hospitals = cursor.fetchall()

        if request.method == "POST":
            hospital_id = request.form.get("hospital_id", type=int)
            appointment_date = request.form.get("appointment_date", "")
            appointment_time = request.form.get("appointment_time", "")
            reason = request.form.get("reason", "").strip()

            if not hospital_id or not appointment_date or not appointment_time:
                flash("Please fill all required fields.", "error")
                return render_template(
                    "appointment.html", hospitals=hospitals
                )

            if len(reason) > 500:
                flash("Reason must be under 500 characters.", "error")
                return render_template(
                    "appointment.html", hospitals=hospitals
                )

            cursor.execute("""
                SELECT id FROM hospitals
                WHERE id = %s AND status = 'approved'
            """, (hospital_id,))

            if not cursor.fetchone():
                flash("Please select a valid hospital.", "error")
                return render_template(
                    "appointment.html", hospitals=hospitals
                )

            try:
                parsed_date = date.fromisoformat(appointment_date)
                parsed_time = time.fromisoformat(appointment_time)
            except ValueError:
                flash("Invalid appointment date or time.", "error")
                return render_template(
                    "appointment.html", hospitals=hospitals
                )

            if parsed_date < date.today():
                flash("Please select a future date.", "error")
                return render_template(
                    "appointment.html", hospitals=hospitals
                )

            cursor.execute("""
                INSERT INTO appointments
                (user_id, hospital_id, appointment_date,
                 appointment_time, reason)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                session["user_id"], hospital_id,
                appointment_date, appointment_time,
                reason or None
            ))

            conn.commit()
            flash("Appointment request submitted successfully!", "success")
            return redirect(url_for("health.appointment"))

    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("Appointment request failed")
        flash("Unable to process appointment. Please try again.", "error")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    return render_template("appointment.html",hospitals=hospitals,today=date.today().isoformat())

# ---------------- MY APPOINTMENTS ----------------

@health_bp.route("/my-appointments")
def my_appointments():
    if "user_id" not in session:
        flash("Please login to view your appointments.", "error")
        return redirect(url_for("login"))

    conn = None
    cursor = None
    appointments = []

    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                a.id,
                h.name AS hospital_name,
                a.appointment_date,
                a.appointment_time,
                a.reason,
                a.status,
                a.created_at
            FROM appointments AS a
            JOIN hospitals AS h
                ON a.hospital_id = h.id
            WHERE a.user_id = %s
            ORDER BY a.appointment_date DESC,
                     a.appointment_time DESC
        """, (session["user_id"],))

        appointments = cursor.fetchall()

    except Exception:
        current_app.logger.exception("My appointments failed")
        flash("Unable to load your appointments.", "error")

    finally:
        if cursor:
            cursor.close()

        if conn and conn.is_connected():
            conn.close()

    return render_template(
        "my_appointments.html",
        appointments=appointments
    )

@health_bp.route("/emergency")
def emergency():
    return render_template("emergency.html")