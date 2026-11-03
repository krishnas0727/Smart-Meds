"""Security and safety regression tests using an isolated disposable SQLite database."""
import os
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.TemporaryDirectory(prefix="smart-meds-tests-")
os.environ["SECRET_KEY"] = "test-only-random-secret-not-for-production"
os.environ["DATABASE_PATH"] = str(Path(_TEST_DIR.name) / "test.db")
os.environ["FLASK_DEBUG"] = "false"
os.environ["SMART_MEDS_DEMO_SEED"] = "false"
os.environ["RUN_REMINDER_WORKER"] = "false"

import app as app_module  # noqa: E402
from database import get_db_connection, init_db  # noqa: E402
from werkzeug.security import check_password_hash  # noqa: E402
from services.scheduler_service import ReminderScheduler  # noqa: E402
from services.inventory_manager import InventoryManager  # noqa: E402
from services.communication_service import CommunicationService  # noqa: E402


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        db_path = Path(os.environ["DATABASE_PATH"])
        for candidate in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            candidate.unlink(missing_ok=True)
        init_db()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def csrf(self, client=None):
        client = client or self.client
        client.get("/signin")
        with client.session_transaction() as sess:
            return sess["csrf_token"]

    def register_patient(self, phone="5550001001", name="Test Patient", caregiver_phone=""):
        token = self.csrf()
        response = self.client.post("/register", data={
            "csrf_token": token,
            "name": name,
            "age": "72",
            "gender": "Female",
            "phone": phone,
            "pin": "not-a-real-password-123",
            "address": "Test address, not a real residence",
            "caregiver_name": "Test Caregiver" if caregiver_phone else "",
            "caregiver_phone": caregiver_phone,
            "doctor_name": "",
            "doctor_phone": "",
        })
        self.assertEqual(response.status_code, 302)
        return token

    def test_empty_database_has_no_fabricated_patient_or_catalog(self):
        conn = get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM health_vitals").fetchone()[0], 0)
        conn.close()

    def test_mutating_registration_requires_csrf(self):
        response = self.client.post("/register", data={"name": "No Token"})
        self.assertEqual(response.status_code, 400)
        conn = get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 0)
        conn.close()

    def test_registration_stores_only_hash_and_defaults_consent_off(self):
        self.register_patient()
        conn = get_db_connection()
        patient = conn.execute("SELECT * FROM patients WHERE phone = ?", ("5550001001",)).fetchone()
        self.assertIsNone(patient["pin"])
        self.assertTrue(check_password_hash(patient["password_hash"], "not-a-real-password-123"))
        self.assertEqual(patient["consent_recorded"], 0)
        self.assertEqual(patient["caregiver_authorized"], 0)
        self.assertEqual(patient["reminder_consent"], 0)
        self.assertEqual(patient["sms_notifications_enabled"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM prescriptions").fetchone()[0], 0)
        conn.close()

    def test_patient_session_cannot_read_or_mutate_another_patient_prescription(self):
        self.register_patient()
        with self.client.session_transaction() as sess:
            first_id = sess["patient_id"]
        conn = get_db_connection()
        conn.execute("INSERT INTO patients (name, age, phone, address) VALUES (?, ?, ?, ?)", ("Other Test Patient", 70, "5550001002", "Other test address"))
        other_id = conn.execute("SELECT id FROM patients WHERE phone = ?", ("5550001002",)).fetchone()[0]
        conn.execute("INSERT INTO prescriptions (patient_id, medicine_name, dosage, timing, stock_remaining) VALUES (?, ?, ?, ?, ?)", (other_id, "Test medicine", "1 tablet", "Morning", 10))
        rx_id = conn.execute("SELECT id FROM prescriptions WHERE patient_id = ?", (other_id,)).fetchone()[0]
        conn.commit()
        conn.close()
        token = self.csrf()
        response = self.client.post(f"/api/take_dose/{rx_id}", json={"status": "Taken"}, headers={"X-CSRFToken": token})
        self.assertEqual(response.status_code, 403)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["patient_id"], first_id)

    def test_caregiver_requires_and_consumes_one_time_invitation(self):
        self.register_patient(phone="5550001003", caregiver_phone="5550002003")
        token = self.csrf()
        response = self.client.post("/caregiver/invite", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        invitation = next(message.split(": ", 1)[-1] for category, message in flashes if "invitation code" in message.lower())

        caregiver = app_module.app.test_client()
        caregiver_token = self.csrf(caregiver)
        response = caregiver.post("/caregiver/register", data={
            "csrf_token": caregiver_token,
            "name": "Authorized Test Caregiver",
            "phone": "5550002003",
            "invite_code": invitation,
            "password": "caregiver-test-password-456",
        })
        self.assertEqual(response.status_code, 302)
        conn = get_db_connection()
        patient = conn.execute("SELECT id, caregiver_authorized, caregiver_invite_hash FROM patients WHERE phone = ?", ("5550001003",)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE phone = ?", ("5550002003",)).fetchone()
        self.assertEqual(patient["caregiver_authorized"], 1)
        self.assertIsNone(patient["caregiver_invite_hash"])
        self.assertEqual(user["role"], "CAREGIVER")
        self.assertEqual(user["patient_id"], patient["id"])
        self.assertTrue(check_password_hash(user["password_hash"], "caregiver-test-password-456"))
        conn.close()

        login_token = self.csrf(caregiver)
        caregiver.post("/signin", data={"csrf_token": login_token, "phone": "5550002003", "pin": "caregiver-test-password-456"})
        self.assertEqual(caregiver.get("/caregiver").status_code, 200)
        revoke_token = self.csrf()
        self.assertEqual(self.client.post("/caregiver/revoke", data={"csrf_token": revoke_token}).status_code, 302)
        revoked_view = caregiver.get("/caregiver")
        self.assertEqual(revoked_view.status_code, 200)
        self.assertIn(b"No patients are currently linked", revoked_view.data)

    def test_replayed_dtmf_callback_does_not_repeat_stock_or_dose_log(self):
        self.register_patient(phone="5550001004")
        with self.client.session_transaction() as sess:
            patient_id = sess["patient_id"]
        conn = get_db_connection()
        conn.execute("INSERT INTO prescriptions (patient_id, medicine_name, dosage, timing, stock_remaining) VALUES (?, ?, ?, ?, ?)", (patient_id, "Test medicine", "1 tablet", "Morning", 2))
        rx_id = conn.execute("SELECT id FROM prescriptions WHERE patient_id = ?", (patient_id,)).fetchone()[0]
        conn.execute("INSERT INTO reminder_dispatches (prescription_id, patient_id, scheduled_date, scheduled_time, channel, status) VALUES (?, ?, date('now'), '08:00', 'VOICE_CALL', 'PENDING')", (rx_id, patient_id))
        dispatch_id = conn.execute("SELECT id FROM reminder_dispatches WHERE prescription_id = ?", (rx_id,)).fetchone()[0]
        conn.commit()
        conn.close()

        _, first_valid = ReminderScheduler.handle_dtmf_response(dispatch_id, "1")
        _, second_valid = ReminderScheduler.handle_dtmf_response(dispatch_id, "1")
        self.assertTrue(first_valid)
        self.assertFalse(second_valid)
        conn = get_db_connection()
        self.assertEqual(conn.execute("SELECT stock_remaining FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM dose_logs WHERE prescription_id = ? AND status = 'Taken'", (rx_id,)).fetchone()[0], 1)
        conn.close()

    def test_health_endpoint_and_secure_cookie_defaults(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertTrue(app_module.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertTrue(app_module.app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app_module.app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_unknown_authenticated_role_fails_closed(self):
        with self.client.session_transaction() as sess:
            sess["user_role"] = "UNRECOGNIZED"
        self.assertEqual(self.client.get("/pharmacy").status_code, 403)

    def test_patient_cannot_access_staff_portals_or_reminder_controls(self):
        self.register_patient(phone="5550001007")
        token = self.csrf()
        self.assertEqual(self.client.get("/pharmacy").status_code, 403)
        self.assertEqual(self.client.get("/admin/reminders").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/admin/reminders/trigger_check",
                data={"csrf_token": token},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/admin/reminders/test_call",
                data={"csrf_token": token, "phone": "5550001007"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/admin/reminders/test_sms",
                data={"csrf_token": token, "phone": "5550001007"},
            ).status_code,
            403,
        )

    def test_active_alerts_endpoint_requires_patient_access(self):
        self.assertEqual(self.client.get("/api/notifications/active_alerts").status_code, 302)

        self.register_patient(phone="5550001008")
        response = self.client.get("/api/notifications/active_alerts")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["is_authenticated"])

    def test_pwa_service_worker_is_public_and_root_scoped(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Service-Worker-Allowed"), "/")
        self.assertIn(b"all app pages and API calls are network-only", response.data)
        response.close()

    def test_order_tracking_discloses_no_default_coordinates_or_eta(self):
        self.register_patient(phone="5550001006")
        with self.client.session_transaction() as sess:
            patient_id = sess["patient_id"]
        conn = get_db_connection()
        cur = conn.execute(
            "INSERT INTO refill_orders (patient_id, order_number, items_summary, total_pills, total_amount, delivery_address, contact_phone, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (patient_id, "TEST-TRACK-1", "Synthetic test item", 1, 10.0, "Synthetic test address", "5550001006", "Shipped"),
        )
        order_id = cur.lastrowid
        conn.commit()
        conn.close()
        page = self.client.get("/orders/track/TEST-TRACK-1")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Live courier GPS is not configured", page.data)
        self.assertNotIn(b"13.0850", page.data)
        page.close()
        api = self.client.get(f"/api/orders/{order_id}/rider_location")
        self.assertEqual(api.status_code, 200)
        self.assertIn(b'"gps_available":false', api.data)
        self.assertIn(b'"eta_minutes":null', api.data)
        api.close()

    def test_whatsapp_order_message_omits_patient_and_medicine_details(self):
        message = CommunicationService.format_whatsapp_tracking_message(
            order_number="TEST-ORDER-1",
            patient_name="Private Test Patient",
            status="Pending",
            total_amount=1234,
            delivery_address="Private Test Address",
            items_summary="Private Test Medicine",
            tracking_url="https://example.invalid/orders/TEST-ORDER-1",
        )
        self.assertIn("TEST-ORDER-1", message)
        for sensitive in ("Private Test Patient", "Private Test Address", "Private Test Medicine", "1234"):
            self.assertNotIn(sensitive, message)

    def test_failed_delivery_restores_pharmacy_stock_only_once(self):
        self.register_patient(phone="5550001005")
        with self.client.session_transaction() as sess:
            patient_id = sess["patient_id"]
        conn = get_db_connection()
        cur = conn.execute(
            "INSERT INTO medicines (name, letter, price, pharmacy_stock) VALUES (?, ?, ?, ?)",
            ("Test catalog medicine", "T", 12.0, 3),
        )
        medicine_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO refill_orders (patient_id, order_number, items_summary, total_pills, total_amount, delivery_address, contact_phone, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'Shipped')",
            (patient_id, "TEST-FAILED-1", "Test item", 2, 24.0, "Synthetic address", "5550001005"),
        )
        order_id = cur.lastrowid
        conn.execute(
            "INSERT INTO order_items (order_id, medicine_id, medicine_name, quantity, unit_price, subtotal) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, medicine_id, "Test catalog medicine", 2, 12.0, 24.0),
        )
        conn.commit()
        conn.close()

        ok, _ = InventoryManager.restore_stock_on_failed_delivery(order_id, "Synthetic failed attempt")
        self.assertTrue(ok)
        ok, _ = InventoryManager.restore_stock_on_failed_delivery(order_id, "Duplicate attempt")
        self.assertFalse(ok)
        conn = get_db_connection()
        self.assertEqual(conn.execute("SELECT pharmacy_stock FROM medicines WHERE id = ?", (medicine_id,)).fetchone()[0], 5)
        self.assertEqual(conn.execute("SELECT status FROM refill_orders WHERE id = ?", (order_id,)).fetchone()[0], "Failed Delivery")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_status_history WHERE order_id = ? AND status = 'Failed Delivery'", (order_id,)).fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
