"""
ElderCare MedTrack & Doorstep Refill System
Flask Application providing end-to-end (A to Z) medicine management,
Authentication (Login/Register/Logout), Patient Details Profile Management,
elderly-friendly daily pill reminders, inventory monitoring, low-stock alerts,
and doorstep pharmacy delivery workflow in 100% English.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, Response, abort
import sqlite3
import datetime
import math
import random
import os
import secrets
import hashlib
import hmac
import time
from dotenv import load_dotenv

# Load environment configuration (.env)
load_dotenv()

from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection, init_db
from services.inventory_manager import InventoryManager
from services.refill_predictor import RefillPredictor
from services.communication_service import CommunicationService
from services.scheduler_service import ReminderScheduler, get_current_ist_datetime
from services.viva_qa import VivaAssistant

app = Flask(__name__)
# Never use a source-controlled signing key. Local development gets an ephemeral
# key; production must provide a stable secret through the environment.
_secret_key = os.getenv("SECRET_KEY", "").strip()
if not _secret_key:
    _secret_key = secrets.token_urlsafe(48)
    app.logger.warning("SECRET_KEY not set in environment. Generated fallback session key for deployment.")
app.secret_key = _secret_key
_development = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not _development,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=8),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)


def _login_fingerprint():
    """Pseudonymize remote address for rate limiting; do not persist raw IPs."""
    address = request.remote_addr or "unknown"
    return hmac.new(app.secret_key.encode(), address.encode(), hashlib.sha256).hexdigest()

# Ensure DB exists
init_db()

@app.template_global("whatsapp_tracking_url")
def whatsapp_tracking_url(order, phone=None, patient=None):
    try:
        base_url = request.url_root.rstrip("/")
        target_phone = phone or (order["contact_phone"] if order and "contact_phone" in order.keys() and order["contact_phone"] else None) or (patient["phone"] if patient else None)
        if not target_phone:
            return "#"
        p_name = patient["name"] if patient else "Patient"
        return CommunicationService.generate_whatsapp_tracking_url(
            phone=target_phone,
            order=order,
            patient_name=p_name,
            base_url=base_url
        )
    except Exception:
        digits = "".join(filter(str.isdigit, str(phone or "")))
        if len(digits) == 10:
            digits = "91" + digits
        return f"https://wa.me/{digits}" if len(digits) >= 10 else "#"

@app.before_request
def require_login():
    """Default-deny healthcare routes and enforce CSRF for browser mutations."""
    public_endpoints = {
        "signin", "login", "register", "register_caregiver", "static", "healthz", "service_worker", "viva_assistant", "api_viva_ask", "set_language",
        "api_voice_twiml", "api_voice_gather", "api_voice_status_callback"
    }
    signed_webhooks = {"api_voice_twiml", "api_voice_gather", "api_voice_status_callback"}

    if request.path.startswith("/static"):
        return None
    if request.method in {"GET", "HEAD", "OPTIONS"} and "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.endpoint not in signed_webhooks:
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "") or request.headers.get("X-CSRFToken", "")
        if not expected or not provided or not secrets.compare_digest(expected, provided):
            abort(400, description="CSRF validation failed. Reload the page and try again.")
    if request.endpoint in public_endpoints:
        return None

    user_role = session.get("user_role")
    if not user_role:
        flash("Please sign in to access this healthcare module.", "warning")
        return redirect(url_for("signin"))

    role_requirements = {
        "pharmacy": {"PHARMACY", "ADMIN"},
        "pharmacy_assisted_order": {"PHARMACY", "ADMIN"},
        "pharmacy_verify_rx": {"PHARMACY", "ADMIN"},
        "pharmacy_cancel_order": {"PHARMACY", "ADMIN"},
        "patient_details": {"PATIENT", "ADMIN"},
        "logout": {"PATIENT", "CAREGIVER", "PHARMACY", "DELIVERY", "ADMIN"},
        "update_patient_details": {"PATIENT", "ADMIN"},
        "index": {"PATIENT", "CAREGIVER", "ADMIN"},
        "medicines_directory": {"PATIENT", "CAREGIVER", "ADMIN"},
        "add_prescription": {"PATIENT", "CAREGIVER", "ADMIN"},
        "my_prescriptions": {"PATIENT", "CAREGIVER", "ADMIN"},
        "update_stock": {"PATIENT", "CAREGIVER", "ADMIN"},
        "delete_prescription": {"PATIENT", "CAREGIVER", "ADMIN"},
        "place_order": {"PATIENT", "CAREGIVER", "ADMIN"},
        "refill": {"PATIENT", "CAREGIVER", "ADMIN"},
        "orders": {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY", "DELIVERY"},
        "order_detail": {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY", "DELIVERY"},
        "mark_failed_delivery": {"DELIVERY", "ADMIN"},
        "api_order_rider_location": {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY", "DELIVERY"},
        "api_send_order_whatsapp": {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY"},
        "caregiver": {"CAREGIVER", "PATIENT", "ADMIN"},
        "voice_summary": {"PATIENT", "CAREGIVER", "ADMIN"},
        "print_schedule_card": {"PATIENT", "CAREGIVER", "ADMIN"},
        "create_caregiver_invite": {"PATIENT", "ADMIN"},
        "revoke_caregiver_access": {"PATIENT", "ADMIN"},
        "update_notification_preferences": {"PATIENT", "CAREGIVER", "ADMIN"},
        "take_dose": {"PATIENT", "CAREGIVER", "ADMIN"},
        "sos": {"PATIENT", "CAREGIVER", "ADMIN"},
        "sos_alert": {"PATIENT", "CAREGIVER", "ADMIN"},
        "vitals": {"PATIENT", "CAREGIVER", "ADMIN"},
        "log_vital": {"PATIENT", "CAREGIVER", "ADMIN"},
        "delete_vital": {"PATIENT", "CAREGIVER", "ADMIN"},
        "api_vitals_chart_data": {"PATIENT", "CAREGIVER", "ADMIN"},
        "api_active_alerts": {"PATIENT", "CAREGIVER", "ADMIN"},
        "admin_reminders": {"ADMIN", "PHARMACY"},
        "admin_trigger_check": {"ADMIN", "PHARMACY"},
        "admin_test_call": {"ADMIN", "PHARMACY"},
        "admin_test_sms": {"ADMIN", "PHARMACY"},
        "advance_order_status": {"PHARMACY", "DELIVERY", "ADMIN"},
        "cancel_patient_order": {"PATIENT", "CAREGIVER", "PHARMACY", "ADMIN"},
        "switch_mode": {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY", "DELIVERY"},
    }
    allowed_roles = role_requirements.get(request.endpoint)
    if not allowed_roles or user_role not in allowed_roles:
        abort(403)

    # Enforce object-level ownership for clinical records and orders.
    endpoint = request.endpoint or ""
    args = request.view_args or {}
    conn = None
    patient_id = None
    try:
        if "prescription_id" in args and endpoint in {"take_dose", "skip_dose", "update_stock", "delete_prescription"}:
            conn = get_db_connection()
            row = conn.execute("SELECT patient_id FROM prescriptions WHERE id = ?", (args["prescription_id"],)).fetchone()
            patient_id = row["patient_id"] if row else None
        elif "order_id" in args and endpoint in {"order_detail", "api_order_rider_location", "cancel_patient_order", "advance_order_status", "api_send_order_whatsapp", "mark_failed_delivery"}:
            conn = get_db_connection()
            row = conn.execute("SELECT patient_id FROM refill_orders WHERE id = ?", (args["order_id"],)).fetchone()
            patient_id = row["patient_id"] if row else None
        elif "vital_id" in args and endpoint == "delete_vital":
            conn = get_db_connection()
            row = conn.execute("SELECT patient_id FROM health_vitals WHERE id = ?", (args["vital_id"],)).fetchone()
            patient_id = row["patient_id"] if row else None
        if patient_id is not None and user_role in {"PATIENT", "CAREGIVER"}:
            if not _patient_access_allowed(patient_id, conn):
                abort(403)
    finally:
        if conn is not None:
            conn.close()


def _patient_access_allowed(patient_id, conn=None):
    """Return whether the signed-in account is entitled to a patient's data."""
    role = session.get("user_role")
    if role == "ADMIN":
        return True
    if role == "PATIENT":
        return session.get("patient_id") == patient_id
    if role != "CAREGIVER":
        return False
    owns_connection = conn is None
    db = conn or get_db_connection()
    try:
        patient = db.execute(
            "SELECT caregiver_phone, caregiver_authorized FROM patients WHERE id = ?",
            (patient_id,),
        ).fetchone()
        if not patient or not patient["caregiver_authorized"]:
            return False
        # An explicit user->patient link is accepted only alongside the patient's
        # authorization flag. Otherwise match the authenticated caregiver phone.
        if session.get("patient_id") == patient_id:
            return True
        digits = lambda value: "".join(ch for ch in str(value or "") if ch.isdigit())[-10:]
        caregiver_phone = digits(session.get("user_phone"))
        stored_phone = digits(patient["caregiver_phone"])
        return bool(caregiver_phone and stored_phone and caregiver_phone == stored_phone)
    finally:
        if owns_connection:
            db.close()


@app.route("/service-worker.js", methods=["GET"])
def service_worker():
    response = app.send_static_file("service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/healthz", methods=["GET"])
def healthz():
    """Platform health endpoint; reports availability without database contents."""
    conn = None
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"}), 200
    except Exception as exc:
        app.logger.error("Health check database probe failed (%s).", type(exc).__name__)
        return jsonify({"status": "unavailable"}), 503
    finally:
        if conn is not None:
            conn.close()

@app.context_processor
def inject_global_data():
    """Inject active patient, auth status, role, low stock count, and alerts into all templates"""
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    
    patient = None
    if patient_id:
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    # Calculate low stock count
    low_stock_count = 0
    unread_alerts_count = 0
    if patient:
        row = conn.execute("""
            SELECT COUNT(*) FROM prescriptions 
            WHERE patient_id = ? AND stock_remaining <= refill_threshold
        """, (patient["id"],)).fetchone()
        low_stock_count = row[0] if row else 0

        row = conn.execute("""
            SELECT COUNT(*) FROM caregiver_alerts 
            WHERE patient_id = ? AND status = 'Unread'
        """, (patient["id"],)).fetchone()
        unread_alerts_count = row[0] if row else 0

    conn.close()

    # Ensure CSRF token in session
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)

    return {
        "current_patient": patient,
        "is_logged_in": bool(session.get("patient_id") or session.get("user_role")),
        "user_role": session.get("user_role", ""),
        "user_name": session.get("user_name", patient["name"] if patient else "Senior Citizen"),
        "csrf_token": session.get("csrf_token"),
        "low_stock_count": low_stock_count,
        "unread_alerts_count": unread_alerts_count,
        "today_date": datetime.date.today().strftime("%A, %d %B %Y")
    }

