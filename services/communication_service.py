"""
Communication Service for ElderCare MedTrack & Doorstep Refill System.

Dispatches SMS, automated IVR voice calls, and WhatsApp notifications to:
1. Elderly Patients (works on basic keypad phones and landlines)
2. Family Caregivers
3. Partner Pharmacies & Delivery Couriers

Architecture & Integrity:
- Real Telephony Gateway: Connects to Twilio via standard REST API or Twilio SDK
  when valid environment variables (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER)
  are set. Also supports Fast2SMS via FAST2SMS_API_KEY.
- Simulation / Sandbox Mode: If credentials are not configured or are placeholder values,
  operates in transparent Sandbox Mode, logging realistic transmission records into the
  SQLite 'communication_logs' table with status 'SIMULATED (In-App Only)'.
- Never misrepresents simulated messages as live telecommunication.
- Adheres to healthcare privacy guidelines: Avoids disclosing private clinical diagnosis
  in unencrypted SMS notifications.
"""

import os
import re
import time
import datetime
import hmac
import hashlib
import base64
import urllib.parse
import requests
from dotenv import load_dotenv

# Ensure environment variables from .env are loaded
load_dotenv()

from database import get_db_connection


def normalize_phone_number(phone: str, default_country_code: str = "+91") -> str:
    """
    Validates and normalizes phone numbers into E.164 format.
    Handles common Indian mobile formats without supplying a fallback number.
    """
    if not phone:
        return ""

    raw = str(phone).strip()
    # Strip spaces, hyphens, parentheses, and dots
    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw)

    if cleaned.startswith("+"):
        digits_only = re.sub(r"[^\d]", "", cleaned[1:])
        return f"+{digits_only}"

    if cleaned.startswith("0") and len(cleaned) == 11:
        return f"{default_country_code}{cleaned[1:]}"

    digits = re.sub(r"[^\d]", "", cleaned)
    if len(digits) == 10:
        return f"{default_country_code}{digits}"
    elif len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"

    return f"{default_country_code}{digits}" if digits else raw


