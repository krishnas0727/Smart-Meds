"""
Automatic Time-Based Reminder & Telephony Scheduler Service for ElderCare MedTrack.

Reliability & Compliance Architecture:
1. Persistent Background Worker: Runs continuously without requiring senior citizens
   to open any app or browser.
2. Timezone Integrity: Computes all schedules in 'Asia/Kolkata' (IST) by default (+05:30).
3. Anti-Duplicate Concurrency Locks: Uses a database-level UNIQUE constraint on
   (prescription_id, scheduled_date, scheduled_time, channel) and atomic transactional claiming
   to guarantee that reminders cannot be duplicated, even across application restarts.
4. Voice Call DTMF Keypad Triage:
   - Key 1: Dose Taken -> Decrements pill count & marks dose as Taken.
   - Key 2: Not Taken -> Logs Skipped & alerts caregiver.
   - Key 3: Snooze -> Reschedules automated retry in 15 minutes.
5. TRAI Quiet Hours Compliance: Prevents nocturnal voice calls between 22:00 and 06:00
   unless explicitly authorized by senior regimen.
6. Limited Retry Policy: Retries up to 2 times on busy/no-answer before escalating to family caregiver.
"""

import os
import time
import threading
import datetime
from database import get_db_connection
from services.communication_service import CommunicationService, normalize_phone_number

# Indian Standard Time (IST) Offset: UTC +05:30
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def get_current_ist_datetime():
    """Returns current localized datetime in Asia/Kolkata (IST)."""
    return datetime.datetime.now(IST)