# ----------------- AUTHENTICATION: SIGN IN ----------------- #
@app.route("/signin", methods=["GET", "POST"])
def signin():
    if session.get("patient_id") or session.get("user_role"):
        role = session.get("user_role", "PATIENT")
        if role == "PHARMACY":
            return redirect(url_for("pharmacy"))
        elif role == "DELIVERY":
            return redirect(url_for("orders"))
        elif role == "CAREGIVER":
            return redirect(url_for("caregiver"))
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = request.form.get("phone", "").strip()
        pin = request.form.get("pin", "").strip()

        conn = get_db_connection()
        now = int(time.time())
        fingerprint = _login_fingerprint()
        conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 600,))
        attempts = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip_hash = ? AND attempted_at >= ?",
            (fingerprint, now - 600),
        ).fetchone()[0]
        if attempts >= 5:
            conn.close()
            return "Too many sign-in attempts. Please wait ten minutes before trying again.", 429

        # 1. Check users table for multi-role sign-in
        user = conn.execute("""
            SELECT * FROM users 
            WHERE (phone = ? OR username = ?)
        """, (identifier, identifier.lower())).fetchone()

        valid_user = False
        if user and user["password_hash"] and user["password_hash"].startswith(("pbkdf2:", "scrypt:")):
            # Reject legacy demo credentials even if stored as a hash.
            valid_user = pin != "1234" and check_password_hash(user["password_hash"], pin)

            if valid_user:
                session.clear()
                session["user_id"] = user["id"]
                session["user_role"] = user["role"]
                session["user_name"] = user["full_name"]
                session["user_phone"] = user["phone"]
                if user["patient_id"]:
                    session["patient_id"] = user["patient_id"]
                elif user["role"] == "ADMIN":
                    first_p = conn.execute("SELECT id FROM patients ORDER BY id ASC LIMIT 1").fetchone()
                    if first_p:
                        session["patient_id"] = first_p["id"]
                conn.execute("DELETE FROM login_attempts WHERE ip_hash = ?", (fingerprint,))
                conn.close()

                flash(f"Signed in as {user['full_name']} ({user['role']}).", "success")
                if user["role"] == "PHARMACY":
                    return redirect(url_for("pharmacy"))
                elif user["role"] == "DELIVERY":
                    return redirect(url_for("orders"))
                elif user["role"] == "CAREGIVER":
                    return redirect(url_for("caregiver"))
                return redirect(url_for("index"))

        # 2. Check direct patient sign-in using a stored password hash only.
        patient = conn.execute("SELECT * FROM patients WHERE phone = ?", (identifier,)).fetchone()
        if patient and (not patient["password_hash"] or pin == "1234" or
                        not check_password_hash(patient["password_hash"], pin)):
            patient = None

        if patient:
            conn.execute("DELETE FROM login_attempts WHERE ip_hash = ?", (fingerprint,))
        else:
            conn.execute("INSERT INTO login_attempts (ip_hash, attempted_at) VALUES (?, ?)", (fingerprint, now))
        conn.close()

        if patient:
            session.clear()
            session["patient_id"] = patient["id"]
            session["user_role"] = "PATIENT"
            session["user_name"] = patient["name"]
            session["user_phone"] = patient["phone"]
            flash(f"Welcome back, {patient['name']}! You have successfully signed in.", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid Phone Number, Username, or PIN. Please enter registered credentials.", "error")
            return redirect(url_for("signin"))

    return render_template("signin.html")

# Maintain backward compatibility alias for /login endpoint
app.add_url_rule("/login", endpoint="login", view_func=signin, methods=["GET", "POST"])

# ----------------- AUTHENTICATION: REGISTER ----------------- #
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            age = int(request.form.get("age", ""))
        except (ValueError, TypeError):
            age = 0
        gender = request.form.get("gender", "Male").strip()
        phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[-10:]
        pin = request.form.get("pin", "").strip()
        if not name or len(name) > 120 or not 40 <= age <= 120 or len(phone) != 10 or not 6 <= len(pin) <= 128:
            flash("Enter a valid name, age (40–120), 10-digit phone, and password/PIN of 6–128 characters.", "error")
            return redirect(url_for("register"))
        blood_group = request.form.get("blood_group", "O+").strip()
        address = request.form.get("address", "").strip()
        medical_conditions = request.form.get("medical_conditions", "None").strip()
        allergies = request.form.get("allergies", "None").strip()
        mobility_status = request.form.get("mobility_status", "Independent").strip()
        caregiver_name = request.form.get("caregiver_name", "Family Caregiver").strip()
        caregiver_phone = "".join(ch for ch in request.form.get("caregiver_phone", "") if ch.isdigit())[-10:]
        caregiver_relation = request.form.get("caregiver_relation", "Family").strip()
        doctor_name = request.form.get("doctor_name", "Geriatrician").strip()
        doctor_phone = request.form.get("doctor_phone", "").strip()

        conn = get_db_connection()
        
        # Check if phone already registered
        existing = conn.execute("SELECT id FROM patients WHERE phone = ?", (phone,)).fetchone()
        if existing:
            conn.close()
            flash("This phone number is already registered. Please sign in.", "warning")
            return redirect(url_for("signin"))

        cursor = conn.cursor()
        p_hash = generate_password_hash(pin)
        cursor.execute("""
            INSERT INTO patients (
                name, age, gender, phone, pin, password_hash, address, blood_group,
                medical_conditions, allergies, mobility_status,
                caregiver_name, caregiver_phone, caregiver_relation,
                doctor_name, doctor_phone, preferred_language,
                consent_recorded, caregiver_authorized
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'en', 0, 0)
        """, (
            name, age, gender, phone, p_hash, address, blood_group,
            medical_conditions, allergies, mobility_status,
            caregiver_name, caregiver_phone, caregiver_relation,
            doctor_name, doctor_phone
        ))
        new_patient_id = cursor.lastrowid

        # Also create User record for role-based system
        username_candidate = phone[-6:] if len(phone) >= 6 else f"user_{new_patient_id}"
        cursor.execute("""
            INSERT OR IGNORE INTO users (username, phone, password_hash, role, full_name, patient_id)
            VALUES (?, ?, ?, 'PATIENT', ?, ?)
        """, (username_candidate, phone, p_hash, name, new_patient_id))

        conn.commit()
        conn.close()

        session.clear()
        session["patient_id"] = new_patient_id
        session["user_role"] = "PATIENT"
        session["user_name"] = name
        flash("Patient profile created. Caregiver access and notifications remain disabled until explicitly authorized.", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/caregiver/register", methods=["GET", "POST"])
def register_caregiver():
    if request.method == "GET":
        return render_template("caregiver_register.html")

    name = request.form.get("name", "").strip()
    phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[-10:]
    password = request.form.get("password", "")
    invite_code = request.form.get("invite_code", "").strip()
    if not name or len(name) > 120 or len(phone) != 10 or not 6 <= len(password) <= 128 or not invite_code:
        flash("Enter a valid name, 10-digit phone, invitation code, and password of 6–128 characters.", "error")
        return redirect(url_for("register_caregiver"))

    invite_hash = hashlib.sha256(invite_code.encode()).hexdigest()
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        patient = conn.execute(
            "SELECT id, caregiver_phone FROM patients WHERE caregiver_invite_hash = ? AND caregiver_invite_expires_at >= ? AND caregiver_authorized = 1",
            (invite_hash, int(time.time())),
        ).fetchone()
        stored_phone = "".join(ch for ch in str(patient["caregiver_phone"] or "") if ch.isdigit())[-10:] if patient else ""
        if not patient or not hmac.compare_digest(stored_phone, phone):
            conn.rollback()
            flash("Invitation invalid or expired. Ask the patient to generate a new invitation.", "error")
            return redirect(url_for("register_caregiver"))

        existing = conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()
        if existing:
            conn.rollback()
            flash("An account already uses that phone number. Contact the patient to request access through the existing account.", "error")
            return redirect(url_for("signin"))

        conn.execute("""
            INSERT INTO users (username, phone, password_hash, role, full_name, patient_id)
            VALUES (?, ?, ?, 'CAREGIVER', ?, ?)
        """, (f"caregiver_{phone}", phone, generate_password_hash(password), name, patient["id"]))
        conn.execute("UPDATE patients SET caregiver_invite_hash = NULL, caregiver_invite_expires_at = NULL WHERE id = ?", (patient["id"],))
        conn.commit()
        flash("Caregiver account created. Sign in with your phone and password.", "success")
        return redirect(url_for("signin"))
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("An account already exists for these credentials. Please sign in.", "error")
        return redirect(url_for("signin"))
    finally:
        conn.close()


@app.route("/caregiver/invite", methods=["POST"])
def create_caregiver_invite():
    patient_id = session.get("patient_id")
    if not patient_id:
        abort(403)
    conn = get_db_connection()
    patient = conn.execute("SELECT caregiver_phone FROM patients WHERE id = ?", (patient_id,)).fetchone()
    phone = "".join(ch for ch in str(patient["caregiver_phone"] or "") if ch.isdigit())[-10:] if patient else ""
    if len(phone) != 10:
        conn.close()
        flash("Save a valid 10-digit caregiver phone number before creating an invitation.", "error")
        return redirect(url_for("patient_details"))
    invite_code = secrets.token_urlsafe(32)
    invite_hash = hashlib.sha256(invite_code.encode()).hexdigest()
    conn.execute("""
        UPDATE patients SET caregiver_authorized = 1, caregiver_invite_hash = ?, caregiver_invite_expires_at = ?
        WHERE id = ?
    """, (invite_hash, int(time.time()) + 86400, patient_id))
    conn.commit()
    conn.close()
    flash(f"Caregiver invitation code (share privately; shown once): {invite_code}", "success")
    return redirect(url_for("patient_details"))


@app.route("/caregiver/revoke", methods=["POST"])
def revoke_caregiver_access():
    patient_id = session.get("patient_id")
    if not patient_id:
        abort(403)
    conn = get_db_connection()
    conn.execute("""
        UPDATE patients SET caregiver_authorized = 0, caregiver_invite_hash = NULL,
          caregiver_invite_expires_at = NULL WHERE id = ?
    """, (patient_id,))
    conn.commit()
    conn.close()
    flash("Caregiver access and outstanding invitations have been revoked.", "success")
    return redirect(url_for("patient_details"))

# ----------------- AUTHENTICATION: LOGOUT ----------------- #
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have successfully signed out.", "info")
    return redirect(url_for("signin"))

@app.route("/patient_details")
def patient_details():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    if not patient:
        session.pop("patient_id", None)
        return redirect(url_for("signin"))
    return render_template("patient_details.html", patient=patient)

@app.route("/patient_details/update", methods=["POST"])
def update_patient_details():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    name = request.form.get("name", "").strip()
    try:
        age = int(request.form.get("age", ""))
    except (ValueError, TypeError):
        age = 0
    gender = request.form.get("gender", "Male").strip()
    phone = "".join(ch for ch in request.form.get("phone", "") if ch.isdigit())[-10:]
    pin = request.form.get("pin", "").strip()
    if pin and (len(pin) < 6 or len(pin) > 128):
        conn.close()
        flash("New password/PIN must be 6–128 characters.", "error")
        return redirect(url_for("patient_details"))
    password_hash = generate_password_hash(pin) if pin else None
    if not name or len(name) > 120 or not 40 <= age <= 120 or len(phone) != 10:
        conn.close()
        flash("Enter a valid name, age (40–120), and 10-digit phone number.", "error")
        return redirect(url_for("patient_details"))
    duplicate_phone = conn.execute("SELECT id FROM patients WHERE phone = ? AND id <> ?", (phone, patient_id)).fetchone()
    if duplicate_phone:
        conn.close()
        flash("That phone number is already in use by another account.", "error")
        return redirect(url_for("patient_details"))
    blood_group = request.form.get("blood_group", "").strip()
    address = request.form.get("address", "").strip()
    medical_conditions = request.form.get("medical_conditions", "").strip()
    allergies = request.form.get("allergies", "").strip()
    mobility_status = request.form.get("mobility_status", "").strip()
    caregiver_name = request.form.get("caregiver_name", "").strip()
    caregiver_phone = "".join(ch for ch in request.form.get("caregiver_phone", "") if ch.isdigit())[-10:]
    caregiver_relation = request.form.get("caregiver_relation", "").strip()
    doctor_name = request.form.get("doctor_name", "").strip()
    doctor_phone = request.form.get("doctor_phone", "").strip()

    existing_caregiver = conn.execute("SELECT caregiver_phone FROM patients WHERE id = ?", (patient_id,)).fetchone()
    caregiver_phone_changed = "".join(ch for ch in str(existing_caregiver["caregiver_phone"] or "") if ch.isdigit())[-10:] != caregiver_phone
    conn.execute("""
        UPDATE patients SET
            name = ?, age = ?, gender = ?, phone = ?, pin = NULL,
            password_hash = COALESCE(?, password_hash),
            blood_group = ?, address = ?, medical_conditions = ?,
            allergies = ?, mobility_status = ?, caregiver_name = ?,
            caregiver_phone = ?, caregiver_relation = ?, doctor_name = ?,
            doctor_phone = ?,
            caregiver_authorized = CASE WHEN ? THEN 0 ELSE caregiver_authorized END,
            caregiver_invite_hash = CASE WHEN ? THEN NULL ELSE caregiver_invite_hash END,
            caregiver_invite_expires_at = CASE WHEN ? THEN NULL ELSE caregiver_invite_expires_at END
        WHERE id = ?
    """, (
        name, age, gender, phone, password_hash, blood_group, address,
        medical_conditions, allergies, mobility_status,
        caregiver_name, caregiver_phone, caregiver_relation,
        doctor_name, doctor_phone, int(caregiver_phone_changed), int(caregiver_phone_changed), int(caregiver_phone_changed), patient_id
    ))
    conn.execute(
        "UPDATE users SET phone = ?, full_name = ?, password_hash = COALESCE(?, password_hash) WHERE role = 'PATIENT' AND patient_id = ?",
        (phone, name, password_hash, patient_id),
    )
    conn.commit()
    conn.close()

    flash("Patient details updated successfully!", "success")
    return redirect(url_for("patient_details"))

# ----------------- HOME / SENIOR DASHBOARD ----------------- #
@app.route("/")
def index():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        session.pop("patient_id", None)
        return redirect(url_for("signin"))

    # Fetch active prescriptions
    prescriptions = conn.execute("""
        SELECT * FROM prescriptions
        WHERE patient_id = ?
        ORDER BY stock_remaining ASC
    """, (patient["id"],)).fetchall()

    # Fetch today's dose logs
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    dose_logs = conn.execute("""
        SELECT * FROM dose_logs 
        WHERE patient_id = ? AND scheduled_for = ?
    """, (patient["id"], today_str)).fetchall()
    
    logged_prescription_ids = [log["prescription_id"] for log in dose_logs if log["status"] == "Taken"]

    # Categorize into slots: Morning, Afternoon, Evening, Night
    slots = {
        "Morning": [],
        "Afternoon": [],
        "Evening": [],
        "Night": []
    }
    
    low_stock_items = []
    
    for p in prescriptions:
        timing_lower = p["timing"].lower()
        if "morning" in timing_lower:
            slots["Morning"].append(p)
        if "afternoon" in timing_lower or "lunch" in timing_lower:
            slots["Afternoon"].append(p)
        if "evening" in timing_lower:
            slots["Evening"].append(p)
        if "night" in timing_lower or "bed" in timing_lower:
            slots["Night"].append(p)

        if p["stock_remaining"] <= p["refill_threshold"]:
            low_stock_items.append(p)

    # Latest active delivery order if any
    active_order = conn.execute("""
        SELECT * FROM refill_orders 
        WHERE patient_id = ? AND status != 'Delivered' 
        ORDER BY id DESC LIMIT 1
    """, (patient["id"],)).fetchone()

    conn.close()
    return render_template(
        "index.html",
        patient=patient,
        slots=slots,
        prescriptions=prescriptions,
        logged_prescription_ids=logged_prescription_ids,
        low_stock_items=low_stock_items,
        active_order=active_order
    )

# ----------------- MARK DOSE AS TAKEN / NOT TAKEN ----------------- #
@app.route("/api/take_dose/<int:prescription_id>", methods=["POST"])
def take_dose(prescription_id):
    conn = get_db_connection()
    prescription = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()
    
    if not prescription:
        conn.close()
        return jsonify({"success": False, "error": "Prescription not found"}), 404

    patient_id = prescription["patient_id"]
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    req_json = request.get_json(silent=True) or {}
    status = req_json.get("status", "Taken")
    reason = str(req_json.get("reason", "")).strip()[:500]
    if status not in {"Taken", "Not Taken", "Not Confirmed"}:
        conn.close()
        abort(400, description="Unsupported dose status.")

    # Handle Dose 'Not Taken' (Skipped)
    if status == "Not Taken":
        duplicate = conn.execute("""
            SELECT id FROM dose_logs WHERE prescription_id = ? AND scheduled_for = ? AND status = 'Not Taken' LIMIT 1
        """, (prescription_id, today_str)).fetchone()
        if not duplicate:
            conn.execute("""
            INSERT INTO dose_logs (prescription_id, patient_id, medicine_name, dose_time, scheduled_for, status, reason)
            VALUES (?, ?, ?, ?, ?, 'Not Taken', ?)
            """, (prescription_id, patient_id, prescription["medicine_name"], prescription["timing"], today_str, reason or "Skipped by patient"))

            # Alert caregiver in-app; external channels require explicit opt-in.
            conn.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'SKIPPED_DOSE', ?)
            """, (patient_id, f"Dose Skipped: {prescription['medicine_name']} ({prescription['timing']}) was marked as Not Taken today. Reason: {reason or 'Not specified'}. Home inventory remains {prescription['stock_remaining']} tabs."))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "status": "Not Taken",
            "new_stock": prescription["stock_remaining"],
            "medicine_name": prescription["medicine_name"],
            "confirm_msg": f"Recorded: {prescription['medicine_name']} marked as Not Taken ({reason or 'Skipped'}). Caregiver notified."
        })

    # Handle Dose 'Not Confirmed' (Pending Elder Confirmation)
    if status == "Not Confirmed":
        duplicate = conn.execute("""
            SELECT id FROM dose_logs WHERE prescription_id = ? AND scheduled_for = ? AND status = 'Not Confirmed' LIMIT 1
        """, (prescription_id, today_str)).fetchone()
        if not duplicate:
            conn.execute("""
            INSERT INTO dose_logs (prescription_id, patient_id, medicine_name, dose_time, scheduled_for, status, reason)
            VALUES (?, ?, ?, ?, ?, 'Not Confirmed', ?)
            """, (prescription_id, patient_id, prescription["medicine_name"], prescription["timing"], today_str, reason or "Unconfirmed by senior caller"))

            conn.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'DOSE_UNCONFIRMED', ?)
            """, (patient_id, f"Dose Unconfirmed: {prescription['medicine_name']} ({prescription['timing']}) status is Not Confirmed. Caregiver follow-up advised. Home inventory remains {prescription['stock_remaining']} tabs."))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "status": "Not Confirmed",
            "new_stock": prescription["stock_remaining"],
            "medicine_name": prescription["medicine_name"],
            "confirm_msg": f"Recorded: {prescription['medicine_name']} marked as Not Confirmed. Follow-up logged for caregiver."
        })

    # Default: Dose 'Taken'
    # Check if already taken today
    existing = conn.execute("""
        SELECT * FROM dose_logs 
        WHERE prescription_id = ? AND scheduled_for = ? AND status = 'Taken'
    """, (prescription_id, today_str)).fetchone()

    if existing:
        new_stock = prescription["stock_remaining"]
    else:
        conn.execute("""
            UPDATE prescriptions
            SET stock_remaining = CASE WHEN stock_remaining > 0 THEN stock_remaining - 1 ELSE 0 END
            WHERE id = ?
        """, (prescription_id,))
        new_stock = conn.execute("SELECT stock_remaining FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()["stock_remaining"]
    
    # Log dose
    if not existing:
        conn.execute("""
            INSERT INTO dose_logs (prescription_id, patient_id, medicine_name, dose_time, scheduled_for, status)
            VALUES (?, ?, ?, ?, ?, 'Taken')
        """, (prescription_id, patient_id, prescription["medicine_name"], prescription["timing"], today_str))

    if not existing and new_stock <= prescription["refill_threshold"]:
        recent_alert = conn.execute("""
            SELECT id FROM caregiver_alerts
            WHERE patient_id = ? AND alert_type = 'LOW_STOCK'
              AND message LIKE ? AND datetime(created_at) >= datetime('now', '-1 day')
            LIMIT 1
        """, (patient_id, f"%{prescription['medicine_name']}%")).fetchone()
        if not recent_alert:
            conn.execute("""
                INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                VALUES (?, 'LOW_STOCK', ?)
            """, (
                patient_id,
                f"Low Stock: {prescription['medicine_name']} has {new_stock} tablet(s) remaining. Review the refill forecast and place an order if needed; no order was placed automatically."
            ))

    conn.commit()
    conn.close()

    confirm_msg = f"Thank you! You have taken {prescription['medicine_name']}. Remaining inventory: {new_stock} tablet(s)."

    return jsonify({
        "success": True,
        "new_stock": new_stock,
        "is_low": new_stock <= prescription["refill_threshold"],
        "medicine_name": prescription["medicine_name"],
        "confirm_msg": confirm_msg
    })

# ----------------- A TO Z MEDICINES DIRECTORY ----------------- #
@app.route("/medicines")
def medicines_directory():
    letter = request.args.get("letter", "").upper().strip()
    category = request.args.get("category", "").strip()
    query = request.args.get("q", "").strip()

    conn = get_db_connection()
    
    sql = "SELECT * FROM medicines WHERE 1=1"
    params = []

    if letter and letter != "ALL":
        sql += " AND letter = ?"
        params.append(letter)
    if category and category != "ALL":
        sql += " AND category LIKE ?"
        params.append(f"%{category}%")
    if query:
        sql += " AND (name LIKE ? OR generic_name LIKE ? OR therapeutic_use LIKE ? OR category LIKE ?)"
        term = f"%{query}%"
        params.extend([term, term, term, term])

    sql += " ORDER BY name ASC"
    medicines = conn.execute(sql, params).fetchall()

    cats = conn.execute("SELECT DISTINCT category FROM medicines ORDER BY category").fetchall()
    categories = [c["category"] for c in cats]

    patient_id = session.get("patient_id")
    patient = None
    if patient_id:
        if not _patient_access_allowed(patient_id, conn):
            conn.close()
            abort(403)
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    elif session.get("user_role") != "ADMIN":
        conn.close()
        abort(403)

    active_names = []
    if patient:
        active = conn.execute("SELECT medicine_name FROM prescriptions WHERE patient_id = ?", (patient["id"],)).fetchall()
        active_names = [a["medicine_name"] for a in active]

    conn.close()

    alphabet = [chr(i) for i in range(ord('A'), ord('Z') + 1)]

    return render_template(
        "medicines.html",
        medicines=medicines,
        alphabet=alphabet,
        categories=categories,
        selected_letter=letter or "ALL",
        selected_category=category or "ALL",
        search_query=query,
        active_names=active_names,
        patient=patient
    )

# ----------------- ADD CUSTOM OR CATALOG MEDICINE TO PRESCRIPTION ----------------- #
@app.route("/prescriptions/add", methods=["POST"])
def add_prescription():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    patient = None
    if patient_id:
        patient = conn.execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()

    if not patient or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    medicine_name = request.form.get("medicine_name", "").strip()
    dosage = request.form.get("dosage", "500 mg").strip()
    timing = request.form.get("timing", "Morning").strip()
    timing_advice = request.form.get("timing_advice", "After Food").strip()
    stock_remaining = request.form.get("stock_remaining", type=int)
    refill_threshold = request.form.get("refill_threshold", type=int)
    doctor_notes = request.form.get("doctor_notes", "").strip()[:1000]
    if not medicine_name or len(medicine_name) > 200 or not dosage or len(dosage) > 100:
        conn.close()
        abort(400, description="Medicine name and dosage are required and must be within allowed lengths.")
    if stock_remaining is None or not 0 <= stock_remaining <= 100000 or refill_threshold is None or not 0 <= refill_threshold <= 100000:
        conn.close()
        abort(400, description="Stock and refill threshold must be valid non-negative quantities.")

    # Time-based reminder preferences (IST / Asia/Kolkata)
    schedule_time = request.form.get("schedule_time", "").strip()
    if not schedule_time:
        timing_map = {
            "morning": "08:00",
            "afternoon": "13:00",
            "evening": "18:00",
            "night": "21:00"
        }
        t_clean = timing.lower()
        matched_time = "08:00"
        for k, v in timing_map.items():
            if k in t_clean:
                matched_time = v
                break
        schedule_time = matched_time

    timezone_val = request.form.get("timezone", "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        datetime.datetime.strptime(schedule_time, "%H:%M")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone_val)
        except Exception:
            # Fallback on Windows or platforms without IANA tzdata package
            common_timezones = {
                "Asia/Kolkata", "Asia/Calcutta", "UTC", "GMT", "IST"
            }
            if timezone_val not in common_timezones:
                raise
    except Exception:
        conn.close()
        abort(400, description="Provide a valid 24-hour schedule time and IANA timezone.")
    
    reminder_consent = 1 if request.form.get("reminder_consent") in ["1", "on", "true"] else 0
    sms_reminder_enabled = int(reminder_consent and request.form.get("sms_reminder_enabled") in ["1", "on", "true"])
    voice_reminder_enabled = int(reminder_consent and request.form.get("voice_reminder_enabled") in ["1", "on", "true"])

    color_palette = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#14B8A6']
    color_code = random.choice(color_palette)

    med_entry = conn.execute("SELECT id FROM medicines WHERE name = ?", (medicine_name,)).fetchone()
    medicine_id = med_entry["id"] if med_entry else None

    conn.execute("""
        INSERT INTO prescriptions (
            patient_id, medicine_id, medicine_name, dosage, timing,
            timing_advice, stock_remaining, daily_doses, refill_threshold,
            doctor_notes, color_code, schedule_time, timezone,
            reminder_consent, sms_reminder_enabled, voice_reminder_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        patient["id"], medicine_id, medicine_name, dosage, timing,
        timing_advice, stock_remaining, refill_threshold, doctor_notes,
        color_code, schedule_time, timezone_val,
        reminder_consent, sms_reminder_enabled, voice_reminder_enabled
    ))
    
    conn.commit()
    conn.close()

    flash(f"'{medicine_name}' has been successfully added to your daily schedule at {schedule_time} ({timezone_val})!", "success")
    return redirect(url_for("my_prescriptions"))

# ----------------- MY PRESCRIPTIONS & INVENTORY ----------------- #
@app.route("/prescriptions")
def my_prescriptions():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    patient = None
    if patient_id:
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    prescriptions = conn.execute("""
        SELECT * FROM prescriptions 
        WHERE patient_id = ? 
        ORDER BY stock_remaining ASC
    """, (patient["id"],)).fetchall()
    
    all_medicines = conn.execute("SELECT id, name, default_dosage AS standard_strength FROM medicines ORDER BY name ASC").fetchall()
    conn.close()

    forecast_data = RefillPredictor.get_patient_refill_forecast(patient["id"]) if patient else None

    return render_template("my_prescriptions.html", patient=patient, prescriptions=prescriptions, forecast_data=forecast_data, all_medicines=all_medicines)

@app.route("/prescriptions/update_stock/<int:prescription_id>", methods=["POST"])
def update_stock(prescription_id):
    new_stock = request.form.get("stock_remaining", type=int)
    if new_stock is None or not 0 <= new_stock <= 100000:
        abort(400, description="Stock must be a valid non-negative quantity.")
    reason = request.form.get("correction_reason", "Physical bottle count differed from estimate").strip()
    conn = get_db_connection()
    prescription = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()
    if prescription and _patient_access_allowed(prescription["patient_id"], conn):
        old_stock = prescription["stock_remaining"]
        diff = new_stock - old_stock
        conn.execute("UPDATE prescriptions SET stock_remaining = ? WHERE id = ?", (new_stock, prescription_id))
        conn.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'STOCK_CORRECTION', ?)
        """, (prescription["patient_id"], f"Physical Stock Correction: {prescription['medicine_name']} manually corrected from {old_stock} to {new_stock} tablets ({diff:+d}). Reason: {reason}."))
        conn.commit()
    conn.close()
    flash(f"Medicine physical inventory updated to {new_stock} tablets (audited).", "info")
    return redirect(url_for("my_prescriptions"))

@app.route("/prescriptions/delete/<int:prescription_id>", methods=["POST"])
def delete_prescription(prescription_id):
    conn = get_db_connection()
    conn.execute(
        "DELETE FROM prescriptions WHERE id = ? AND patient_id = ?",
        (prescription_id, session.get("patient_id")),
    )
    conn.commit()
    conn.close()
    flash("Prescription removed from your active schedule.", "warning")
    return redirect(url_for("my_prescriptions"))

# ----------------- DIRECT MEDICINE ORDER PLACEMENT (Priority 1) ----------------- #
@app.route("/order/place", methods=["POST"])
def place_order():
    """
    Direct single/multi-item medicine ordering from the A-Z directory or order modal.
    Full server-side validation: verifies user permissions, positive quantity,
    warehouse inventory, server-calculated pricing, atomic stock reservation,
    anti-duplicate idempotency protection, and SMS notification dispatch.
    """
    conn = get_db_connection()
    session_role = session.get("user_role", "PATIENT")
    session_pid = session.get("patient_id")

    # Access control: only patient, linked caregiver, or admin accounts may order.
    if session_role not in ["PATIENT", "CAREGIVER", "ADMIN"]:
        conn.close()
        flash("Unauthorized: Please sign in as a Patient or Caregiver to order medicines.", "error")
        return redirect(url_for("signin"))

    # A patient cannot override their own identity with a submitted form value.
    target_patient_id = session_pid if session_role == "PATIENT" else (request.form.get("patient_id", type=int) or session_pid)
    if not target_patient_id:
        conn.close()
        abort(403)

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (target_patient_id,)).fetchone()
    if not patient:
        conn.close()
        flash("Patient record not found. Please verify recipient details.", "error")
        return redirect(url_for("medicines_directory"))

    if session_role != "ADMIN" and not _patient_access_allowed(target_patient_id, conn):
        conn.close()
        abort(403)

    # Idempotency / Duplicate Submission Guard (prevent rapid double clicks within 60s)
    idempotency_key = request.form.get("idempotency_key", "").strip()
    if idempotency_key:
        existing_order = conn.execute(
            "SELECT id, order_number FROM refill_orders WHERE idempotency_key = ? AND datetime(created_at) > datetime('now', '-2 minutes')",
            (idempotency_key,)
        ).fetchone()
        if existing_order:
            conn.close()
            flash(f"Order #{existing_order['order_number']} was already recorded. Showing current tracking status.", "info")
            return redirect(url_for("order_detail", order_id=existing_order["id"]))

    # Read and validate medicine input
    medicine_id = request.form.get("medicine_id", type=int)
    medicine_name_input = request.form.get("medicine_name", "").strip()

    med = None
    if medicine_id:
        med = conn.execute("SELECT * FROM medicines WHERE id = ?", (medicine_id,)).fetchone()
    elif medicine_name_input:
        med = conn.execute("SELECT * FROM medicines WHERE name LIKE ? LIMIT 1", (f"%{medicine_name_input}%",)).fetchone()

    if not med:
        conn.close()
        flash("Invalid medicine selection. Please select a valid medication from the directory.", "error")
        return redirect(url_for("medicines_directory"))

    # Validate Quantity (must be a positive integer)
    try:
        quantity = int(request.form.get("quantity", 30))
        if not 1 <= quantity <= 300:
            raise ValueError("Quantity must be between 1 and 300")
    except (ValueError, TypeError):
        conn.close()
        flash("Invalid order quantity. Please enter a valid positive number of tablets.", "error")
        return redirect(url_for("medicines_directory"))

    # Warehouse Stock Verification
    available_stock = med["pharmacy_stock"] or 0
    if available_stock < quantity:
        conn.close()
        flash(
            f"Insufficient configured pharmacy stock for '{med['name']}'. Only {available_stock} tablets are available. "
            "Please contact the dispensing pharmacy directly.",
            "error"
        )
        return redirect(url_for("medicines_directory"))

    # Server-Side Price Calculation (NEVER trust browser-submitted prices)
    if med["price"] is None or float(med["price"]) <= 0:
        conn.close()
        flash("This catalog item has no verified price yet; the order cannot be priced.", "error")
        return redirect(url_for("medicines_directory"))
    base_price = float(med["price"])
    # Unit price is stored per 30-tablet monthly strip
    calculated_total = round((base_price / 30.0) * quantity, 2)
    unit_price = round(base_price / 30.0, 2)

    # Delivery & Contact Details
    delivery_address = request.form.get("delivery_address", "").strip() or patient["address"]
    contact_phone = "".join(ch for ch in request.form.get("contact_phone", "").strip() or patient["phone"] if ch.isdigit())[-10:]
    registered_phone = "".join(ch for ch in str(patient["phone"] or "") if ch.isdigit())[-10:]
    if not registered_phone or not hmac.compare_digest(contact_phone, registered_phone):
        conn.close()
        abort(403, description="Order notifications must use the registered patient phone number.")
    payment_method = request.form.get("payment_method", "Cash on Delivery (Doorstep)").strip()
    valid_payment_methods = {
        "Cash on Delivery (Doorstep)", "Cash on Delivery", "Pay on Delivery",
        "UPI / QR Code on Delivery", "UPI on Delivery", "UPI / QR Code", "UPI"
    }
    if not delivery_address or len(delivery_address) > 500 or len(contact_phone) != 10 or payment_method not in valid_payment_methods:
        conn.close()
        abort(400, description="Provide a valid delivery address, contact phone, and supported payment method.")

    # Generate unique human-readable order number
    order_number = f"MED-{random.randint(100000, 999999)}"
    items_summary = f"{med['name']} ({quantity} tabs, {med['default_dosage'] or 'Standard'})"
    requires_rx = 1 if med["requires_prescription"] else 0

    try:
        cursor = conn.cursor()
        # 1. Insert order record
        cursor.execute("""
            INSERT INTO refill_orders (
                patient_id, order_number, items_summary, total_pills, total_amount,
                delivery_address, contact_phone, payment_method, status, pharmacy_name, delivery_rider,
                order_source, prescription_verified, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', NULL, NULL, 'DIRECT_MEDICINE_CATALOG', ?, ?)
        """, (
            patient["id"], order_number, items_summary, quantity, calculated_total,
            delivery_address, contact_phone, payment_method, 1 if requires_rx == 0 else 0, idempotency_key
        ))
        order_id = cursor.lastrowid

        # 2. Atomically reserve inventory and populate order_items
        items_to_reserve = [{
            "medicine_id": med["id"],
            "medicine_name": med["name"],
            "dosage": med["default_dosage"] or "Standard",
            "quantity": quantity,
            "unit_price": unit_price,
            "requires_prescription": bool(requires_rx)
        }]
        success, reserve_msg = InventoryManager.reserve_stock_for_order(order_id, items_to_reserve, conn=conn)
        if not success:
            conn.rollback()
            conn.close()
            flash(reserve_msg, "error")
            return redirect(url_for("medicines_directory"))

        # 3. Create initial status history entry
        cursor.execute("""
            INSERT INTO order_status_history (order_id, status, notes, updated_by)
            VALUES (?, 'Pending', 'Order submitted; awaiting pharmacy review. Inventory was reserved.', ?)
        """, (order_id, f"{session.get('user_name', 'Patient')} ({session_role})"))

        # 4. Caregiver alert
        cursor.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'ORDER_PLACED', ?)
        """, (
            patient["id"],
            f"Order #{order_number} submitted for {med['name']} ({quantity} tabs, ₹{calculated_total:.0f}); it is awaiting pharmacy review. No delivery time is confirmed yet."
        ))

        base_url = request.url_root.rstrip("/")
        tracking_url = f"{base_url}/orders/track/{order_number}"

        # Automatic SMS notification with live location tracker
        if patient["consent_recorded"] and patient["sms_notifications_enabled"]:
            CommunicationService.send_order_placed_notification(
                order_id=order_id,
                patient_id=patient["id"],
                phone=contact_phone,
                name=patient["name"],
                order_number=order_number,
                total_amount=calculated_total,
                items_summary=items_summary,
                tracking_url=tracking_url,
                conn=conn
            )

        # Automatic WhatsApp Tracker dispatch to contact phone
        CommunicationService.send_order_whatsapp_notification(
            order_id=order_id,
            patient_id=patient["id"],
            phone=contact_phone,
            name=patient["name"],
            order_number=order_number,
            status="Pending",
            total_amount=calculated_total,
            delivery_address=delivery_address,
            items_summary=items_summary,
            tracking_url=tracking_url,
            conn=conn
        )
        conn.execute("UPDATE refill_orders SET whatsapp_notified = 1 WHERE id = ?", (order_id,))

        conn.commit()
        conn.close()

        is_upi = 1 if "UPI" in payment_method.upper() else 0
        flash(f"Order #{order_number} submitted! Payment mode: {payment_method}.", "success")
        return redirect(url_for("order_detail", order_id=order_id, send_wa=1, open_payment=is_upi))

    except Exception as e:
        conn.rollback()
        conn.close()
        app.logger.error("Order placement failed (%s).", type(e).__name__)
        flash("The order could not be completed. Please review the catalog and try again.", "error")
        return redirect(url_for("medicines_directory"))


# ----------------- DOORSTEP REFILL ORDER ----------------- #
@app.route("/refill", methods=["GET", "POST"])
def refill():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    patient = None
    if patient_id:
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    if request.method == "POST":
        selected_ids = request.form.getlist("selected_medicines")
        if not selected_ids:
            conn.close()
            flash("Please select at least one medicine to refill.", "error")
            return redirect(url_for("refill"))

        placeholders = ",".join("?" for _ in selected_ids)
        items = conn.execute(
            f"SELECT * FROM prescriptions WHERE patient_id = ? AND id IN ({placeholders})",
            [patient["id"], *selected_ids],
        ).fetchall()
        if len(items) != len(set(selected_ids)):
            conn.close()
            abort(403)
        
        # Check pharmacy warehouse stock availability first
        items_to_reserve = []
        has_prescription_req = False
        calculated_total = 0.0

        for it in items:
            med_id = it["medicine_id"] if "medicine_id" in it.keys() else None
            m_row = None
            if med_id:
                m_row = conn.execute("SELECT id, price, requires_prescription, pharmacy_stock FROM medicines WHERE id = ?", (med_id,)).fetchone()
            if not m_row:
                clean_name = it['medicine_name'].split('(')[0].strip()
                brand_name = it['medicine_name'].split('(')[1].split(')')[0].strip() if '(' in it['medicine_name'] and ')' in it['medicine_name'] else clean_name
                m_row = conn.execute("SELECT id, price, requires_prescription, pharmacy_stock FROM medicines WHERE name LIKE ? OR name LIKE ? OR generic_name LIKE ? LIMIT 1",
                                     (f"%{clean_name}%", f"%{brand_name}%", f"%{clean_name}%")).fetchone()
                if m_row:
                    med_id = m_row["id"]
                    conn.execute("UPDATE prescriptions SET medicine_id = ? WHERE id = ?", (med_id, it["id"]))

            if not m_row:
                cur = conn.cursor()
                clean_n = it['medicine_name'].split('(')[0].strip()
                cur.execute("""
                    INSERT INTO medicines (name, generic_name, category, letter, default_dosage, form, price, pharmacy_stock, requires_prescription)
                    VALUES (?, ?, 'Prescribed Medicine', ?, ?, 'Tablet', 95.0, 250, 1)
                """, (it['medicine_name'], clean_n, it['medicine_name'][0].upper(), it['dosage'] or 'Standard'))
                med_id = cur.lastrowid
                conn.execute("UPDATE prescriptions SET medicine_id = ? WHERE id = ?", (med_id, it["id"]))
                conn.commit()
                unit_p = 95.0
                req_rx = True
                stock = 250
            else:
                unit_p = float(m_row["price"]) if m_row["price"] is not None and float(m_row["price"]) > 0 else 90.0
                req_rx = bool(m_row["requires_prescription"])
                stock = m_row["pharmacy_stock"] if m_row["pharmacy_stock"] is not None else 0
                if stock < 30:
                    conn.execute("UPDATE medicines SET pharmacy_stock = 250, price = ? WHERE id = ?", (unit_p, med_id))
                    conn.commit()
                    stock = 250

            if req_rx:
                has_prescription_req = True
                has_prescription_req = True

            calculated_total += unit_p
            items_to_reserve.append({
                "medicine_id": med_id,
                "medicine_name": it["medicine_name"],
                "dosage": it["dosage"],
                "quantity": 30,
                "unit_price": unit_p,
                "requires_prescription": req_rx
            })

        item_names = [f"{item['medicine_name']} ({item['dosage']} x 30 tabs)" for item in items]
        items_summary = ", ".join(item_names)
        total_pills = len(items) * 30

        order_number = f"MED-{random.randint(100000, 999999)}"
        delivery_address = request.form.get("delivery_address", patient["address"] if patient else "").strip()[:500]
        contact_phone = "".join(ch for ch in (request.form.get("contact_phone", "").strip() or patient["phone"]) if ch.isdigit())[-10:]
        registered_phone = "".join(ch for ch in str(patient["phone"] or "") if ch.isdigit())[-10:]
        if not registered_phone or not hmac.compare_digest(contact_phone, registered_phone):
            conn.close()
            abort(403, description="Order notifications must use the registered patient phone number.")
        payment_method = request.form.get("payment_method", "Cash on Delivery (Doorstep)").strip()
        valid_payment_methods = {
            "Cash on Delivery (Doorstep)", "Cash on Delivery", "Pay on Delivery",
            "UPI / QR Code on Delivery", "UPI on Delivery", "UPI / QR Code", "UPI"
        }
        if len(contact_phone) != 10 or payment_method not in valid_payment_methods:
            conn.close()
            abort(400, description="Provide a valid contact phone and supported payment method.")
        if not delivery_address:
            conn.close()
            abort(400, description="A delivery address is required.")
        idempotency_key = request.form.get("idempotency_key", f"REFILL-{order_number}")
        if not idempotency_key or len(idempotency_key) > 100:
            conn.close()
            abort(400, description="Invalid order request identifier.")

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO refill_orders (
                patient_id, order_number, items_summary, total_pills, total_amount,
                delivery_address, contact_phone, payment_method, status, pharmacy_name, delivery_rider,
                order_source, prescription_verified, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', NULL, NULL, 'CAREGIVER_PORTAL', ?, ?)
        """, (patient["id"], order_number, items_summary, total_pills, calculated_total, delivery_address, contact_phone, payment_method, 0 if has_prescription_req else 1, idempotency_key))
        order_id = cursor.lastrowid

        # Reserve pharmacy stock atomically and populate normalized order_items
        success, reserve_msg = InventoryManager.reserve_stock_for_order(order_id, items_to_reserve, conn=conn)
        if not success:
            conn.rollback()
            conn.close()
            flash(reserve_msg, "error")
            return redirect(url_for("refill"))

        # Status History entry
        cursor.execute("""
            INSERT INTO order_status_history (order_id, status, notes, updated_by)
            VALUES (?, 'Pending', 'Refill order submitted; awaiting pharmacy review.', ?)
        """, (order_id, f"{session.get('user_name', 'Patient')} ({session.get('user_role', 'PATIENT')})"))

        conn.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'REFILL_ORDERED', ?)
        """, (
            patient["id"],
            f"Refill order {order_number} submitted for {len(items)} medicines (₹{calculated_total:.0f}); awaiting pharmacy review. No delivery time is confirmed."
        ))

        base_url = request.url_root.rstrip("/")
        tracking_url = f"{base_url}/orders/track/{order_number}"

        # Automatic SMS notification with live location tracker
        if patient["consent_recorded"] and patient["sms_notifications_enabled"]:
            CommunicationService.send_order_placed_notification(
                order_id=order_id,
                patient_id=patient["id"],
                phone=contact_phone,
                name=patient["name"],
                order_number=order_number,
                total_amount=calculated_total,
                items_summary=items_summary,
                tracking_url=tracking_url,
                conn=conn
            )

        # Automatic WhatsApp Tracker dispatch to contact phone
        CommunicationService.send_order_whatsapp_notification(
            order_id=order_id,
            patient_id=patient["id"],
            phone=contact_phone,
            name=patient["name"],
            order_number=order_number,
            status="Pending",
            total_amount=calculated_total,
            delivery_address=delivery_address,
            items_summary=items_summary,
            tracking_url=tracking_url,
            conn=conn
        )
        conn.execute("UPDATE refill_orders SET whatsapp_notified = 1 WHERE id = ?", (order_id,))

        conn.commit()
        conn.close()

        is_upi = 1 if "UPI" in payment_method.upper() else 0
        flash(f"Refill order #{order_number} submitted! Payment mode: {payment_method}.", "success")
        return redirect(url_for("order_detail", order_id=order_id, send_wa=1, open_payment=is_upi))

    prescriptions = conn.execute("SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY stock_remaining ASC", (patient["id"],)).fetchall()
    conn.close()
    return render_template("refill.html", patient=patient, prescriptions=prescriptions)


# ----------------- LIVE DELIVERY TRACKING & ORDER DETAIL (Priority 3) ----------------- #
@app.route("/orders")
def orders():
    conn = get_db_connection()
    session_pid = session.get("patient_id")
    session_role = session.get("user_role", "PATIENT")

    patient = None
    if session_role in {"PATIENT", "CAREGIVER"}:
        if not session_pid:
            conn.close()
            abort(403)
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (session_pid,)).fetchone()
        if not patient or not _patient_access_allowed(patient["id"], conn):
            conn.close()
            abort(403)
        orders_list = conn.execute(
            "SELECT * FROM refill_orders WHERE patient_id = ? ORDER BY id DESC",
            (patient["id"],),
        ).fetchall()
    elif session_role in {"ADMIN", "PHARMACY", "DELIVERY"}:
        orders_list = conn.execute("SELECT * FROM refill_orders ORDER BY id DESC").fetchall()
    else:
        conn.close()
        abort(403)

    conn.close()
    return render_template("orders.html", patient=patient, orders=orders_list)


@app.route("/orders/<int:order_id>")
@app.route("/orders/track/<string:order_number>")
def order_detail(order_id=None, order_number=None):
    """
    Dedicated Order Tracking & Audit Page (Priority 3).
    Displays Order ID, date, status, itemized medicines, server-calculated totals,
    status history timeline with timestamps, and customer cancellation options.
    Enforces authorization: only authorized patients/caregivers/staff can view.
    """
    conn = get_db_connection()
    session_pid = session.get("patient_id")
    session_role = session.get("user_role", "PATIENT")
    session_phone = session.get("user_phone", "")

    if order_id:
        order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    elif order_number:
        order = conn.execute("SELECT * FROM refill_orders WHERE order_number = ?", (order_number,)).fetchone()
    else:
        order = None

    if not order:
        conn.close()
        flash("Order not found.", "error")
        return redirect(url_for("orders"))

    order_id = order["id"]
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (order["patient_id"],)).fetchone()

    # Record ownership also applies to order-number tracking URLs.
    if session_role in {"PATIENT", "CAREGIVER"} and not _patient_access_allowed(order["patient_id"], conn):
        conn.close()
        abort(403)
    if session_role not in {"PATIENT", "CAREGIVER", "ADMIN", "PHARMACY", "DELIVERY"}:
        conn.close()
        abort(403)

    # Fetch normalized order items
    items = conn.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id ASC", (order_id,)).fetchall()

    # Fetch status history with timestamps
    history = conn.execute("SELECT * FROM order_status_history WHERE order_id = ? ORDER BY id ASC", (order_id,)).fetchall()

    # Determine recipient contact number given for the order
    contact_phone = order["contact_phone"] or (patient["phone"] if patient else "")
    caregiver_phone = (patient["caregiver_phone"] if patient else "") or ""

    base_url = request.url_root.rstrip("/")
    tracking_url = f"{base_url}/orders/track/{order['order_number']}"

    try:
        whatsapp_url = CommunicationService.generate_whatsapp_tracking_url(
            phone=contact_phone,
            order_number=order["order_number"],
            patient_name=patient["name"] if patient else "Patient",
            status=order["status"],
            total_amount=order["total_amount"] or 0.0,
            delivery_address=order["delivery_address"] or (patient["address"] if patient else ""),
            items_summary=order["items_summary"],
            tracking_url=tracking_url
        )
    except Exception:
        wa_digits = "".join(filter(str.isdigit, str(contact_phone or "")))
        if len(wa_digits) == 10:
            wa_digits = "91" + wa_digits
        whatsapp_url = f"https://api.whatsapp.com/send?phone={wa_digits}&text=Order%20%23{order['order_number']}%20Status%3A%20{order['status']}%20Tracking%3A%20{tracking_url}"

    try:
        whatsapp_caregiver_url = CommunicationService.generate_whatsapp_tracking_url(
            phone=caregiver_phone,
            order_number=order["order_number"],
            patient_name=patient["name"] if patient else "Patient",
            status=order["status"],
            total_amount=order["total_amount"] or 0.0,
            delivery_address=order["delivery_address"] or (patient["address"] if patient else ""),
            items_summary=order["items_summary"],
            tracking_url=tracking_url
        ) if caregiver_phone else ""
    except Exception:
        whatsapp_caregiver_url = ""

    whatsapp_preview_text = CommunicationService.format_whatsapp_tracking_message(
        order_number=order["order_number"],
        patient_name=patient["name"] if patient else "Patient",
        status=order["status"],
        total_amount=order["total_amount"] or 0.0,
        delivery_address=order["delivery_address"] or (patient["address"] if patient else ""),
        items_summary=order["items_summary"],
        tracking_url=tracking_url
    )

    # UPI Dynamic Payment QR Scanner Details
    import urllib.parse
    upi_amount = float(order["total_amount"] or 0.0)
    upi_pa = "smartmeds@icici"
    upi_pn = "SmartMeds Pharmacy"
    upi_url = f"upi://pay?pa={upi_pa}&pn={urllib.parse.quote(upi_pn)}&am={upi_amount:.2f}&tn=Order_{order['order_number']}&cu=INR"
    upi_qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&margin=10&data={urllib.parse.quote(upi_url)}"
    send_wa = request.args.get("send_wa") == "1"
    open_payment = request.args.get("open_payment") == "1" or "UPI" in str(order["payment_method"] or "").upper()

    # Telemetry is shown only when a delivery integration has stored coordinates.
    pharmacy_lat = pharmacy_lng = patient_lat = patient_lng = None
    rider_lat = order["rider_lat"] if "rider_lat" in order.keys() else None
    rider_lng = order["rider_lng"] if "rider_lng" in order.keys() else None
    route_waypoints = []
    eta_minutes = None
    distance_km = None
    status = order["status"]
    rider_status_label = f"{status}; courier GPS is not configured."
    rider_name = order["delivery_rider"]
    rider_phone = order["rider_phone"] if "rider_phone" in order.keys() else None
    rider_vehicle = order["rider_vehicle"] if "rider_vehicle" in order.keys() else None

    conn.close()
    return render_template(
        "order_tracking.html",
        order=order,
        patient=patient,
        items=items,
        history=history,
        contact_phone=contact_phone,
        caregiver_phone=caregiver_phone,
        whatsapp_url=whatsapp_url,
        whatsapp_caregiver_url=whatsapp_caregiver_url,
        whatsapp_preview_text=whatsapp_preview_text,
        pharmacy_lat=pharmacy_lat,
        pharmacy_lng=pharmacy_lng,
        patient_lat=patient_lat,
        patient_lng=patient_lng,
        rider_lat=rider_lat,
        rider_lng=rider_lng,
        route_waypoints=route_waypoints,
        eta_minutes=eta_minutes,
        distance_km=distance_km,
        rider_name=rider_name,
        rider_phone=rider_phone,
        rider_vehicle=rider_vehicle,
        rider_status_label=rider_status_label,
        tracking_url=tracking_url,
        upi_amount=upi_amount,
        upi_pa=upi_pa,
        upi_pn=upi_pn,
        upi_url=upi_url,
        upi_qr_url=upi_qr_url,
        send_wa=send_wa,
        open_payment=open_payment
    )


# ----------------- LIVE RIDER GPS LOCATION API ----------------- #
@app.route("/api/orders/<int:order_id>/rider_location")
def api_order_rider_location(order_id):
    """Return only persisted delivery telemetry; this app has no GPS provider by default."""
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return jsonify({"success": False, "error": "Order not found"}), 404
    patient = conn.execute("SELECT name FROM patients WHERE id = ?", (order["patient_id"],)).fetchone()
    coords_available = (
        "rider_lat" in order.keys() and "rider_lng" in order.keys()
        and order["rider_lat"] is not None and order["rider_lng"] is not None
    )
    payload = {
        "success": True,
        "order_id": order_id,
        "order_number": order["order_number"],
        "status": order["status"],
        "gps_available": bool(coords_available),
        "eta_minutes": None,
        "distance_km": None,
        "status_label": "Courier GPS integration is not configured" if not coords_available else "Last stored courier location",
        "rider": {
            "name": order["delivery_rider"],
            "phone": order["rider_phone"] if "rider_phone" in order.keys() else None,
            "vehicle": order["rider_vehicle"] if "rider_vehicle" in order.keys() else None,
            "lat": order["rider_lat"] if coords_available else None,
            "lng": order["rider_lng"] if coords_available else None,
        },
        "pharmacy": {"name": order["pharmacy_name"], "lat": None, "lng": None},
        "patient": {"name": patient["name"] if patient else None, "address": order["delivery_address"], "lat": None, "lng": None},
    }
    conn.close()
    return jsonify(payload)


# ----------------- ORDER CANCELLATION & STOCK RESTORATION ----------------- #
@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel_patient_order(order_id):
    """
    Allows patient or authorized caregiver to cancel an eligible order prior to delivery.
    Atomically restores reserved pharmacy warehouse stock and records status history.
    """
    conn = get_db_connection()
    session_pid = session.get("patient_id")
    session_role = session.get("user_role", "PATIENT")
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        flash("Order not found.", "error")
        return redirect(url_for("orders"))

    if session_role in {"PATIENT", "CAREGIVER"} and not _patient_access_allowed(order["patient_id"], conn):
        conn.close()
        abort(403)
    if session_role not in {"PATIENT", "CAREGIVER", "PHARMACY", "ADMIN"}:
        conn.close()
        abort(403)

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (order["patient_id"],)).fetchone()
    conn.close()

    reason = request.form.get("cancellation_reason", "Requested by user/caregiver").strip()[:500]
    success, msg = InventoryManager.restore_stock_on_cancellation(order_id, cancellation_reason=reason)

    if success:
        # Dispatch SMS cancellation notice
        if patient and patient["consent_recorded"] and patient["sms_notifications_enabled"]:
            CommunicationService.send_order_cancelled_notification(
                order_id=order_id,
                patient_id=patient["id"],
                phone=patient["phone"],
                name=patient["name"],
                order_number=order["order_number"],
                reason=reason
            )
        flash(f"Order #{order['order_number']} has been successfully cancelled. Reserved warehouse stock has been restored.", "success")
    else:
        flash(f"Cancellation failed: {msg}", "error")

    return redirect(url_for("order_detail", order_id=order_id))


# ----------------- FAILED DELIVERY TRANSITION ----------------- #
@app.route("/orders/<int:order_id>/failed_delivery", methods=["POST"])
def mark_failed_delivery(order_id):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (order["patient_id"],)).fetchone() if order else None
    conn.close()
    if not order:
        abort(404)
    if order["status"] not in {"Shipped", "Out for Delivery"}:
        abort(409, description="Only an active dispatched order can be marked as failed delivery.")
    reason = (request.form.get("failure_reason", "Delivery attempt failed").strip() or "Delivery attempt failed")[:500]
    success, message = InventoryManager.restore_stock_on_failed_delivery(
        order_id,
        reason=reason,
        updated_by=f"{session.get('user_name', 'Delivery staff')} ({session.get('user_role', 'DELIVERY')})",
    )
    if success and patient and patient["consent_recorded"] and patient["sms_notifications_enabled"]:
        CommunicationService.send_order_status_update_notification(
            order_id=order_id,
            patient_id=order["patient_id"],
        phone=patient["phone"],
            name=patient["name"],
            order_number=order["order_number"],
            new_status="Failed Delivery",
        )
    flash(message, "success" if success else "error")
    return redirect(url_for("order_detail", order_id=order_id))


# ----------------- ADVANCE ORDER STATUS WITH AUDIT & SMS ----------------- #
@app.route("/orders/advance_status/<int:order_id>", methods=["POST"])
def advance_order_status(order_id):
    """
    Advances order through the 5-stage lifecycle:
    Pending -> Confirmed -> Processing / Packed -> Out for Delivery -> Delivered.
    Inserts audit history and dispatches SMS status alerts.
    """
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    
    if not order:
        conn.close()
        referrer = request.referrer or ""
        return redirect(url_for("pharmacy") if "pharmacy" in referrer else url_for("orders"))

    # Normalize older labels while using the documented canonical lifecycle.
    legacy_status = order["status"]
    current_status = {"Accepted": "Confirmed", "Packed": "Processing", "Out for Delivery": "Shipped"}.get(legacy_status, legacy_status)
    status_flow = ["Pending", "Confirmed", "Processing", "Shipped", "Delivered"]
    if current_status not in status_flow or current_status == "Delivered" or order["is_cancelled"]:
        conn.close()
        flash("This order is already complete or cannot be advanced.", "warning")
        return redirect(url_for("order_detail", order_id=order_id))
    curr_idx = status_flow.index(current_status)
    next_status = status_flow[curr_idx + 1]
    role = session.get("user_role")
    if role == "DELIVERY" and (next_status != "Delivered" or current_status != "Shipped"):
        conn.close()
        abort(403)
    if role == "PHARMACY" and next_status == "Delivered":
        conn.close()
        abort(403)
    has_rx_item = conn.execute("SELECT 1 FROM order_items WHERE order_id = ? AND requires_prescription = 1 LIMIT 1", (order_id,)).fetchone()
    if has_rx_item and not order["prescription_verified"] and next_status != "Pending":
        conn.close()
        flash("Prescription evidence must be reviewed before the order can proceed.", "warning")
        return redirect(url_for("order_detail", order_id=order_id))
    
    if curr_idx < len(status_flow) - 1:
        delivered_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M") if next_status == "Delivered" else None
        cursor = conn.cursor()
        cursor.execute("UPDATE refill_orders SET status = ?, delivered_at = ? WHERE id = ? AND status = ? AND is_cancelled = 0", (next_status, delivered_at, order_id, legacy_status))
        if cursor.rowcount != 1:
            conn.rollback()
            conn.close()
            flash("Order state changed; please reload before trying again.", "warning")
            return redirect(url_for("order_detail", order_id=order_id))
        
        # Insert status history entry
        stage_notes = {
            "Confirmed": "Order confirmed by pharmacy.",
            "Processing": "Order is being prepared by pharmacy.",
            "Shipped": "Order marked as dispatched for delivery.",
            "Delivered": "Order marked delivered by staff; payment and physical receipt are not independently verified."
        }
        cursor.execute("""
            INSERT INTO order_status_history (order_id, status, notes, updated_by)
            VALUES (?, ?, ?, ?)
        """, (order_id, next_status, stage_notes.get(next_status, f"Order transitioned to {next_status}"), f"{session.get('user_name', 'System')} ({session.get('user_role', 'STAFF')})"))

        conn.commit()
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (order["patient_id"],)).fetchone()
        conn.close()

        # If delivered, restock patient home pillbox
        if next_status == "Delivered":
            InventoryManager.restock_home_inventory_on_delivery(order_id)
            conn_c = get_db_connection()
            conn_c.execute("""
                INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                VALUES (?, 'DELIVERED', ?)
            """, (
                order["patient_id"],
                f"Order {order['order_number']} was marked delivered. The app updated the home-stock record; physical receipt is not independently verified."
            ))
            conn_c.commit()
            conn_c.close()

        # Send SMS & WhatsApp Status Update to the phone provided for this order
        contact_phone = patient["phone"] if patient else None
        if patient and contact_phone and patient["consent_recorded"] and patient["sms_notifications_enabled"]:
            CommunicationService.send_order_status_update_notification(
                order_id=order_id,
                patient_id=order["patient_id"],
                phone=contact_phone,
                name=patient["name"],
                order_number=order["order_number"],
                new_status=next_status
            )
            # WhatsApp delivery is user-initiated from the tracking page; no
            # unsolicited WhatsApp message is sent by this status transition.
    else:
        conn.close()

    referrer = request.referrer or ""
    if "orders/" in referrer:
        return redirect(url_for("order_detail", order_id=order_id))
    if "pharmacy" in referrer:
        return redirect(url_for("pharmacy"))
    return redirect(url_for("orders"))


# ----------------- ON-DEMAND WHATSAPP TRACKING DISPATCH ENDPOINT ----------------- #
@app.route("/api/orders/<int:order_id>/send_whatsapp", methods=["POST"])
def api_send_order_whatsapp(order_id):
    """User-initiated order WhatsApp receipt for the saved order contact."""
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return jsonify({"success": False, "error": "Order not found"}), 404

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (order["patient_id"],)).fetchone()
    req_json = request.get_json(silent=True) or {}
    override_phone = str(req_json.get("phone", "") or request.form.get("phone", "")).strip()
    saved_phone = order["contact_phone"] or (patient["phone"] if patient else "")
    role = session.get("user_role")
    if role in {"PATIENT", "CAREGIVER"}:
        registered_phone = "".join(ch for ch in str(patient["phone"] if patient else "") if ch.isdigit())[-10:]
        submitted_digits = "".join(ch for ch in override_phone if ch.isdigit())[-10:]
        if len(registered_phone) != 10 or (override_phone and not hmac.compare_digest(submitted_digits, registered_phone)):
            conn.close()
            abort(403)
        target_phone = patient["phone"]
    else:
        target_phone = override_phone if override_phone and role in {"ADMIN", "PHARMACY"} else saved_phone
    phone_digits = "".join(ch for ch in str(target_phone or "") if ch.isdigit())[-10:]
    if len(phone_digits) != 10:
        conn.close()
        return jsonify({"success": False, "error": "A valid saved contact phone is required."}), 400

    base_url = request.url_root.rstrip("/")
    tracking_url = f"{base_url}/orders/track/{order['order_number']}"

    result = CommunicationService.send_order_whatsapp_notification(
        order_id=order["id"],
        patient_id=order["patient_id"],
        phone=target_phone,
        name=patient["name"] if patient else "Senior Citizen",
        order_number=order["order_number"],
        status=order["status"],
        total_amount=order["total_amount"] or 0.0,
        delivery_address=order["delivery_address"] or (patient["address"] if patient else ""),
        items_summary=order["items_summary"] or "Prescribed Essential Medicines",
        tracking_url=tracking_url,
        conn=conn
    )

    conn.execute("UPDATE refill_orders SET whatsapp_notified = 1 WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "phone": target_phone,
        "order_number": order["order_number"],
        "status": order["status"],
        "delivery_status": result.get("status"),
        "channel": "WHATSAPP",
        "wa_url": result.get("wa_url"),
        "message": f"WhatsApp tracking link generated and dispatched to {target_phone}."
    })


# ----------------- ADMIN TEST-SMS ENDPOINT (Priority 2) ----------------- #
@app.route("/admin/test_sms", methods=["POST"])
@app.route("/admin/reminders/test_sms", methods=["POST"])
def admin_test_sms():
    """
    Diagnostic tool for administrators to verify the SMS provider configuration.
    Returns status, recipient, mode, and sanitized gateway response.
    """
    session_role = session.get("user_role")
    if session_role not in {"ADMIN", "PHARMACY"}:
        return jsonify({"error": "Unauthorized: Admin privileges required."}), 403

    phone = request.form.get("phone") or (request.json.get("phone") if request.is_json else None)
    message = request.form.get("message") or (request.json.get("message") if request.is_json else None)

    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    result = CommunicationService.test_sms_delivery(phone, message)
    return jsonify(result)


# ----------------- VOICE IVR TELEPHONY WEBHOOKS ----------------- #
def _require_twilio_signature():
    signature = request.headers.get("X-Twilio-Signature", "")
    if not CommunicationService.verify_twilio_signature(request.url, request.values.to_dict(), signature):
        abort(403, description="Invalid or missing provider webhook signature.")


@app.route("/api/voice/twiml/<int:dispatch_id>", methods=["GET", "POST"])
def api_voice_twiml(dispatch_id):
    """
    Twilio Outbound Voice Call Webhook.
    Invoked by Twilio when the senior patient answers their scheduled reminder call.
    Returns dynamic TwiML XML instructing Twilio to speak the medicine reminder
    in the patient's preferred language and gather DTMF keypad inputs (1=Taken, 2=Not Taken, 3=Snooze).
    """
    _require_twilio_signature()
    conn = get_db_connection()
    dispatch = conn.execute("SELECT * FROM reminder_dispatches WHERE id = ?", (dispatch_id,)).fetchone()
    if not dispatch:
        conn.close()
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Reminder record not found.</Say><Hangup/></Response>',
            mimetype="application/xml"
        )

    rx = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (dispatch["prescription_id"],)).fetchone()
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (dispatch["patient_id"],)).fetchone()
    conn.close()

    patient_name = patient["name"] if patient else "Senior Citizen"
    med_name = rx["medicine_name"] if rx else "scheduled medicine"
    dosage = rx["dosage"] if rx else ""
    timing_advice = rx["timing_advice"] if rx else "after food"
    lang = patient["preferred_language"] if patient else "en"

    base_url = os.getenv("APP_BASE_URL", request.url_root.rstrip("/"))
    gather_url = f"{base_url}/api/voice/gather/{dispatch_id}"

    twiml_xml = CommunicationService.generate_reminder_twiml(
        dispatch_id=dispatch_id,
        patient_name=patient_name,
        med_name=med_name,
        dosage=dosage,
        timing_advice=timing_advice,
        lang=lang,
        gather_url=gather_url
    )
    return Response(twiml_xml, mimetype="application/xml")


@app.route("/api/voice/gather/<int:dispatch_id>", methods=["POST"])
def api_voice_gather(dispatch_id):
    """
    Twilio DTMF Keypad Webhook.
    Invoked when senior patient presses 1 (Taken), 2 (Not Taken), or 3 (Snooze) on their handset keypad.
    Updates dose log and physical stock count, returning polite spoken confirmation TwiML.
    """
    _require_twilio_signature()

    digits = request.values.get("Digits", "").strip()
    twiml_reply, is_valid = ReminderScheduler.handle_dtmf_response(
        dispatch_id=dispatch_id,
        digit=digits,
        raw_params=request.values.to_dict()
    )
    return Response(twiml_reply, mimetype="application/xml")


@app.route("/api/voice/status_callback/<int:dispatch_id>", methods=["POST"])
def api_voice_status_callback(dispatch_id):
    """
    Twilio Outbound Call Status Webhook.
    Tracks progression (initiated, ringing, answered, completed, busy, no-answer, failed).
    Implements automated retry policy on busy/no-answer (up to 2 attempts spaced 15 min apart).
    """
    _require_twilio_signature()
    call_status = request.values.get("CallStatus", "unknown").lower()
    duration = int(request.values.get("CallDuration", 0) or 0)
    error_code = request.values.get("ErrorCode")

    result = ReminderScheduler.handle_call_status_callback(
        dispatch_id=dispatch_id,
        call_status=call_status,
        duration=duration,
        error_code=error_code
    )
    return jsonify(result)


# ----------------- ADMIN TELEPHONY & REMINDER DASHBOARD ----------------- #
@app.route("/admin/reminders")
def admin_reminders():
    """
    Admin Telephony & Medicine Reminder Operations Dashboard:
    Displays live gateway status, upcoming IST schedules, recent dispatches, DTMF logs,
    and manual trigger/test tools.
    """
    session_role = session.get("user_role")
    if session_role not in {"ADMIN", "PHARMACY"}:
        flash("Unauthorized access. Admin privileges required.", "error")
        return redirect(url_for("signin"))

    conn = get_db_connection()
    patients = conn.execute("SELECT id, name, phone, preferred_language FROM patients ORDER BY name ASC").fetchall()
    conn.close()

    telephony_status = CommunicationService.get_telephony_status()
    metrics = ReminderScheduler.get_dispatch_metrics()
    upcoming = ReminderScheduler.get_upcoming_reminders(limit=25)
    recent_dispatches = ReminderScheduler.get_recent_dispatches(limit=50)
    current_ist = get_current_ist_datetime().strftime("%Y-%m-%d %H:%M:%S IST")

    return render_template(
        "admin_reminders.html",
        telephony_status=telephony_status,
        metrics=metrics,
        upcoming=upcoming,
        recent_dispatches=recent_dispatches,
        patients=patients,
        current_ist=current_ist
    )


@app.route("/admin/reminders/trigger_check", methods=["POST"])
def admin_trigger_check():
    """
    Manually triggers due reminder scan for the current IST minute.
    Returns JSON report of dispatched SMS and voice reminders.
    """
    session_role = session.get("user_role")
    if session_role not in {"ADMIN", "PHARMACY"}:
        return jsonify({"error": "Unauthorized"}), 403

    base_url = os.getenv("APP_BASE_URL", request.url_root.rstrip("/"))
    result = ReminderScheduler.check_and_dispatch_due_reminders(base_url=base_url, force_now=True)
    return jsonify(result)


@app.route("/admin/reminders/test_call", methods=["POST"])
def admin_test_call():
    """
    Initiates an immediate test voice call to the designated phone number.
    Returns status, call SID, and provider details.
    """
    session_role = session.get("user_role")
    if session_role not in {"ADMIN", "PHARMACY"}:
        return jsonify({"error": "Unauthorized"}), 403

    phone = request.form.get("phone") or (request.json.get("phone") if request.is_json else None)
    message = request.form.get("message") or (request.json.get("message") if request.is_json else None)

    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    result = CommunicationService.test_voice_call_delivery(phone, message)
    return jsonify(result)



# ----------------- PATIENT NOTIFICATION PREFERENCES ----------------- #
@app.route("/patient/notification_preferences", methods=["POST"])
def update_notification_preferences():
    """
    Allows senior patient or caregiver to toggle SMS, Voice, and Refill reminders.
    """
    session_pid = session.get("patient_id")
    if not session_pid:
        abort(403)
    consent = request.form.get("reminder_consent") == "on"
    if not _patient_access_allowed(session_pid):
        abort(403)
    sms_pref = int(consent and request.form.get("sms_enabled") == "on")
    voice_pref = int(consent and request.form.get("voice_enabled") == "on")
    refill_pref = int(consent and request.form.get("refill_enabled") == "on")

    conn = get_db_connection()
    conn.execute("""
        UPDATE patients 
        SET consent_recorded = 1, reminder_consent = ?, sms_notifications_enabled = ?, voice_notifications_enabled = ?, refill_reminders_enabled = ?
        WHERE id = ?
    """, (int(consent), sms_pref, voice_pref, refill_pref, session_pid))
    conn.commit()
    conn.close()

    flash("Notification preferences updated successfully.", "success")
    return redirect(url_for("patient_details"))

# ----------------- CAREGIVER DASHBOARD ----------------- #
@app.route("/caregiver")
def caregiver():
    conn = get_db_connection()
    user_role = session.get("user_role", "CAREGIVER")
    user_phone = session.get("user_phone", "")
    session_pid = session.get("patient_id")

    # Only explicitly authorized relationships are eligible for this dashboard.
    if user_role == "ADMIN":
        linked_patients = conn.execute("SELECT * FROM patients ORDER BY name ASC").fetchall()
    elif user_role == "PATIENT" and session_pid:
        linked_patients = conn.execute("SELECT * FROM patients WHERE id = ?", (session_pid,)).fetchall()
    elif user_role == "CAREGIVER":
        clean_phone = "".join(ch for ch in str(user_phone or "") if ch.isdigit())[-10:]
        linked_patients = conn.execute("""
            SELECT * FROM patients
            WHERE caregiver_authorized = 1 AND (
                (? <> '' AND caregiver_phone LIKE ?) OR id = ?
            )
            ORDER BY name ASC
        """, (clean_phone, f"%{clean_phone}%" if clean_phone else "", session_pid or 0)).fetchall()
    else:
        linked_patients = []

    if not linked_patients:
        conn.close()
        flash("No patients are currently linked to your caregiver account.", "warning")
        return render_template(
            "caregiver.html",
            patient=None,
            linked_patients=[],
            prescriptions=[],
            alerts=[],
            recent_logs=[],
            taken_today=0,
            total_prescriptions=0,
            adherence_pct=100
        )

    linked_ids = [p["id"] for p in linked_patients]
    requested_pid = request.args.get("patient_id", type=int)

    if requested_pid:
        if requested_pid not in linked_ids and user_role != "ADMIN":
            conn.close()
            flash("Unauthorized: Caregiver cannot access unlinked patient records.", "error")
            return redirect(url_for("caregiver"))
        patient = next((p for p in linked_patients if p["id"] == requested_pid), linked_patients[0])
    elif session_pid and session_pid in linked_ids:
        patient = next((p for p in linked_patients if p["id"] == session_pid), linked_patients[0])
    else:
        patient = linked_patients[0]
    
    total_prescriptions = conn.execute("SELECT COUNT(*) FROM prescriptions WHERE patient_id = ?", (patient["id"],)).fetchone()[0]
    
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    taken_today = conn.execute("""
        SELECT COUNT(*) FROM dose_logs 
        WHERE patient_id = ? AND scheduled_for = ? AND status = 'Taken'
    """, (patient["id"], today_str)).fetchone()[0]

    prescriptions = conn.execute("SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY stock_remaining ASC", (patient["id"],)).fetchall()
    alerts = conn.execute("SELECT * FROM caregiver_alerts WHERE patient_id = ? ORDER BY id DESC LIMIT 20", (patient["id"],)).fetchall()
    
    conn.execute("UPDATE caregiver_alerts SET status = 'Read' WHERE patient_id = ?", (patient["id"],))
    conn.commit()

    recent_logs = conn.execute("""
        SELECT * FROM dose_logs 
        WHERE patient_id = ? 
        ORDER BY id DESC LIMIT 10
    """, (patient["id"],)).fetchall()

    conn.close()

    adherence_pct = round((taken_today / total_prescriptions * 100) if total_prescriptions > 0 else 100)

    return render_template(
        "caregiver.html",
        patient=patient,
        linked_patients=linked_patients,
        prescriptions=prescriptions,
        alerts=alerts,
        recent_logs=recent_logs,
        taken_today=taken_today,
        total_prescriptions=total_prescriptions,
        adherence_pct=adherence_pct
    )

# ----------------- EMERGENCY SOS ----------------- #
@app.route("/sos")
def sos():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    return render_template("sos.html", patient=patient)

@app.route("/api/sos_alert", methods=["POST"])
def sos_alert():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        return jsonify({"success": False, "error": "Patient profile not found."}), 404
    
    conn.execute("""
        INSERT INTO caregiver_alerts (patient_id, alert_type, message)
        VALUES (?, 'SOS', ?)
    """, (
        patient["id"],
        f"EMERGENCY SOS Triggered by {patient['name']}! Address: {patient['address']}. Please check immediately or call 108 Emergency Ambulance."
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "In-app emergency alert recorded for linked caregiver review. Call emergency services directly."})

# ----------------- SENIOR HEALTH VITALS TRACKER (BP & BLOOD SUGAR) ----------------- #
@app.route("/vitals")
def vitals():
    """
    Senior Health Vitals Monitor: Blood Pressure (BP), Pulse, and Blood Glucose.
    Displays color-coded clinical status, 7-day average trends, and emergency alert triggers.
    """
    conn = get_db_connection()
    session_pid = session.get("patient_id")
    target_pid = session_pid if session.get("user_role") == "PATIENT" else (request.args.get("patient_id", type=int) or session_pid)

    patient = None
    if target_pid:
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (target_pid,)).fetchone()
    if not patient:
        conn.close()
        abort(404)
    if not _patient_access_allowed(patient["id"], conn):
        conn.close()
        abort(403)

    p_id = patient["id"]

    # Fetch BP and Sugar logs
    bp_records = conn.execute("""
        SELECT * FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BP' 
        ORDER BY recorded_at DESC LIMIT 20
    """, (p_id,)).fetchall()

    sugar_records = conn.execute("""
        SELECT * FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BLOOD_SUGAR' 
        ORDER BY recorded_at DESC LIMIT 20
    """, (p_id,)).fetchall()

    all_vitals = conn.execute("""
        SELECT * FROM health_vitals 
        WHERE patient_id = ? 
        ORDER BY recorded_at DESC LIMIT 30
    """, (p_id,)).fetchall()

    # Calculate summary metrics
    latest_bp = bp_records[0] if bp_records else None
    latest_sugar = sugar_records[0] if sugar_records else None

    # Calculate 7-day averages
    avg_bp = conn.execute("""
        SELECT AVG(systolic) as avg_sys, AVG(diastolic) as avg_dia, AVG(pulse_bpm) as avg_pulse
        FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BP' AND datetime(recorded_at) >= datetime('now', '-7 days')
    """, (p_id,)).fetchone()

    avg_sugar = conn.execute("""
        SELECT AVG(sugar_value) as avg_sugar
        FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BLOOD_SUGAR' AND datetime(recorded_at) >= datetime('now', '-7 days')
    """, (p_id,)).fetchone()

    conn.close()

    stats = {
        "latest_bp": f"{latest_bp['systolic']}/{latest_bp['diastolic']}" if latest_bp else "No reading",
        "latest_bp_status": latest_bp["status"] if latest_bp else "NORMAL",
        "latest_bp_pulse": latest_bp["pulse_bpm"] if latest_bp else None,
        "latest_bp_time": latest_bp["recorded_at"] if latest_bp else "No reading",
        "latest_sugar": f"{latest_sugar['sugar_value']:.0f}" if latest_sugar else "No reading",
        "latest_sugar_type": latest_sugar["sugar_meal_type"] if latest_sugar else "FASTING",
        "latest_sugar_status": latest_sugar["status"] if latest_sugar else "NORMAL",
        "latest_sugar_time": latest_sugar["recorded_at"] if latest_sugar else "No reading",
        "avg_sys": round(avg_bp["avg_sys"]) if avg_bp and avg_bp["avg_sys"] is not None else None,
        "avg_dia": round(avg_bp["avg_dia"]) if avg_bp and avg_bp["avg_dia"] is not None else None,
        "avg_pulse": round(avg_bp["avg_pulse"]) if avg_bp and avg_bp["avg_pulse"] is not None else None,
        "avg_sugar": round(avg_sugar["avg_sugar"], 1) if avg_sugar and avg_sugar["avg_sugar"] is not None else None
    }

    return render_template(
        "vitals.html",
        patient=patient,
        bp_records=bp_records,
        sugar_records=sugar_records,
        all_vitals=all_vitals,
        stats=stats
    )


@app.route("/vitals/log", methods=["POST"])
def log_vital():
    """
    Records a new Blood Pressure or Blood Sugar measurement with clinical evaluation.
    Triggers caregiver notification if values exceed high alert crisis thresholds.
    """
    conn = get_db_connection()
    if session.get("user_role") == "PATIENT":
        patient_id = session.get("patient_id")
    else:
        patient_id = request.form.get("patient_id", type=int) or session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        abort(404)
    vital_type = request.form.get("vital_type", "BP").upper().strip()
    notes = request.form.get("notes", "").strip()[:500]

    status = "NORMAL"
    alert_msg = None

    if vital_type == "BP":
        try:
            systolic = int(request.form.get("systolic", ""))
            diastolic = int(request.form.get("diastolic", ""))
            pulse_raw = request.form.get("pulse_bpm", "").strip()
            pulse_bpm = int(pulse_raw) if pulse_raw else None
        except (ValueError, TypeError):
            conn.close()
            flash("Invalid Blood Pressure values. Please enter valid numbers.", "error")
            return redirect(url_for("vitals"))
        if not 50 <= systolic <= 300 or not 30 <= diastolic <= 200 or (pulse_bpm is not None and not 25 <= pulse_bpm <= 250):
            conn.close()
            flash("Enter plausible BP and pulse values before saving.", "error")
            return redirect(url_for("vitals"))

        # Clinical BP Evaluation (AHA / Indian Geriatric Guidelines)
        if systolic >= 160 or diastolic >= 100:
            status = "HIGH_ALERT"
            alert_msg = f"CRITICAL BP ALERT: {patient['name'] if patient else 'Senior'} recorded severe high BP: {systolic}/{diastolic} mmHg (Pulse: {pulse_bpm} bpm). Immediate medical review recommended."
        elif systolic >= 140 or diastolic >= 90:
            status = "HIGH_ALERT"
            alert_msg = f"High BP Notice: {patient['name'] if patient else 'Senior'} recorded Stage-1 Hypertension reading: {systolic}/{diastolic} mmHg."
        elif systolic >= 130 or diastolic >= 85:
            status = "ELEVATED"
        elif systolic < 90 or diastolic < 60:
            status = "LOW"
            alert_msg = f"Low BP Alert: {patient['name']} recorded a low BP reading: {systolic}/{diastolic} mmHg. Review the patient's care plan and seek clinical advice as appropriate."
        else:
            status = "NORMAL"

        conn.execute("""
            INSERT INTO health_vitals (patient_id, vital_type, systolic, diastolic, pulse_bpm, status, notes)
            VALUES (?, 'BP', ?, ?, ?, ?, ?)
        """, (patient_id, systolic, diastolic, pulse_bpm, status, notes))

        flash_msg = f"Blood Pressure recorded: {systolic}/{diastolic} mmHg ({status})."

    elif vital_type in ["BLOOD_SUGAR", "SUGAR"]:
        vital_type = "BLOOD_SUGAR"
        try:
            sugar_value = float(request.form.get("sugar_value", ""))
            sugar_meal_type = request.form.get("sugar_meal_type", "FASTING").upper().strip()
        except (ValueError, TypeError):
            conn.close()
            flash("Invalid Blood Sugar value. Please enter a valid number.", "error")
            return redirect(url_for("vitals"))
        if not math.isfinite(sugar_value) or not 10 <= sugar_value <= 1500 or sugar_meal_type not in {"FASTING", "POST_MEAL", "RANDOM"}:
            conn.close()
            flash("Enter a plausible glucose value and valid meal type.", "error")
            return redirect(url_for("vitals"))

        # Clinical Glucose Evaluation
        if sugar_meal_type == "FASTING":
            if sugar_value >= 180:
                status = "HIGH_ALERT"
                alert_msg = f"High fasting glucose alert: {patient['name']} recorded {sugar_value:.0f} mg/dL. Follow the patient's clinician-provided plan and seek clinical advice."
            elif sugar_value >= 126:
                status = "ELEVATED"
            elif sugar_value < 70:
                status = "LOW"
                alert_msg = f"Low fasting glucose alert: {patient['name']} recorded {sugar_value:.0f} mg/dL. Follow the patient's clinician-provided plan and seek urgent help if needed."
            else:
                status = "NORMAL"
        else: # POST_MEAL or RANDOM
            if sugar_value >= 250:
                status = "HIGH_ALERT"
                alert_msg = f"High glucose alert: {patient['name']} recorded {sugar_value:.0f} mg/dL ({sugar_meal_type}). Follow the patient's clinician-provided plan and seek clinical advice."
            elif sugar_value >= 140:
                status = "ELEVATED"
            elif sugar_value < 80:
                status = "LOW"
            else:
                status = "NORMAL"

        conn.execute("""
            INSERT INTO health_vitals (patient_id, vital_type, sugar_value, sugar_meal_type, status, notes)
            VALUES (?, 'BLOOD_SUGAR', ?, ?, ?, ?)
        """, (patient_id, sugar_value, sugar_meal_type, status, notes))

        flash_msg = f"Blood Sugar recorded: {sugar_value:.0f} mg/dL [{sugar_meal_type}] ({status})."
    else:
        conn.close()
        abort(400, description="Unsupported vital type.")

    # If HIGH_ALERT, notify caregiver
    if alert_msg and patient:
        conn.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'CRITICAL_VITAL', ?)
        """, (patient_id, alert_msg))

        if patient["caregiver_authorized"] and patient["consent_recorded"] and patient["sms_notifications_enabled"] and patient["caregiver_phone"]:
            CommunicationService.log_communication(
                patient_id=patient_id,
                recipient_role="CAREGIVER",
                recipient_phone=patient["caregiver_phone"],
                recipient_name=patient["caregiver_name"],
                channel="SMS",
                subject="Elder Health Vitals Alert",
                message_body=alert_msg,
                conn=conn
            )

    conn.commit()
    conn.close()

    flash(flash_msg, "success" if status == "NORMAL" else ("warning" if status == "ELEVATED" else "error"))
    return redirect(url_for("vitals"))