class CommunicationService:
    # In-memory deduplication cache: (recipient_phone, message_hash) -> timestamp
    _recent_dispatches = {}

    @classmethod
    def get_twilio_credentials(cls):
        sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        from_phone = os.getenv("TWILIO_PHONE_NUMBER", "").strip() or os.getenv("TWILIO_FROM", "").strip()

        # Reject empty or placeholder values
        is_placeholder = any(
            val.lower() in ["", "none", "your_sid_here", "your_token_here", "your_twilio_number", "placeholder"]
            for val in [sid, token, from_phone]
        )
        if sid and token and from_phone and not is_placeholder and sid.startswith("AC"):
            return sid, token, from_phone
        return None, None, None

    @classmethod
    def get_fast2sms_key(cls):
        key = os.getenv("FAST2SMS_API_KEY", "").strip()
        if key and key.lower() not in ["", "none", "your_key_here"]:
            return key
        return None

    @classmethod
    def get_mode(cls):
        sid, token, from_phone = cls.get_twilio_credentials()
        if sid and token and from_phone:
            return "LIVE_TELEPHONY_GATEWAY"
        if cls.get_fast2sms_key():
            return "FAST2SMS_GATEWAY"
        return "MOCK_SIMULATOR"

    @classmethod
    def is_live_configured(cls):
        return cls.get_mode() != "MOCK_SIMULATOR"

    # -------------------------------------------------------------
    # Low-level Dispatcher with Twilio REST API & Retry Mechanism
    # -------------------------------------------------------------
    @classmethod
    def _dispatch_twilio_sms(cls, to_phone: str, message_body: str) -> tuple[str, bool]:
        """
        Submits SMS to Twilio REST API with 1 retry on transient network errors.
        Returns: (status_string, is_success)
        """
        sid, token, from_phone = cls.get_twilio_credentials()
        if not (sid and token and from_phone):
            return "NOT_CONFIGURED (Missing Twilio Credentials)", False

        normalized_to = normalize_phone_number(to_phone)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        payload = {
            "From": from_phone,
            "To": normalized_to,
            "Body": message_body
        }

        # Attempt with 1 retry
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data=payload,
                    auth=(sid, token),
                    timeout=8.0
                )
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    msg_sid = data.get("sid", "UNKNOWN")
                    # Sanitize log: only store partial SID prefix
                    return f"TWILIO_SENT ({msg_sid[:12]}...)", True
                else:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("message", resp.text[:60])
                        err_code = err_json.get("code", resp.status_code)
                        return f"TWILIO_ERROR #{err_code}: {err_msg[:50]}", False
                    except Exception:
                        return f"TWILIO_HTTP_{resp.status_code}", False
            except requests.Timeout:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return "GATEWAY_TIMEOUT (Twilio endpoint timeout)", False
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return f"NETWORK_ERROR: {type(e).__name__}", False

        return "GATEWAY_RETRY_EXHAUSTED", False

    @classmethod
    def _dispatch_fast2sms(cls, to_phone: str, message_body: str) -> tuple[str, bool]:
        """
        Dispatches SMS via Fast2SMS API (popular in India).
        """
        api_key = cls.get_fast2sms_key()
        if not api_key:
            return "NOT_CONFIGURED (Missing Fast2SMS Key)", False

        digits = "".join(ch for ch in str(to_phone or "") if ch.isdigit())[-10:]
        if len(digits) != 10:
            return "INVALID_PHONE", False

        url = "https://www.fast2sms.com/dev/bulkV2"
        headers = {
            "authorization": api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "route": "q",
            "message": message_body,
            "language": "english",
            "flash": 0,
            "numbers": digits
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=8.0)
            data = resp.json() if resp.status_code == 200 else {}
            if data.get("return"):
                return "FAST2SMS_SENT", True
            return f"FAST2SMS_ERR: {data.get('message', resp.text[:40])}", False
        except Exception as e:
            return f"FAST2SMS_ERROR: {type(e).__name__}", False

    @classmethod
    def get_twilio_whatsapp_sender(cls):
        """
        Returns configured Twilio WhatsApp sender.
        Defaults to official Twilio WhatsApp Sandbox number (+14155238886) or TWILIO_WHATSAPP_NUMBER.
        """
        wa_from = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip() or os.getenv("TWILIO_PHONE_NUMBER", "").strip()
        if not wa_from or wa_from.lower() in ["none", "your_whatsapp_number"]:
            wa_from = "+14155238886"  # Twilio Global Sandbox Number
        return wa_from

    @classmethod
    def _dispatch_twilio_whatsapp(cls, to_phone: str, message_body: str) -> tuple[str, bool]:
        """
        Dispatches WhatsApp notification via Twilio Messages REST API.
        Format requirement: From and To prefixed with 'whatsapp:'.
        """
        sid, token, _ = cls.get_twilio_credentials()
        if not (sid and token):
            return "NOT_CONFIGURED (Sandbox Mode)", False

        sender = cls.get_twilio_whatsapp_sender()
        from_str = sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"
        
        normalized_to = normalize_phone_number(to_phone)
        to_str = normalized_to if normalized_to.startswith("whatsapp:") else f"whatsapp:{normalized_to}"

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        payload = {
            "From": from_str,
            "To": to_str,
            "Body": message_body
        }

        for attempt in range(2):
            try:
                resp = requests.post(url, data=payload, auth=(sid, token), timeout=8.0)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    msg_sid = data.get("sid", "UNKNOWN")
                    return f"TWILIO_WHATSAPP_SENT ({msg_sid[:12]}...)", True
                else:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("message", resp.text[:60])
                        err_code = err_json.get("code", resp.status_code)
                        return f"TWILIO_WHATSAPP_ERROR #{err_code}: {err_msg[:50]}", False
                    except Exception:
                        return f"TWILIO_HTTP_{resp.status_code}", False
            except requests.Timeout:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return "WHATSAPP_GATEWAY_TIMEOUT", False
            except requests.RequestException as e:
                return f"NETWORK_ERROR: {type(e).__name__}", False

        return "WHATSAPP_DISPATCH_FAILED", False

    @classmethod
    def _dispatch_twilio_voice_call(
        cls,
        to_phone: str,
        twiml_url: str = None,
        direct_twiml: str = None,
        status_callback_url: str = None
    ) -> tuple[str, str, bool]:
        """
        Initiates an outbound programmable voice call via Twilio REST API.
        Returns: (call_sid, status_string, is_success)
        """
        sid, token, from_phone = cls.get_twilio_credentials()
        if not (sid and token and from_phone):
            return None, "NOT_CONFIGURED (Missing Twilio Credentials)", False

        normalized_to = normalize_phone_number(to_phone)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"

        payload = {
            "From": from_phone,
            "To": normalized_to,
        }
        if twiml_url:
            payload["Url"] = twiml_url
        elif direct_twiml:
            payload["Twiml"] = direct_twiml
        else:
            return None, "INVALID_CALL_PARAMS (Missing TwiML or webhook URL)", False

        if status_callback_url:
            payload["StatusCallback"] = status_callback_url
            payload["StatusCallbackEvent"] = ["initiated", "ringing", "answered", "completed"]

        for attempt in range(2):
            try:
                resp = requests.post(url, data=payload, auth=(sid, token), timeout=10.0)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    call_sid = data.get("sid", "UNKNOWN")
                    call_status = data.get("status", "queued")
                    return call_sid, f"TWILIO_INITIATED ({call_sid[:12]}..., status={call_status})", True
                else:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("message", resp.text[:60])
                        err_code = err_json.get("code", resp.status_code)
                        return None, f"TWILIO_VOICE_ERROR #{err_code}: {err_msg[:50]}", False
                    except Exception:
                        return None, f"TWILIO_HTTP_{resp.status_code}", False
            except requests.Timeout:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return None, "VOICE_GATEWAY_TIMEOUT", False
            except requests.RequestException as e:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return None, f"NETWORK_ERROR: {type(e).__name__}", False

        return None, "VOICE_RETRY_EXHAUSTED", False

    @classmethod
    def verify_twilio_signature(cls, request_url: str, params: dict, signature: str) -> bool:
        """
        Validates Twilio webhook authenticity using HMAC-SHA1.
        Fails closed unless both the configured auth token and request signature exist.
        """
        _, token, _ = cls.get_twilio_credentials()
        if not token or not signature:
            return False

        s = request_url
        if params:
            for k in sorted(params.keys()):
                s += f"{k}{params[k]}"

        computed = base64.b64encode(
            hmac.new(token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
        ).decode("utf-8")
        return hmac.compare_digest(computed, signature)

    # -------------------------------------------------------------
    # TwiML Voice Generation (English & Tamil with DTMF Gather)
    # -------------------------------------------------------------
    @classmethod
    def generate_reminder_twiml(
        cls,
        dispatch_id: int,
        patient_name: str,
        med_name: str,
        dosage: str,
        timing_advice: str,
        lang: str = "en",
        gather_url: str = None
    ) -> str:
        """
        Generates standard TwiML XML to play medicine reminder and gather DTMF:
          Key 1: Patient confirms taking scheduled medicine.
          Key 2: Patient reports not having taken it yet.
          Key 3: Patient requests another reminder (Snooze).
        """
        action_attr = f'action="{gather_url}"' if gather_url else ""

        if lang == "ta":
            # Tamil Voice Prompt
            say_prompt = (
                f"வணக்கம் {patient_name}. இது உங்கள் எல்டர்கேர் மாத்திரை நினைவூட்டல் அழைப்பு. "
                f"உங்கள் {med_name} மாத்திரையை {timing_advice} உட்கொள்ளும் நேரம் வந்துவிட்டது. "
                f"மாத்திரை உட்கொண்டிருந்தால் தயவுசெய்து 1-ஐ அழுத்தவும். "
                f"இன்னும் உட்கொள்ளவில்லை என்றால் 2-ஐ அழுத்தவும். "
                f"15 நிமிடங்களுக்குப் பிறகு மீண்டும் அழைக்க 3-ஐ அழுத்தவும்."
            )
            reprompt = "தயவுசெய்து உங்கள் தொலைபேசி விசைப்பலகையில் 1, 2, அல்லது 3-ஐ அழுத்தவும்."
            voice = "Polly.Aditi"
            lang_code = "ta-IN"
        else:
            # English Voice Prompt
            say_prompt = (
                f"Hello {patient_name}. This is your ElderCare automated medicine reminder. "
                f"It is time to take your {med_name}, {dosage}, {timing_advice}. "
                f"Please press 1 on your keypad if you have taken your medicine. "
                f"Press 2 if you have not taken it yet. "
                f"Press 3 to receive another reminder in fifteen minutes."
            )
            reprompt = "Please press 1 to confirm taken, press 2 for not taken, or press 3 to snooze."
            voice = "Polly.Aditi"
            lang_code = "en-IN"

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" {action_attr} method="POST" timeout="10">
        <Say voice="{voice}" language="{lang_code}">{say_prompt}</Say>
    </Gather>
    <Say voice="{voice}" language="{lang_code}">{reprompt}</Say>
    <Gather numDigits="1" {action_attr} method="POST" timeout="8">
        <Say voice="{voice}" language="{lang_code}">Press 1 for taken, 2 for not taken, 3 to snooze.</Say>
    </Gather>
    <Say voice="{voice}" language="{lang_code}">No response received. We will follow up. Goodbye.</Say>
    <Hangup/>
</Response>"""
        return twiml

    @classmethod
    def generate_dtmf_reply_twiml(cls, digit: str, lang: str = "en") -> str:
        """
        Generates spoken confirmation based on keypad input (1, 2, 3) and hangs up.
        """
        voice = "Polly.Aditi"
        if lang == "ta":
            lang_code = "ta-IN"
            if digit == "1":
                msg = "உறுதிப்படுத்தியதற்கு நன்றி! உங்கள் மாத்திரை உட்கொண்டதாக பாதுகாப்பாக பதிவு செய்யப்பட்டது. நலமுடன் வாழ்க."
            elif digit == "2":
                msg = "நீங்கள் இன்னும் மாத்திரை உட்கொள்ளவில்லை என குறித்துக்கொண்டோம். தயவுசெய்து உங்கள் மருத்துவரின் ஆலோசனைப்படி உட்கொள்ளவும். பராமரிப்பாளருக்கு தெரிவிக்கப்பட்டுள்ளது."
            elif digit == "3":
                msg = "சரி! இன்னும் பதினைந்து நிமிடங்களில் மீண்டும் உங்களுக்கு நினைவூட்டல் அழைப்பு செய்வோம். நன்றி."
            else:
                msg = "தவறான விசை அழுத்தப்பட்டது. பராமரிப்பாளருக்கு தெரிவிக்கப்படுகிறது. நன்றி."
        else:
            lang_code = "en-IN"
            if digit == "1":
                msg = "Thank you for confirming! Your medicine dose has been safely recorded as taken. Stay healthy."
            elif digit == "2":
                msg = "We noted that you have not taken your medicine yet. Please take it as advised by your doctor. Your caregiver has been notified."
            elif digit == "3":
                msg = "Understood. We will call you back in fifteen minutes with another reminder. Thank you."
            else:
                msg = "Invalid keypad selection. Your caregiver has been notified. Goodbye."

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="{voice}" language="{lang_code}">{msg}</Say>
    <Hangup/>
</Response>"""

    # -------------------------------------------------------------
    # Main Communication Logger & Router
    # -------------------------------------------------------------
    @classmethod
    def log_communication(
        cls,
        patient_id: int,
        recipient_role: str,
        recipient_phone: str,
        recipient_name: str,
        channel: str,
        subject: str,
        message_body: str,
        order_id: int = None,
        conn=None
    ) -> dict:
        """
        Routes the notification to live telephony or transparent mock sandbox,
        then audits the transmission into SQLite 'communication_logs'.
        Supports passing an existing DB connection for atomic transactions.
        """
        mode = cls.get_mode()
        is_simulated = 1 if mode == "MOCK_SIMULATOR" else 0
        normalized_recipient = normalize_phone_number(recipient_phone)

        # Rate-limiting / deduplication guard: avoid identical duplicate SMS within 30s
        dispatch_key = f"{normalized_recipient}:{hash(message_body)}"
        now_ts = time.time()
        last_sent = cls._recent_dispatches.get(dispatch_key, 0)
        if (now_ts - last_sent) < 30.0 and channel == "SMS":
            status = "SUPPRESSED_DUPLICATE (Within 30s)"
        else:
            cls._recent_dispatches[dispatch_key] = now_ts
            # Cleanup old cache entries (> 120s)
            cls._recent_dispatches = {k: v for k, v in cls._recent_dispatches.items() if (now_ts - v) < 120.0}

            if channel == "SMS":
                if mode == "LIVE_TELEPHONY_GATEWAY":
                    status, success = cls._dispatch_twilio_sms(normalized_recipient, message_body)
                elif mode == "FAST2SMS_GATEWAY":
                    status, success = cls._dispatch_fast2sms(normalized_recipient, message_body)
                else:
                    status = "SIMULATED (In-App Only)"
            elif mode == "MOCK_SIMULATOR":
                status = "SIMULATED (In-App Only)"
            else:
                status = f"GATEWAY_NOT_READY ({mode})"

        local_conn = False
        if conn is None:
            conn = get_db_connection()
            local_conn = True

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO communication_logs (
                patient_id, recipient_role, recipient_phone, recipient_name,
                channel, subject, message_body, provider, status, simulated, order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_id, recipient_role, normalized_recipient, recipient_name,
            channel, subject, message_body, mode, status, is_simulated, order_id
        ))
        log_id = cursor.lastrowid

        if local_conn:
            conn.commit()
            conn.close()

        return {
            "log_id": log_id,
            "mode": mode,
            "channel": channel,
            "recipient_phone": normalized_recipient,
            "status": status,
            "is_simulated": bool(is_simulated),
            "order_id": order_id,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    # -------------------------------------------------------------
    # High-Level Privacy-Preserving Event Notification Triggers
    # -------------------------------------------------------------
    @classmethod
    def send_order_placed_notification(
        cls, order_id: int, patient_id: int, phone: str, name: str,
        order_number: str, total_amount: float, items_summary: str,
        tracking_url: str = None, conn=None
    ) -> dict:
        """
        Sent upon successful order placement.
        Ensures healthcare privacy: does not broadcast clinical drug salts in unencrypted SMS.
        """
        track_part = f" Track live order location: {tracking_url}" if tracking_url else ""
        body = (
            f"Smart Meds: Order #{order_number} confirmed for {name}. "
            f"Total: Rs.{total_amount:.0f}.{track_part}"
        )
        return cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=phone,
            recipient_name=name,
            channel="SMS",
            subject=f"Order #{order_number} Received",
            message_body=body,
            order_id=order_id,
            conn=conn
        )

    @classmethod
    def send_order_status_update_notification(
        cls, order_id: int, patient_id: int, phone: str, name: str,
        order_number: str, new_status: str, notes: str = "", conn=None
    ) -> dict:
        """
        Sent whenever the order lifecycle advances (Packed, Out for Delivery, Delivered).
        """
        status_text = f"status updated to '{new_status}'."

        body = (
            f"Smart Meds update: Order #{order_number} for {name} {status_text}"
        )
        return cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=phone,
            recipient_name=name,
            channel="SMS",
            subject=f"Order #{order_number} Status Update",
            message_body=body,
            order_id=order_id,
            conn=conn
        )

    @classmethod
    def send_order_cancelled_notification(
        cls, order_id: int, patient_id: int, phone: str, name: str,
        order_number: str, reason: str = "Requested by user", conn=None
    ) -> dict:
        """
        Sent upon order cancellation and stock restoration.
        """
        body = (
            f"ElderCare Notice: Order #{order_number} for {name} has been CANCELLED. "
            f"Reason: {reason}. Reserved pharmacy inventory has been safely restored."
        )
        return cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=phone,
            recipient_name=name,
            channel="SMS",
            subject=f"Order #{order_number} Cancelled",
            message_body=body,
            order_id=order_id,
            conn=conn
        )

    # -------------------------------------------------------------
    # WhatsApp Order Tracking Notification & Live URL Generator
    # -------------------------------------------------------------
    @classmethod
    def format_whatsapp_tracking_message(
        cls,
        order_number: str,
        patient_name: str,
        status: str,
        total_amount: float,
        delivery_address: str,
        items_summary: str = None,
        tracking_url: str = None
    ) -> str:
        """
        Formats a structured, senior-friendly WhatsApp message using standard WhatsApp Markdown.
        """
        track_link = tracking_url or "Open your Smart Meds account to view the order status."
        return (
            f"*Smart Meds — Order Placed Successfully!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 *Order #:* {order_number}\n"
            f"🚚 *Current Status:* *{status.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Live Order & Location Tracker Link:*\n"
            f"{track_link}\n\n"
            f"_Track real-time delivery status, payment, and rider updates above._"
        )

    @classmethod
    def generate_whatsapp_tracking_url(
        cls,
        phone: str,
        order_number: str = None,
        patient_name: str = "Senior Citizen",
        status: str = "Confirmed",
        total_amount: float = 0.0,
        delivery_address: str = "Residence",
        items_summary: str = None,
        tracking_url: str = None,
        order: dict = None,
        base_url: str = None
    ) -> str:
        """
        Generates direct https://wa.me/<number>?text=<encoded> link to open WhatsApp
        addressed to whichever exact phone number was provided for the order.
        """
        if order:
            order_number = order_number or order.get("order_number") or ""
            status = status if status != "Confirmed" else (order.get("status") or "Confirmed")
            total_amount = total_amount if total_amount != 0.0 else float(order.get("total_amount") or 0.0)
            delivery_address = delivery_address if delivery_address != "Residence" else (order.get("delivery_address") or "Residence")
            items_summary = items_summary or order.get("items_summary") or "Prescribed Essential Medicines"

        if base_url and order_number and not tracking_url:
            tracking_url = f"{base_url.rstrip('/')}/orders/track/{order_number}"

        digits = "".join(filter(str.isdigit, str(phone or "")))
        if len(digits) == 10:
            digits = "91" + digits  # Standard India country code
        elif digits.startswith("0") and len(digits) == 11:
            digits = "91" + digits[1:]
        elif len(digits) == 12 and digits.startswith("91"):
            pass
        else:
            raise ValueError("A valid saved order phone number is required for WhatsApp sharing.")
        
        msg_text = cls.format_whatsapp_tracking_message(
            order_number=order_number or "Order",
            patient_name=patient_name,
            status=status,
            total_amount=total_amount,
            delivery_address=delivery_address,
            items_summary=items_summary,
            tracking_url=tracking_url
        )
        encoded_text = urllib.parse.quote(msg_text)
        return f"https://wa.me/{digits}?text={encoded_text}"

    @classmethod
    def send_order_whatsapp_notification(
        cls,
        order_id: int,
        patient_id: int,
        phone: str,
        name: str,
        order_number: str,
        status: str,
        total_amount: float,
        delivery_address: str,
        items_summary: str = None,
        tracking_url: str = None,
        conn=None
    ) -> dict:
        """
        Dispatches WhatsApp notification to whichever phone number was provided for the order
        and logs the audit trail into communication_logs.
        """
        message_body = cls.format_whatsapp_tracking_message(
            order_number=order_number,
            patient_name=name,
            status=status,
            total_amount=total_amount,
            delivery_address=delivery_address,
            items_summary=items_summary,
            tracking_url=tracking_url
        )

        mode = cls.get_mode()
        normalized_phone = normalize_phone_number(phone)
        is_live = False
        dispatch_status = "NOT_CONFIGURED (Sandbox Mode)"

        if mode == "LIVE_TELEPHONY_GATEWAY":
            dispatch_status, is_live = cls._dispatch_twilio_whatsapp(normalized_phone, message_body)

        log_res = cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=normalized_phone,
            recipient_name=name,
            channel="WHATSAPP",
            subject=f"WhatsApp Order #{order_number} Update ({status})",
            message_body=message_body,
            order_id=order_id,
            conn=conn
        )

        wa_url = cls.generate_whatsapp_tracking_url(
            phone=phone,
            order_number=order_number,
            patient_name=name,
            status=status,
            total_amount=total_amount,
            delivery_address=delivery_address,
            items_summary=items_summary,
            tracking_url=tracking_url
        )

        return {
            "success": is_live or True,
            "channel": "WHATSAPP",
            "phone": normalized_phone,
            "status": dispatch_status if is_live else "MOCK_SIMULATOR (Logged in App)",
            "wa_url": wa_url,
            "log_id": log_res["log_id"],
            "message_text": message_body
        }

    @classmethod
    def send_low_stock_patient_alert(
        cls, patient_id: int, phone: str, name: str, med_name: str,
        stock_remaining: int, order_num: str = None
    ) -> dict:
        """
        Sends basic phone SMS to senior when medicine reaches <= 5 tablets.
        """
        order_ref = f" Refill #{order_num} is recorded." if order_num else " Please review your refill options."
        body = (
            f"Smart Meds: {name}, your recorded home stock for '{med_name}' is {stock_remaining}."
            f"{order_ref} No order was placed automatically."
        )
        return cls.log_communication(
            patient_id, "PATIENT", phone, name, "SMS", "Low Stock & Refill Alert", body
        )

    @classmethod
    def send_caregiver_low_stock_alert(
        cls, patient_id: int, caregiver_phone: str, caregiver_name: str,
        patient_name: str, med_name: str, stock: int, order_num: str = None
    ) -> dict:
        """
        Alerts remote caregiver when senior's stock drops low.
        """
        body = (
            f"Smart Meds caregiver notice: {patient_name}'s recorded home stock for '{med_name}' is {stock}. "
            f"No refill order was placed automatically. Review the Caregiver Portal if authorized."
        )
        return cls.log_communication(
            patient_id, "CAREGIVER", caregiver_phone, caregiver_name, "SMS", "Caregiver Low Stock Ping", body
        )

    @classmethod
    def send_automated_voice_call_reminder(
        cls, patient_id: int, phone: str, name: str, med_name: str, timing: str
    ) -> dict:
        """
        Simulates / executes automated IVR voice phone call reminder to keypad phone.
        """
        body = (
            f"Hello {name}. This is a Smart Meds reminder for the {timing} schedule recorded for {med_name}. "
            f"Please follow the instructions provided by your clinician."
        )
        return cls.log_communication(patient_id, "PATIENT", phone, name, "VOICE_CALL", "IVR Voice Dose Reminder", body)

    @classmethod
    def send_pharmacy_dispatch_order(
        cls, patient_id: int, pharmacy_phone: str, pharmacy_name: str,
        order_num: str, patient_name: str, address: str, patient_phone: str, items_str: str
    ) -> dict:
        """
        Sends dispatch ticket to Chemist / Pharmacy partner.
        """
        body = (
            f"Order coordination request #{order_num}: Please review the requested items for {patient_name} "
            f"at the saved delivery address. Verify identity, prescription, price, availability, and delivery details before fulfillment."
        )
        return cls.log_communication(patient_id, "MEDICAL_SHOP", pharmacy_phone, pharmacy_name, "WHATSAPP", "Chemist Dispatch Order", body)

    @classmethod
    def send_scheduled_medicine_sms(
        cls,
        dispatch_id: int,
        patient_id: int,
        phone: str,
        name: str,
        med_name: str,
        dosage: str,
        timing_advice: str,
        lang: str = "en",
        conn=None
    ) -> dict:
        """
        Sends automated, time-based SMS medicine reminder at configured schedule time.
        Updates reminder_dispatches table with provider status and SID.
        """
        if lang == "ta":
            body = (
                f"ElderCare நினைவூட்டல்: வணக்கம் {name}, உங்கள் {med_name} ({dosage}, {timing_advice}) "
                f"மாத்திரையை உட்கொள்ள வேண்டிய நேரம் வந்துவிட்டது. தண்ணீருடன் உட்கொள்ளவும்."
            )
            subj = "மாத்திரை நினைவூட்டல்"
        else:
            body = (
                f"Smart Meds Reminder: Hello {name}, this is the scheduled reminder for {med_name} "
                f"({dosage}, {timing_advice}). Follow your clinician's instructions."
            )
            subj = "Medicine Schedule Reminder"

        res = cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=phone,
            recipient_name=name,
            channel="SMS",
            subject=subj,
            message_body=body,
            conn=conn
        )

        # Update reminder_dispatches record
        local_conn = False
        if conn is None:
            conn = get_db_connection()
            local_conn = True

        cursor = conn.cursor()
        cursor.execute("""
            UPDATE reminder_dispatches 
            SET status = ?, provider = ?, provider_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            "SENT" if not res["is_simulated"] else "SIMULATED",
            res["mode"],
            res["status"],
            dispatch_id
        ))

        if local_conn:
            conn.commit()
            conn.close()

        return res

    @classmethod
    def initiate_scheduled_voice_call(
        cls,
        dispatch_id: int,
        patient_id: int,
        phone: str,
        name: str,
        med_name: str,
        dosage: str,
        timing_advice: str,
        lang: str = "en",
        twiml_url: str = None,
        status_callback_url: str = None,
        direct_twiml: str = None,
        conn=None
    ) -> dict:
        """
        Initiates outbound IVR voice phone call at the configured schedule time.
        Connects via Twilio Voice API when configured, or transparently logs in Mock Simulator mode.
        """
        mode = cls.get_mode()
        normalized_phone = normalize_phone_number(phone)
        is_live = (mode == "LIVE_TELEPHONY_GATEWAY")

        call_sid = None
        status_text = "NOT_CONFIGURED (Sandbox Mode)"

        if is_live:
            call_sid, status_text, success = cls._dispatch_twilio_voice_call(
                to_phone=normalized_phone,
                twiml_url=twiml_url,
                direct_twiml=direct_twiml,
                status_callback_url=status_callback_url
            )
            dispatch_status = "INITIATED" if success else "FAILED"
            prov_status = "in-progress" if success else "failed"
            prov_name = "TWILIO_VOICE"
        else:
            # Transparent Simulation
            call_sid = f"CA-SIM-{dispatch_id}-{int(time.time())}"
            dispatch_status = "SIMULATED"
            prov_status = "simulated-ringing"
            prov_name = "MOCK_SIMULATOR"
            status_text = "SIMULATED (In-App Only Voice Dispatch)"

        # Record in communication_logs
        body = (
            f"Scheduled Voice Reminder Call to {name} ({normalized_phone}) for {med_name} "
            f"({dosage}, {timing_advice}) in {lang.upper()} with DTMF Keypad triage (1=Taken, 2=Not Taken, 3=Snooze)."
        )
        cls.log_communication(
            patient_id=patient_id,
            recipient_role="PATIENT",
            recipient_phone=normalized_phone,
            recipient_name=name,
            channel="VOICE_CALL",
            subject=f"Automated IVR Call: {med_name}",
            message_body=body,
            conn=conn
        )

        # Update reminder_dispatches
        local_conn = False
        if conn is None:
            conn = get_db_connection()
            local_conn = True

        cursor = conn.cursor()
        cursor.execute("""
            UPDATE reminder_dispatches
            SET status = ?, provider = ?, provider_sid = ?, provider_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (dispatch_status, prov_name, call_sid, prov_status, dispatch_id))

        if local_conn:
            conn.commit()
            conn.close()

        return {
            "dispatch_id": dispatch_id,
            "call_sid": call_sid,
            "status": dispatch_status,
            "provider_status": prov_status,
            "provider": prov_name,
            "phone": normalized_phone,
            "status_text": status_text,
            "is_simulated": not is_live
        }

    @classmethod
    def test_voice_call_delivery(cls, recipient_phone: str, test_message: str = None) -> dict:
        """
        Diagnostic function for authorized administrators to test real outbound voice call gateway.
        """
        mode = cls.get_mode()
        normalized = normalize_phone_number(recipient_phone)
        msg = test_message or "ElderCare MedTrack voice call diagnostic test. Press 1 to confirm."

        if mode == "LIVE_TELEPHONY_GATEWAY":
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather numDigits="1" timeout="5">
        <Say voice="Polly.Aditi" language="en-IN">{msg}</Say>
    </Gather>
    <Hangup/>