class ReminderScheduler:
    _scheduler_thread = None
    _scheduler_running = False
    _lock = threading.Lock()

    @staticmethod
    def is_quiet_hours(patient_record, ist_time: datetime.datetime = None) -> bool:
        """
        Checks whether current IST time falls within quiet hours (default 22:00 to 06:00).
        Healthcare Best Practice & TRAI Telephony Guideline.
        """
        now = ist_time or get_current_ist_datetime()
        cur_hhmm = now.strftime("%H:%M")

        q_start = patient_record["quiet_hours_start"] if "quiet_hours_start" in patient_record.keys() and patient_record["quiet_hours_start"] else "22:00"
        q_end = patient_record["quiet_hours_end"] if "quiet_hours_end" in patient_record.keys() and patient_record["quiet_hours_end"] else "06:00"

        if q_start > q_end:
            # Crosses midnight: e.g. 22:00 to 06:00
            return cur_hhmm >= q_start or cur_hhmm < q_end
        else:
            return q_start <= cur_hhmm < q_end

    @classmethod
    def check_and_dispatch_due_reminders(
        cls,
        target_dt: datetime.datetime = None,
        force_now: bool = False,
        catchup_minutes: int = 15,
        base_url: str = "http://localhost:5000"
    ) -> dict:
        """
        Primary worker method: inspects active medicine schedules in the database,
        determines due reminders matching current IST time, claims jobs atomically,
        and dispatches SMS and Outbound IVR voice calls.
        
        Supports restart catch-up window: reminders within past catchup_minutes
        that were missed during service downtime are dispatched safely without duplication.
        """
        now = target_dt or get_current_ist_datetime()
        today_date_str = now.strftime("%Y-%m-%d")
        current_time_str = now.strftime("%H:%M")

        conn = get_db_connection()
        cursor = conn.cursor()

        # Query all active prescriptions where both medicine and patient have reminder_consent enabled
        prescriptions = conn.execute("""
            SELECT p.*, pat.name as patient_name, pat.phone as patient_phone,
                   pat.preferred_language as patient_lang, pat.reminder_consent as patient_consent,
                   pat.sms_notifications_enabled, pat.voice_notifications_enabled,
                   pat.quiet_hours_start, pat.quiet_hours_end, pat.caregiver_phone, pat.caregiver_name
            FROM prescriptions p
            JOIN patients pat ON p.patient_id = pat.id
            WHERE p.reminder_consent = 1
              AND pat.reminder_consent = 1
              AND pat.consent_recorded = 1
        """).fetchall()

        dispatched_sms = []
        dispatched_voice = []
        skipped_quiet = []
        skipped_duplicates = []

        for p in prescriptions:
            sched_time = p["schedule_time"] or "08:00"
            # Format comparison: normalize to HH:MM
            sched_clean = sched_time.strip()[:5]

            # Timing Match Check: exact match, force override, or within catch-up window
            is_due = False
            if force_now:
                is_due = True
            elif sched_clean == current_time_str:
                is_due = True
            else:
                # Catch-up window check for missed jobs after service restart
                try:
                    sched_hour, sched_min = map(int, sched_clean.split(":"))
                    sched_dt = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
                    time_diff_sec = (now - sched_dt).total_seconds()
                    # Due if within past catchup_minutes (e.g. 0 to 900 seconds ago)
                    if 0 <= time_diff_sec <= (catchup_minutes * 60):
                        is_due = True
                except Exception:
                    is_due = False

            if not is_due:
                continue

            patient_id = p["patient_id"]
            rx_id = p["id"]
            med_name = p["medicine_name"]
            dosage = p["dosage"] or "Standard"
            timing_advice = p["timing_advice"] or "With Water"
            lang = p["patient_lang"] or "en"
            phone = p["patient_phone"]
            quiet = cls.is_quiet_hours(p, now)

            # ---------------------------------------------------------
            # 1. TIME-BASED SMS NOTIFICATION DISPATCH
            # ---------------------------------------------------------
            if p["sms_reminder_enabled"] != 0 and p["sms_notifications_enabled"] != 0:
                channel = "SMS"
                # Atomic Claim: insert or ignore due to UNIQUE constraint
                try:
                    cursor.execute("""
                        INSERT INTO reminder_dispatches (
                            prescription_id, patient_id, scheduled_date, scheduled_time, channel, status
                        ) VALUES (?, ?, ?, ?, ?, 'PENDING')
                    """, (rx_id, patient_id, today_date_str, sched_clean, channel))
                    dispatch_id = cursor.lastrowid
                    conn.commit()

                    # Execute dispatch
                    sms_res = CommunicationService.send_scheduled_medicine_sms(
                        dispatch_id=dispatch_id,
                        patient_id=patient_id,
                        phone=phone,
                        name=p["patient_name"],
                        med_name=med_name,
                        dosage=dosage,
                        timing_advice=timing_advice,
                        lang=lang,
                        conn=conn
                    )
                    dispatched_sms.append({
                        "dispatch_id": dispatch_id,
                        "medicine": med_name,
                        "patient": p["patient_name"],
                        "status": sms_res["status"],
                        "mode": sms_res["mode"]
                    })
                except Exception as e:
                    # Duplicate entry rejected by UNIQUE constraint: already handled today!
                    skipped_duplicates.append(f"SMS for Rx #{rx_id} at {sched_clean} already dispatched")

            # ---------------------------------------------------------
            # 2. AUTOMATED PROGRAMMABLE VOICE CALL WITH DTMF
            # ---------------------------------------------------------
            if p["voice_reminder_enabled"] != 0 and p["voice_notifications_enabled"] != 0:
                channel = "VOICE_CALL"

                # TRAI Quiet Hours Protection: skip nocturnal voice calls
                if quiet and not force_now:
                    skipped_quiet.append(f"Voice call for {p['patient_name']} skipped due to quiet hours ({p['quiet_hours_start']} - {p['quiet_hours_end']})")
                    continue

                try:
                    cursor.execute("""
                        INSERT INTO reminder_dispatches (
                            prescription_id, patient_id, scheduled_date, scheduled_time, channel, status
                        ) VALUES (?, ?, ?, ?, ?, 'PENDING')
                    """, (rx_id, patient_id, today_date_str, sched_clean, channel))
                    dispatch_id = cursor.lastrowid
                    conn.commit()

                    # Webhook endpoints for Twilio Voice IVR
                    twiml_url = f"{base_url}/api/voice/twiml/{dispatch_id}"
                    status_url = f"{base_url}/api/voice/status_callback/{dispatch_id}"

                    voice_res = CommunicationService.initiate_scheduled_voice_call(
                        dispatch_id=dispatch_id,
                        patient_id=patient_id,
                        phone=phone,
                        name=p["patient_name"],
                        med_name=med_name,
                        dosage=dosage,
                        timing_advice=timing_advice,
                        lang=lang,
                        twiml_url=twiml_url,
                        status_callback_url=status_url,
                        conn=conn
                    )
                    dispatched_voice.append({
                        "dispatch_id": dispatch_id,
                        "medicine": med_name,
                        "patient": p["patient_name"],
                        "status": voice_res["status"],
                        "call_sid": voice_res["call_sid"],
                        "mode": voice_res["provider"]
                    })
                except Exception as e:
                    skipped_duplicates.append(f"Voice call for Rx #{rx_id} at {sched_clean} already dispatched")

        conn.close()

        return {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "dispatched_sms_count": len(dispatched_sms),
            "dispatched_voice_count": len(dispatched_voice),
            "skipped_quiet_count": len(skipped_quiet),
            "skipped_duplicates_count": len(skipped_duplicates),
            "dispatched_sms": dispatched_sms,
            "dispatched_voice": dispatched_voice,
            "skipped_quiet": skipped_quiet
        }

    # -------------------------------------------------------------
    # DTMF Keypad Triage Handler (1=Taken, 2=Not Taken, 3=Snooze)
    # -------------------------------------------------------------
    @classmethod
    def handle_dtmf_response(cls, dispatch_id: int, digit: str, raw_params: dict = None) -> tuple[str, bool]:
        """
        Processes interactive telephone keypad input from senior patient:
        - Digit 1: Confirmed Taken -> Updates dose_logs to 'Taken', decrements stock.
        - Digit 2: Not Taken -> Updates dose_logs to 'Not Taken', alerts caregiver.
        - Digit 3: Snooze -> Reschedules automated retry call in 15 minutes.
        Returns: (spoken_confirmation_twiml, is_valid_selection)
        """
        conn = get_db_connection()
        dispatch = conn.execute("SELECT * FROM reminder_dispatches WHERE id = ?", (dispatch_id,)).fetchone()
        if not dispatch:
            conn.close()
            return CommunicationService.generate_dtmf_reply_twiml(digit, "en"), False

        rx = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (dispatch["prescription_id"],)).fetchone()
        patient = conn.execute("SELECT * FROM patients WHERE id = ?", (dispatch["patient_id"],)).fetchone()

        lang = patient["preferred_language"] if patient else "en"
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        digit_clean = str(digit).strip()

        cursor = conn.cursor()

        if digit_clean == "1":
            # ---------------- Key 1: Dose Taken ----------------
            cursor.execute("""
                UPDATE reminder_dispatches 
                SET status = 'CONFIRMED', dtmf_response = '1', provider_status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND dtmf_response IS NULL AND status NOT IN ('CONFIRMED', 'NOT_TAKEN', 'SNOOZED')
            """, (dispatch_id,))
            if cursor.rowcount != 1:
                conn.close()
                return CommunicationService.generate_dtmf_reply_twiml("1", lang), False

            # Log to dose_logs
            if rx and patient:
                cursor.execute("""
                    INSERT INTO dose_logs (prescription_id, patient_id, medicine_name, dose_time, scheduled_for, status, reason)
                    VALUES (?, ?, ?, ?, ?, 'Taken', 'IVR Telephone Keypad Confirmation (Digit 1)')
                """, (rx["id"], patient["id"], rx["medicine_name"], rx["timing"], today_str))

                # Decrement physical pill counter safely
                cursor.execute("UPDATE prescriptions SET stock_remaining = stock_remaining - 1 WHERE id = ? AND stock_remaining > 0", (rx["id"],))
                if cursor.rowcount == 1:
                    new_stock = conn.execute("SELECT stock_remaining FROM prescriptions WHERE id = ?", (rx["id"],)).fetchone()["stock_remaining"]

                    # Low stock threshold check
                    if new_stock <= rx["refill_threshold"]:
                        cursor.execute("""
                            INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                            VALUES (?, 'LOW_STOCK', ?)
                        """, (
                            patient["id"],
                            f"Low Stock Alert: {rx['medicine_name']} remaining count is {new_stock} tablets (below threshold {rx['refill_threshold']}). Refill recommended."
                        ))

            conn.commit()
            conn.close()
            return CommunicationService.generate_dtmf_reply_twiml("1", lang), True

        elif digit_clean == "2":
            # ---------------- Key 2: Dose Not Taken ----------------
            cursor.execute("""
                UPDATE reminder_dispatches 
                SET status = 'NOT_TAKEN', dtmf_response = '2', provider_status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND dtmf_response IS NULL AND status NOT IN ('CONFIRMED', 'NOT_TAKEN', 'SNOOZED')
            """, (dispatch_id,))
            if cursor.rowcount != 1:
                conn.close()
                return CommunicationService.generate_dtmf_reply_twiml("2", lang), False

            if rx and patient:
                cursor.execute("""
                    INSERT INTO dose_logs (prescription_id, patient_id, medicine_name, dose_time, scheduled_for, status, reason)
                    VALUES (?, ?, ?, ?, ?, 'Not Taken', 'Senior reported Not Taken via telephone keypad (Digit 2)')
                """, (rx["id"], patient["id"], rx["medicine_name"], rx["timing"], today_str))

                # Alert caregiver immediately of skipped dose
                cursor.execute("""
                    INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                    VALUES (?, 'SKIPPED_DOSE', ?)
                """, (
                    patient["id"],
                    f"Caregiver Alert: {patient['name']} reported that they have NOT taken their scheduled dose of {rx['medicine_name']} ({rx['timing']}) via IVR keypad."
                ))

            conn.commit()
            conn.close()
            return CommunicationService.generate_dtmf_reply_twiml("2", lang), True

        elif digit_clean == "3":
            # ---------------- Key 3: Snooze Reminder ----------------
            cursor.execute("""
                UPDATE reminder_dispatches 
                SET status = 'SNOOZED', dtmf_response = '3', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND dtmf_response IS NULL AND status NOT IN ('CONFIRMED', 'NOT_TAKEN', 'SNOOZED')
            """, (dispatch_id,))
            if cursor.rowcount != 1:
                conn.close()
                return CommunicationService.generate_dtmf_reply_twiml("3", lang), False

            # Calculate snooze retry time (+15 minutes IST)
            snooze_dt = get_current_ist_datetime() + datetime.timedelta(minutes=15)
            snooze_time_str = snooze_dt.strftime("%H:%M")

            # Insert snoozed follow-up dispatch
            try:
                cursor.execute("""
                    INSERT INTO reminder_dispatches (
                        prescription_id, patient_id, scheduled_date, scheduled_time, channel, status, retry_count
                    ) VALUES (?, ?, ?, ?, 'VOICE_CALL', 'PENDING', ?)
                """, (dispatch["prescription_id"], dispatch["patient_id"], today_str, snooze_time_str, dispatch["retry_count"] + 1))
            except Exception:
                pass  # Ignore if collision occurs

            conn.commit()
            conn.close()
            return CommunicationService.generate_dtmf_reply_twiml("3", lang), True

        else:
            # ---------------- Invalid Keypad Digit ----------------
            cursor.execute("""
                UPDATE reminder_dispatches 
                SET dtmf_response = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (digit_clean, dispatch_id))
            conn.commit()
            conn.close()
            return CommunicationService.generate_dtmf_reply_twiml("invalid", lang), False

    # -------------------------------------------------------------
    # Call Status Webhook Callback & Retry Policy
    # -------------------------------------------------------------
    @classmethod
    def handle_call_status_callback(
        cls,
        dispatch_id: int,
        call_status: str,
        duration: int = 0,
        error_code: str = None
    ) -> dict:
        """
        Receives webhook status from telephony gateway:
        Status events: 'initiated', 'ringing', 'answered', 'completed', 'busy', 'no-answer', 'failed'.
        Implements a limited retry policy (up to 2 retries spaced 15 mins apart) on busy/no-answer.
        """
        conn = get_db_connection()
        dispatch = conn.execute("SELECT * FROM reminder_dispatches WHERE id = ?", (dispatch_id,)).fetchone()
        if not dispatch:
            conn.close()
            return {"error": "Dispatch not found"}

        cursor = conn.cursor()
        new_status = dispatch["status"]
        retries = dispatch["retry_count"] or 0

        if call_status in ["completed", "in-progress"]:
            if dispatch["status"] not in ["CONFIRMED", "NOT_TAKEN", "SNOOZED"]:
                new_status = "ANSWERED"
        elif call_status in ["busy", "no-answer", "failed"]:
            if retries < 2:
                # Schedule retry after 15 minutes
                retry_dt = get_current_ist_datetime() + datetime.timedelta(minutes=15)
                retry_time_str = retry_dt.strftime("%H:%M")
                today_str = datetime.date.today().strftime("%Y-%m-%d")

                try:
                    cursor.execute("""
                        INSERT INTO reminder_dispatches (
                            prescription_id, patient_id, scheduled_date, scheduled_time, channel, status, retry_count
                        ) VALUES (?, ?, ?, ?, 'VOICE_CALL', 'PENDING', ?)
                    """, (dispatch["prescription_id"], dispatch["patient_id"], today_str, retry_time_str, retries + 1))
                except Exception:
                    pass
                new_status = f"RETRY_SCHEDULED ({retries+1}/2)"
            else:
                new_status = "MAX_RETRIES_EXCEEDED"
                # Alert family caregiver of unreachable senior
                patient = conn.execute("SELECT * FROM patients WHERE id = ?", (dispatch["patient_id"],)).fetchone()
                if patient:
                    cursor.execute("""
                        INSERT INTO caregiver_alerts (patient_id, alert_type, message)
                        VALUES (?, 'CALL_UNREACHABLE', ?)
                    """, (
                        patient["id"],
                        f"Unreachable Alert: Medicine reminder call to {patient['name']} ({patient['phone']}) was unanswered after 2 retries ({call_status}). Please check on senior."
                    ))

        cursor.execute("""
            UPDATE reminder_dispatches
            SET status = ?, provider_status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_status, call_status, error_code, dispatch_id))

        conn.commit()
        conn.close()

        return {
            "dispatch_id": dispatch_id,
            "call_status": call_status,
            "new_dispatch_status": new_status,
            "retry_count": retries
        }

    # -------------------------------------------------------------
    # Background Scheduler Thread Lifecycle
    # -------------------------------------------------------------
    @classmethod
    def _background_loop(cls, interval_seconds: int = 30):
        """Worker loop executing every interval_seconds."""
        while cls._scheduler_running:
            try:
                cls.check_and_dispatch_due_reminders()
            except Exception as e:
                # Fail-safe: prevent thread crash
                print(f"[SchedulerWorker Error] {type(e).__name__}")
            time.sleep(interval_seconds)

    @classmethod
    def start_background_scheduler(cls, interval_seconds: int = 30):
        """Starts the persistent background scheduler thread once."""
        with cls._lock:
            if cls._scheduler_running:
                return
            cls._scheduler_running = True
            cls._scheduler_thread = threading.Thread(
                target=cls._background_loop,
                args=(interval_seconds,),
                daemon=True,
                name="ElderCareReminderWorker"
            )
            cls._scheduler_thread.start()
            print(f"[Scheduler] Persistent background reminder worker started (checking every {interval_seconds}s in Asia/Kolkata).")

    @classmethod
    def stop_background_scheduler(cls):
        """Gracefully signals background worker to stop."""
        with cls._lock:
            cls._scheduler_running = False
            print("[Scheduler] Persistent background reminder worker stopped.")

    # -------------------------------------------------------------
    # Query & Diagnostic Helpers for Admin Dashboard
    # -------------------------------------------------------------
    @classmethod
    def get_upcoming_reminders(cls, limit: int = 20) -> list:
        """Fetches upcoming scheduled medicine reminders for active patients."""
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT p.id as prescription_id, p.medicine_name, p.dosage, p.timing,
                   p.schedule_time, p.timezone, p.sms_reminder_enabled, p.voice_reminder_enabled,
                   pat.id as patient_id, pat.name as patient_name, pat.phone as patient_phone,
                   pat.preferred_language, pat.reminder_consent
            FROM prescriptions p
            JOIN patients pat ON p.patient_id = pat.id
            WHERE p.reminder_consent = 1
              AND (p.sms_reminder_enabled = 1 OR p.voice_reminder_enabled = 1)
            ORDER BY p.schedule_time ASC, p.id ASC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @classmethod
    def get_recent_dispatches(cls, limit: int = 50) -> list:
        """Fetches recent SMS and Voice dispatch logs with DTMF status and provider IDs."""
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT rd.*, 
                   pat.name as patient_name, pat.phone as patient_phone, pat.preferred_language,
                   p.medicine_name, p.dosage
            FROM reminder_dispatches rd
            LEFT JOIN patients pat ON rd.patient_id = pat.id
            LEFT JOIN prescriptions p ON rd.prescription_id = p.id
            ORDER BY rd.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @classmethod
    def get_dispatch_metrics(cls) -> dict:
        """Aggregates dispatch statistics for today."""
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT status, channel, dtmf_response, COUNT(*) as cnt
            FROM reminder_dispatches
            WHERE scheduled_date = ?
            GROUP BY status, channel, dtmf_response
        """, (today_str,)).fetchall()
        conn.close()

        metrics = {
            "today": today_str,
            "total_today": 0,
            "sms_total": 0,
            "voice_total": 0,
            "confirmed_taken": 0,
            "not_taken": 0,
            "snoozed": 0,
            "dispatched": 0,
            "failed": 0,
            "pending": 0
        }

        for r in rows:
            cnt = r["cnt"]
            st = (r["status"] or "").upper()
            ch = (r["channel"] or "").upper()
            dtmf = str(r["dtmf_response"] or "").strip()

            metrics["total_today"] += cnt
            if "SMS" in ch:
                metrics["sms_total"] += cnt
            if "VOICE" in ch:
                metrics["voice_total"] += cnt

            if st == "CONFIRMED" or dtmf == "1":
                metrics["confirmed_taken"] += cnt
            elif st == "NOT_TAKEN" or dtmf == "2":
                metrics["not_taken"] += cnt
            elif st == "SNOOZED" or dtmf == "3":
                metrics["snoozed"] += cnt
            elif "DISPATCHED" in st or "SENT" in st or "ANSWERED" in st:
                metrics["dispatched"] += cnt
            elif "FAIL" in st or "MAX_RETRIES" in st:
                metrics["failed"] += cnt
            elif "PENDING" in st:
                metrics["pending"] += cnt

        return metrics