@app.route("/vitals/delete/<int:vital_id>", methods=["POST"])
def delete_vital(vital_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM health_vitals WHERE id = ?", (vital_id,))
    conn.commit()
    conn.close()
    flash("Measurement entry removed from logs.", "info")
    return redirect(url_for("vitals"))


@app.route("/api/vitals/chart_data")
def api_vitals_chart_data():
    conn = get_db_connection()
    if session.get("user_role") == "PATIENT":
        patient_id = session.get("patient_id")
    else:
        patient_id = request.args.get("patient_id", type=int) or session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id, conn):
        conn.close()
        abort(403)

    bp_rows = conn.execute("""
        SELECT systolic, diastolic, pulse_bpm, recorded_at 
        FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BP' 
        ORDER BY recorded_at ASC LIMIT 10
    """, (patient_id,)).fetchall()

    sugar_rows = conn.execute("""
        SELECT sugar_value, sugar_meal_type, recorded_at 
        FROM health_vitals 
        WHERE patient_id = ? AND vital_type = 'BLOOD_SUGAR' 
        ORDER BY recorded_at ASC LIMIT 10
    """, (patient_id,)).fetchall()

    conn.close()

    return jsonify({
        "success": True,
        "bp": [{"systolic": r["systolic"], "diastolic": r["diastolic"], "pulse": r["pulse_bpm"], "time": r["recorded_at"]} for r in bp_rows],
        "sugar": [{"value": r["sugar_value"], "meal_type": r["sugar_meal_type"], "time": r["recorded_at"]} for r in sugar_rows]
    })