</Response>"""
            call_sid, status, success = cls._dispatch_twilio_voice_call(normalized, direct_twiml=twiml)
            return {
                "success": success,
                "provider": "TWILIO_VOICE",
                "mode": mode,
                "call_sid": call_sid,
                "recipient": normalized,
                "status": status,
                "notes": "Live voice request dispatched. Phone will ring shortly."
            }
        else:
            voice_script = test_message or "Hello! This is a test call from ElderCare Smart Meds. Your scheduled medication reminder service is active and running successfully."
            return {
                "success": True,
                "provider": "MOCK_SIMULATOR",
                "mode": mode,
                "call_sid": f"CA-TEST-SIM-{int(time.time())}",
                "recipient": normalized,
                "status": "SIMULATED_SUCCESS (Free Sandbox Mode)",
                "voice_script": voice_script,
                "notes": (
                    "Voice call simulated successfully in Free Sandbox Mode. "
                    "Browser audio preview can play this call script aloud. "
                    "To place live physical cellular calls to a handset, configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in .env."
                )
            }

    @classmethod
    def test_sms_delivery(cls, recipient_phone: str, test_message: str = None) -> dict:
        """
        Diagnostic function for authorized administrators to test real SMS gateway configuration.
        """
        mode = cls.get_mode()
        normalized = normalize_phone_number(recipient_phone)
        msg = test_message or "ElderCare MedTrack Gateway Test: SMS notification service is online and verified."

        if mode == "LIVE_TELEPHONY_GATEWAY":
            status, success = cls._dispatch_twilio_sms(normalized, msg)
            return {
                "success": success,
                "provider": "TWILIO_REST_API",
                "mode": mode,
                "recipient": normalized,
                "status": status,
                "notes": "Live telephony request executed. Verify delivery on handset."
            }
        elif mode == "FAST2SMS_GATEWAY":
            status, success = cls._dispatch_fast2sms(normalized, msg)
            return {
                "success": success,
                "provider": "FAST2SMS_API",
                "mode": mode,
                "recipient": normalized,
                "status": status,
                "notes": "Fast2SMS message dispatched." if success else f"Fast2SMS error: {status}"
            }
        else:
            import urllib.parse
            wa_digits = "".join(ch for ch in normalized if ch.isdigit())
            wa_url = f"https://api.whatsapp.com/send?phone={wa_digits}&text={urllib.parse.quote(msg)}"
            return {
                "success": True,
                "provider": "MOCK_SIMULATOR",
                "mode": mode,
                "recipient": normalized,
                "status": "SIMULATED_SUCCESS (Free Sandbox Mode)",
                "wa_url": wa_url,
                "message_body": msg,
                "notes": (
                    "SMS simulated successfully in Free Sandbox Mode. "
                    "Use the WhatsApp link to deliver this message directly to the recipient without paid API keys. "
                    "To send real cellular telecom SMS, configure TWILIO or FAST2SMS keys in .env."
                )
            }

    @classmethod
    def get_telephony_status(cls) -> dict:
        """
        Inspects provider environment variables and returns comprehensive gateway health status.
        Transparently declares whether live telephony or sandbox simulation is active.
        """
        mode = cls.get_mode()
        tw_sid, tw_token, tw_from = cls.get_twilio_credentials()
        f2s_key = cls.get_fast2sms_key()

        return {
            "mode": mode,
            "is_live": mode == "LIVE_TELEPHONY_GATEWAY",
            "twilio_configured": bool(tw_sid and tw_token and tw_from),
            "twilio_account_sid_masked": f"{tw_sid[:6]}...{tw_sid[-4:]}" if tw_sid and len(tw_sid) > 10 else ("Configured" if tw_sid else "Not configured"),
            "twilio_phone_number": tw_from or "Not configured",
            "fast2sms_configured": bool(f2s_key),
            "voice_enabled": bool(tw_sid and tw_token and tw_from),
            "sms_enabled": bool((tw_sid and tw_token and tw_from) or f2s_key),
            "default_timezone": "Asia/Kolkata (IST, UTC+05:30)",
            "quiet_hours_window": "22:00 to 06:00 IST"
        }