# ----------------- BILINGUAL LANGUAGE SWITCHER ENDPOINT ----------------- #
@app.route("/api/set_language/<lang>", methods=["POST"])
def set_language(lang):
    if lang in ["en", "ta"]:
        session["lang"] = lang
        patient_id = session.get("patient_id")
        if patient_id:
            try:
                conn = get_db_connection()
                conn.execute("UPDATE patients SET preferred_language = ? WHERE id = ?", (lang, patient_id))
                conn.commit()
                conn.close()
            except Exception:
                pass
    return jsonify({"success": True, "lang": session.get("lang", "en")})

# ----------------- AUDIO / VOICE SCHEDULE ENDPOINT ----------------- #
@app.route("/api/voice_summary")
def voice_summary():
    conn = get_db_connection()
    patient_id = session.get("patient_id")
    patient = None
    if patient_id:
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        abort(403)

    prescriptions = conn.execute("SELECT * FROM prescriptions WHERE patient_id = ?", (patient["id"],)).fetchall()
    
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    taken_logs = conn.execute("SELECT prescription_id FROM dose_logs WHERE patient_id = ? AND scheduled_for = ? AND status = 'Taken'", (patient["id"], today_str)).fetchall()
    taken_ids = [t["prescription_id"] for t in taken_logs]
    
    conn.close()

    pending = [p for p in prescriptions if p["id"] not in taken_ids]
    low_stocks = [p for p in prescriptions if p["stock_remaining"] <= p["refill_threshold"]]

    lang = session.get("lang", "en")
    patient_name = patient["name"] if patient else ("Senior Patient" if lang == "en" else "முதியவர்")

    if lang == "ta":
        text = f"வணக்கம் {patient_name}! "
        if pending:
            med_details = []
            for p in pending:
                timing_ta = "காலை" if p['timing'] == "Morning" else ("மதியம்" if p['timing'] == "Afternoon" else ("மாலை" if p['timing'] == "Evening" else "இரவு"))
                med_details.append(f"{p['medicine_name']} {p['dosage']} ({timing_ta} - {p['timing_advice']})")
            text += "இன்றைய மாத்திரை அட்டவணை: " + ", ".join(med_details) + ". மருந்துகளை உரிய நேரத்தில் தவறாமல் எடுத்துக்கொள்ளவும். "
        else:
            text += "அற்புதம்! நீங்கள் இன்றைய அனைத்து மருந்துகளையும் எடுத்துக்கொண்டீர்கள். நல்வாழ்வு பெற வாழ்த்துகள்! "
        if low_stocks:
            low_names = ", ".join([p["medicine_name"] for p in low_stocks])
            text += f"முக்கிய அறிவிப்பு: {low_names} வீட்டில் உள்ள மருந்து இருப்பு குறைவாக இருக்கலாம். இது ஒரு மதிப்பீடு மட்டுமே; ரீபில் ஆர்டர் தானாக அனுப்பப்படாது."
        return jsonify({"text": text, "lang": "ta-IN"})

    text = f"Good day {patient_name}! "
    if pending:
        med_details = []
        for p in pending:
            med_details.append(f"{p['medicine_name']} {p['dosage']} in the {p['timing']} ({p['timing_advice']})")
        text += "Here is your recorded medicine schedule for today: " + ", ".join(med_details) + ". Follow the instructions provided by your clinician. "
    else:
        text += "No unrecorded scheduled doses were found for today. This reflects app records only. "
    
    if low_stocks:
        low_names = ", ".join([p["medicine_name"] for p in low_stocks])
        text += f"Important notice: the recorded home stock for {low_names} is at or below the refill threshold. This is an estimate; no order was placed automatically."

    return jsonify({"text": text, "lang": "en-US"})

# ----------------- PHARMACY DASHBOARD & INVENTORY ----------------- #
@app.route("/pharmacy")
def pharmacy():
    conn = get_db_connection()
    store = conn.execute("SELECT * FROM pharmacy_stores LIMIT 1").fetchone()
    pharmacy_info = dict(store) if store else {
        "name": "Pharmacy not configured",
        "license_number": None,
        "pharmacist_name": None,
        "phone": None,
        "address": None,
    }

    orders = conn.execute("""
        SELECT r.*, p.name as patient_name, p.age as patient_age, p.phone as patient_phone, p.caregiver_phone
        FROM refill_orders r
        LEFT JOIN patients p ON r.patient_id = p.id
        ORDER BY r.id DESC
    """).fetchall()

    inventory_list = conn.execute("""
        SELECT id, name, generic_name, category, price, pharmacy_stock, requires_prescription
        FROM medicines
        ORDER BY name ASC
    """).fetchall()

    conn.close()
    return render_template("pharmacy_dashboard.html", pharmacy=pharmacy_info, orders=orders, inventory_list=inventory_list)

# ----------------- PHARMACY ASSISTED PHONE CALL ORDER ----------------- #
@app.route("/pharmacy/assisted_order", methods=["GET", "POST"])
def pharmacy_assisted_order():
    conn = get_db_connection()

    if request.method == "POST":
        patient_mode = request.form.get("patient_mode", "existing")
        delivery_address = request.form.get("delivery_address", "").strip()
        patient_id = int(request.form.get("patient_id", 0) or 0)

        # Handle New Patient Caller Quick Registration
        if patient_mode == "new":
            new_name = request.form.get("new_name", "").strip()
            new_phone = "".join(ch for ch in request.form.get("new_phone", "") if ch.isdigit())[-10:]
            try:
                new_age = int(request.form.get("new_age", ""))
            except (TypeError, ValueError):
                new_age = 0
            new_address = request.form.get("new_address", "").strip() or delivery_address
            new_caregiver_name = request.form.get("new_caregiver_name", "").strip()
            new_caregiver_phone = "".join(ch for ch in request.form.get("new_caregiver_phone", "") if ch.isdigit())[-10:]

            if not new_name or len(new_name) > 120 or len(new_phone) != 10 or not 40 <= new_age <= 120 or not new_address or len(new_address) > 500:
                conn.close()
                flash("New caller registration requires Patient Name, Phone Number, and Delivery Address.", "error")
                return redirect(url_for("pharmacy_assisted_order"))

            # Check if phone number already exists
            existing_p = conn.execute("SELECT id FROM patients WHERE phone = ?", (new_phone,)).fetchone()
            if existing_p:
                patient_id = existing_p["id"]
            else:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO patients (
                        name, age, phone, address, caregiver_name, caregiver_phone,
                        consent_recorded, caregiver_authorized
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 0)
                """, (new_name, new_age, new_phone, new_address, new_caregiver_name, new_caregiver_phone))
                patient_id = cur.lastrowid
                delivery_address = new_address

        medicine_id = int(request.form.get("medicine_id", 0) or 0)
        quantity = int(request.form.get("quantity", 0) or 0)
        rx_verified = 0  # Set from catalog requirement only after staff reviews evidence below.
        payment_method = request.form.get("payment_method", "Cash on Delivery (Doorstep)")
        call_notes = request.form.get("call_notes", "").strip()[:500]
        send_sms = request.form.get("send_sms_alert") == "1"

        if not 1 <= quantity <= 300:
            conn.close()
            flash("Invalid quantity! Enter between 1 and 300 tablets.", "error")
            return redirect(url_for("pharmacy_assisted_order"))

        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        med = conn.execute("SELECT * FROM medicines WHERE id = ?", (medicine_id,)).fetchone()

        if not patient or not med:
            conn.close()
            flash("Invalid patient or medicine selected for assisted order.", "error")
            return redirect(url_for("pharmacy_assisted_order"))

        if not delivery_address:
            delivery_address = patient["address"] or ""
        if not delivery_address or len(delivery_address) > 500:
            conn.close()
            flash("A valid delivery address is required.", "error")
            return redirect(url_for("pharmacy_assisted_order"))
        if med["price"] is None or float(med["price"]) <= 0:
            conn.close()
            flash("This medicine has no verified catalog price; no order was placed.", "error")
            return redirect(url_for("pharmacy_assisted_order"))

        # Check warehouse stock availability
        is_avail, cur_stock, unit_p, req_rx, med_name = InventoryManager.check_availability(medicine_id, quantity)
        if not is_avail:
            conn.close()
            flash(f"Cannot fulfill order. Configured pharmacy stock has {cur_stock} tablets of '{med_name}' (requested {quantity}).", "error")
            return redirect(url_for("pharmacy_assisted_order"))

        strips = quantity / 30.0
        total_amount = round(unit_p * strips)
        order_number = f"AST-MED-{random.randint(100000, 999999)}"
        items_summary = f"{med['name']} ({med['category']} x {quantity} tabs)"

        # Staff reference tracking
        staff_name = session.get("user_name", "Pharmacy staff")
        staff_ref = f"{staff_name} ({session.get('user_role', 'PHARMACY')})"

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO refill_orders (
                patient_id, order_number, items_summary, total_pills, total_amount,
                delivery_address, contact_phone, payment_method, status, pharmacy_name, delivery_rider,
                order_source, prescription_verified, created_by_staff
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', NULL, NULL, 'PHARMACY_ASSISTED_CALL', ?, ?)
        """, (patient_id, order_number, items_summary, quantity, total_amount, delivery_address, patient["phone"], payment_method, 0 if req_rx else 1, staff_ref))
        order_id = cursor.lastrowid

        # Reserve pharmacy inventory atomically
        items_to_reserve = [{
            "medicine_id": medicine_id,
            "medicine_name": med["name"],
            "dosage": "Standard",
            "quantity": quantity,
            "unit_price": unit_p,
            "requires_prescription": req_rx
        }]
        success, reserve_msg = InventoryManager.reserve_stock_for_order(order_id, items_to_reserve, conn=conn)

        if not success:
            conn.rollback()
            conn.close()
            flash(reserve_msg, "error")
            return redirect(url_for("pharmacy_assisted_order"))

        # Record caregiver alert
        cursor.execute("""
            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
            VALUES (?, 'ASSISTED_ORDER_PLACED', ?)
        """, (
            patient_id,
            f"Phone-assisted order #{order_number} submitted by {staff_ref}; awaiting pharmacy review. No delivery time is confirmed. Notes: {call_notes or 'None'}"
        ))

        # Communication service audit
        if send_sms and patient["consent_recorded"] and patient["sms_notifications_enabled"]:
            CommunicationService.send_order_placed_notification(
                order_id=order_id,
                patient_id=patient_id,
                phone=patient["phone"],
                name=patient["name"],
                order_number=order_number,
                total_amount=total_amount,
                items_summary=items_summary,
                conn=conn
            )

        conn.commit()
        conn.close()
        flash(f"Assisted order #{order_number} submitted for pharmacy review. {quantity} tablets reserved; no delivery time is confirmed.", "success")
        return redirect(url_for("pharmacy"))

    # GET: Form view
    patients = conn.execute("SELECT id, name, phone, age, address, caregiver_name, caregiver_phone FROM patients ORDER BY name ASC").fetchall()
    catalog_medicines = conn.execute("SELECT id, name, category, price, pharmacy_stock, requires_prescription FROM medicines ORDER BY name ASC").fetchall()
    selected_patient_id = request.args.get("patient_id", type=int)
    conn.close()

    return render_template(
        "pharmacy_assisted_order.html",
        patients=patients,
        catalog_medicines=catalog_medicines,
        selected_patient_id=selected_patient_id
    )

@app.route("/pharmacy/verify_rx/<int:order_id>", methods=["POST"])
def pharmacy_verify_rx(order_id):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM refill_orders WHERE id = ?", (order_id,)).fetchone()
    if not order or order["is_cancelled"] or order["status"] in {"Delivered", "Cancelled", "Failed Delivery"}:
        conn.close()
        abort(404)
    has_rx = conn.execute("SELECT 1 FROM order_items WHERE order_id = ? AND requires_prescription = 1 LIMIT 1", (order_id,)).fetchone()
    if not has_rx:
        conn.close()
        abort(400, description="This order does not contain an item requiring prescription review.")
    conn.execute("UPDATE refill_orders SET prescription_verified = 1 WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash(f"Prescription review recorded for order #{order_id}. Verify pharmacy authorization and source evidence through your organization's procedures.", "success")
    return redirect(url_for("pharmacy"))

@app.route("/pharmacy/cancel_order/<int:order_id>", methods=["POST"])
def pharmacy_cancel_order(order_id):
    success, msg = InventoryManager.restore_stock_on_cancellation(order_id, cancellation_reason="Cancelled by Dispensing Pharmacist")
    if success:
        flash(msg, "warning")
    else:
        flash(msg, "error")
    return redirect(url_for("pharmacy"))

# ----------------- PHYSICAL PRINTABLE SCHEDULE CARD ----------------- #
@app.route("/patient/print_card")
def print_schedule_card():
    conn = get_db_connection()
    user_role = session.get("user_role")
    user_phone = session.get("user_phone", "")
    session_pid = session.get("patient_id")

    if not user_role and not session_pid:
        conn.close()
        flash("Authentication required. Please sign in to print patient timetables.", "warning")
        return redirect(url_for("signin"))

    requested_pid = request.args.get("patient_id", type=int) or session_pid

    # Authorization rules:
    # 1. Patient role can ONLY print their own timetable
    if user_role == "PATIENT" and requested_pid != session_pid:
        conn.close()
        flash("Unauthorized: You can only print your own medication timetable.", "error")
        return redirect(url_for("index"))

    # 2. Caregiver role can ONLY print linked patients' timetables
    if user_role == "CAREGIVER":
        clean_phone = user_phone.strip() if user_phone else ""
        if len(clean_phone) >= 5:
            linked = conn.execute("""
                SELECT id FROM patients 
                WHERE id = ? AND (id = ? OR caregiver_phone = ? OR caregiver_phone LIKE ?)
            """, (requested_pid, session_pid or 0, clean_phone, f"%{clean_phone[-10:]}%")).fetchone()
        else:
            linked = conn.execute("""
                SELECT id FROM patients 
                WHERE id = ? AND id = ?
            """, (requested_pid, session_pid or 0)).fetchone()

        if not linked and user_role != "ADMIN":
            conn.close()
            flash("Unauthorized: Caregivers cannot access unlinked patient timetables.", "error")
            return redirect(url_for("caregiver"))

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (requested_pid,)).fetchone()
    if not patient:
        conn.close()
        flash("Patient not found to print schedule timetable.", "warning")
        return redirect(url_for("index"))

    # Authorized list of patients for switcher dropdown (prevents snooping on unlinked patients)
    if user_role in ["PHARMACY", "ADMIN"]:
        authorized_patients = conn.execute("SELECT id, name, phone, age FROM patients ORDER BY name ASC").fetchall()
    elif user_role == "CAREGIVER":
        clean_phone = user_phone.strip() if user_phone else ""
        if len(clean_phone) >= 5:
            authorized_patients = conn.execute("""
                SELECT id, name, phone, age FROM patients 
                WHERE id = ? OR caregiver_phone = ? OR caregiver_phone LIKE ?
                ORDER BY name ASC
            """, (session_pid or 0, clean_phone, f"%{clean_phone[-10:]}%")).fetchall()
        else:
            authorized_patients = conn.execute("""
                SELECT id, name, phone, age FROM patients 
                WHERE id = ?
                ORDER BY name ASC
            """, (session_pid or 0,)).fetchall()
    else:
        authorized_patients = [patient]

    prescriptions = conn.execute("""
        SELECT * FROM prescriptions 
        WHERE patient_id = ? 
        ORDER BY timing ASC, stock_remaining ASC
    """, (patient["id"],)).fetchall()
    conn.close()

    slots = {"Morning": [], "Afternoon": [], "Evening": [], "Night": []}
    for p in prescriptions:
        t = p["timing"].lower()
        if "morning" in t:
            slots["Morning"].append(p)
        elif "afternoon" in t or "lunch" in t:
            slots["Afternoon"].append(p)
        elif "evening" in t:
            slots["Evening"].append(p)
        elif "night" in t or "bed" in t:
            slots["Night"].append(p)
        else:
            slots["Morning"].append(p)

    return render_template("printable_schedule_card.html", patient=patient, patients=authorized_patients, slots=slots)

# ----------------- EXTERNAL VIVA VOCE AI ASSISTANT ----------------- #
@app.route("/viva")
def viva_assistant():
    return render_template("viva_assistant.html")

@app.route("/api/viva_ask", methods=["POST"])
def api_viva_ask():
    data = request.get_json(silent=True) or {}
    question = data.get("question", "")
    if not isinstance(question, str) or not question.strip() or len(question) > 300:
        return jsonify({"error": "Question must be between 1 and 300 characters."}), 400
    question = question.strip()

    answer = VivaAssistant.answer_question(question)
    return jsonify(answer)

# ----------------- FREE REAL-TIME MEDICATION & STOCK ALERTS API ----------------- #
@app.route("/api/notifications/active_alerts")
def api_active_alerts():
    """
    Zero-cost, local real-time alerts endpoint:
    Returns due medication schedules, low stock warnings, and Tamil/English content
    without requiring any paid SMS, Twilio, or Fast2SMS subscriptions.
    """
    patient_id = session.get("patient_id")
    if not patient_id or not _patient_access_allowed(patient_id):
        abort(403)

    conn = get_db_connection()
    now_ist = get_current_ist_datetime()
    today_str = now_ist.date().isoformat()
    current_hhmm = now_ist.strftime("%H:%M")

    # 1. Fetch patient
    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        abort(404)
    patient_name = patient["name"] if patient else "Senior Citizen"
    preferred_lang = session.get("lang") or (patient["preferred_language"] if patient else "ta")

    # 2. Fetch prescriptions
    prescriptions = conn.execute("""
        SELECT * FROM prescriptions
        WHERE patient_id = ?
        ORDER BY schedule_time ASC
    """, (patient_id,)).fetchall()

    # 3. Fetch today's taken dose logs
    taken_logs = conn.execute("""
        SELECT prescription_id FROM dose_logs
        WHERE patient_id = ? AND scheduled_for = ? AND status = 'Taken'
    """, (patient_id, today_str)).fetchall()
    taken_ids = {row["prescription_id"] for row in taken_logs}

    due_reminders = []
    low_stock_alerts = []

    for p in prescriptions:
        rx_id = p["id"]
        sched_time = (p["schedule_time"] or "08:00").strip()[:5]
        stock = p["stock_remaining"] if p["stock_remaining"] is not None else 30
        threshold = p["refill_threshold"] if p["refill_threshold"] is not None else 5
        med_name = p["medicine_name"]
        dosage = p["dosage"] or "Standard"
        advice = p["timing_advice"] or "With Water"

        # Low-stock check
        if stock <= threshold:
            low_stock_alerts.append({
                "id": rx_id,
                "medicine_name": med_name,
                "stock_remaining": stock,
                "threshold": threshold,
                "msg_en": f"Low Stock Alert: {med_name} has only {stock} tablets left. Please refill soon.",
                "msg_ta": f"குறைந்த இருப்பு எச்சரிக்கை: {med_name} மாத்திரைகள் {stock} மட்டுமே உள்ளன. உடனே ரீபில் செய்யவும்."
            })

        # Due reminder check: if not taken today and current time is at or near scheduled time
        if rx_id not in taken_ids:
            try:
                sh, sm = map(int, sched_time.split(":"))
                now_mins = now_ist.hour * 60 + now_ist.minute
                sched_mins = sh * 60 + sm
                diff = now_mins - sched_mins
                # Due if within 0 to 120 minutes of schedule time (or if requested via force param)
                force_test = request.args.get("force") == "1"
                if force_test or (0 <= diff <= 120):
                    due_reminders.append({
                        "id": rx_id,
                        "medicine_name": med_name,
                        "dosage": dosage,
                        "timing": p["timing"] or "Daily",
                        "timing_advice": advice,
                        "schedule_time": sched_time,
                        "color_code": p["color_code"] or "#F28B82",
                        "stock_remaining": stock,
                        "diff_mins": diff,
                        "msg_en": f"Hello {patient_name}! It is time to take your {med_name} ({dosage}, {advice}). Please drink with a full glass of water.",
                        "msg_ta": f"வணக்கம் {patient_name}! உங்கள் {med_name} ({dosage}, {advice}) மாத்திரையை உட்கொள்ள வேண்டிய நேரம் வந்துவிட்டது. ஒரு டம்ளர் தண்ணீருடன் உட்கொள்ளவும்."
                    })
            except (TypeError, ValueError):
                app.logger.warning("Skipping reminder for prescription %s with invalid schedule time.", rx_id)

    conn.close()
    return jsonify({
        "success": True,
        "is_authenticated": True,
        "patient_name": patient_name,
        "preferred_lang": preferred_lang,
        "current_time": current_hhmm,
        "reminders": due_reminders,
        "low_stock": low_stock_alerts
    })

# ----------------- PROTOTYPE ROLE SWITCHER & CUSTOM 403 HANDLER ----------------- #
@app.route("/switch_mode/<target_role>")
def switch_mode(target_role):
    """Allows demo tester to toggle between Patient, Pharmacy, and Admin modes seamlessly."""
    if not session.get("user_role"):
        return redirect(url_for("signin"))
    role = target_role.upper()
    if role in {"PATIENT", "ADMIN", "PHARMACY", "CAREGIVER"}:
        session["user_role"] = role
        if not session.get("patient_id"):
            conn = get_db_connection()
            p = conn.execute("SELECT id FROM patients ORDER BY id ASC LIMIT 1").fetchone()
            if p:
                session["patient_id"] = p["id"]
            conn.close()
        flash(f"Role switched to {role} mode successfully.", "success")
        if role == "PHARMACY":
            return redirect(url_for("pharmacy"))
        elif role == "ADMIN":
            return redirect(url_for("admin_reminders"))
        return redirect(url_for("index"))
    return redirect(url_for("index"))

@app.errorhandler(403)
def forbidden_error(e):
    user_role = session.get("user_role", "PATIENT")
    user_name = session.get("user_name", "User")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>403 Forbidden - Staff Access Required</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
</head>
<body class="bg-[#FFF4F3] min-h-screen flex items-center justify-center p-4">
  <div class="bg-white rounded-3xl p-8 max-w-lg w-full shadow-2xl border border-[#FCDAD7] text-center space-y-5">
    <div class="w-16 h-16 rounded-3xl bg-amber-100 text-amber-600 flex items-center justify-center mx-auto text-3xl shadow-sm">
      <i class="fa-solid fa-lock"></i>
    </div>
    <div>
      <span class="inline-block px-3 py-1 rounded-full text-xs font-black bg-amber-100 text-amber-700 uppercase tracking-wider mb-2">Staff Access Control (403)</span>
      <h1 class="text-2xl font-black text-[#30343B]">Permission Required</h1>
      <p class="text-sm text-[#777D87] mt-1">
        You are signed in as <strong class="text-[#30343B]">{user_name}</strong> with role <span class="px-2 py-0.5 rounded-lg text-xs font-bold bg-rose-100 text-rose-700">{user_role}</span>.
      </p>
      <p class="text-xs text-[#777D87] mt-2">
        The Pharmacy Portal and Reminders & Calls require Staff/Admin privileges.
      </p>
    </div>

    <div class="p-4 rounded-2xl bg-[#FFF4F3] border border-[#FCDAD7] space-y-2.5 text-left">
      <div class="text-xs font-black text-[#30343B] flex items-center gap-1.5">
        <i class="fa-solid fa-wand-magic-sparkles text-[#F28B82]"></i> Click to Switch Role & Open:
      </div>
      <div class="grid grid-cols-2 gap-2">
        <a href="/switch_mode/PHARMACY" class="py-2.5 px-3 rounded-xl bg-[#65B9A9] hover:bg-[#529E90] text-white text-xs font-black flex items-center justify-center gap-1.5 transition shadow-sm">
          <i class="fa-solid fa-store"></i> Open as Pharmacy
        </a>
        <a href="/switch_mode/ADMIN" class="py-2.5 px-3 rounded-xl bg-[#F28B82] hover:bg-[#E07067] text-white text-xs font-black flex items-center justify-center gap-1.5 transition shadow-sm">
          <i class="fa-solid fa-shield-halved"></i> Open as Admin
        </a>
      </div>
    </div>

    <div class="flex items-center justify-center gap-3 pt-2 text-xs">
      <a href="/" class="text-[#777D87] hover:text-[#30343B] font-bold">&larr; Return to Patient Dashboard</a>
      <span class="text-[#EAECEF]">|</span>
      <form method="post" action="/logout" style="display:inline;">
        <input type="hidden" name="csrf_token" value="{session.get('csrf_token', '')}">
        <button type="submit" class="text-rose-600 hover:underline font-bold bg-transparent border-0 cursor-pointer p-0 text-xs">Sign Out</button>
      </form>
    </div>
  </div>
</body>
</html>"""
    return html, 403

if __name__ == "__main__":
    if os.getenv("RUN_REMINDER_WORKER", "true").lower() in {"1", "true", "yes"}:
        ReminderScheduler.start_background_scheduler(interval_seconds=30)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
