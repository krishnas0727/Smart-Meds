# ElderCare MedTrack — Telephony & Automated Reminder Scheduler Guide

## 1. Architectural Overview

ElderCare MedTrack includes an enterprise-grade automated telephony and reminder engine designed specifically for senior citizens and geriatric healthcare regimens.

Senior patients often struggle with complex smartphone notifications or may not own a smartphone at all. The automated telephony engine dispatches **cellular SMS reminders** and **interactive outbound voice calls** to regular feature phones or smartphones at exact scheduled times without requiring the patient or caregiver to open any app or browser.

```
       +--------------------------------------------------------+
       |   Persistent Background Scheduler Thread / worker.py   |
       |             Checks every 30s in Asia/Kolkata           |
       +---------------------------+----------------------------+
                                   |
                +------------------v-------------------+
                | 1. Timezone Check (Asia/Kolkata IST) |
                | 2. TRAI Quiet Hours (22:00-06:00)    |
                | 3. Patient & Prescription Consent    |
                +------------------+-------------------+
                                   |
                 +-----------------v------------------+
                 | Atomic Database Claiming           |
                 | UNIQUE(rx_id, date, time, channel) |
                 +--------+------------------+--------+
                          |                  |
           SMS Channel    |                  |  Voice IVR Channel
+-------------------------v---+          +---v----------------------------+
| Twilio REST API / Fast2SMS  |          | Twilio Outbound Voice Call     |
| Cellular SMS Delivery       |          | Speaks English / தமிழ் Prompt   |
| Message SID & Status Logged |          +---------------+----------------+
+-----------------------------+                          |
                                                         | Senior presses keypad
                                               +---------v-----------+
                                               | Key 1: Confirmed    | -> Decrement Stock, Log Taken
                                               | Key 2: Not Taken    | -> Alert Caregiver, Log Skipped
                                               | Key 3: Snooze       | -> Reschedule Retry in 15 mins
                                               +---------------------+
```

---

## 2. Timezone & Anti-Duplicate Architecture

### A. Indian Standard Time (IST, UTC+05:30)
All schedules are strictly evaluated in `Asia/Kolkata` (+05:30) using standard Python `datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="Asia/Kolkata")`. This eliminates dependency on external operating system timezone files on Windows and Linux alike.

### B. Anti-Duplicate Concurrency Protection
To guarantee that neither duplicate SMS nor duplicate calls occur—even across multi-threaded workers, application restarts, or server crashes—the SQLite database enforces a table-level unique constraint on `reminder_dispatches`:

```sql
UNIQUE (prescription_id, scheduled_date, scheduled_time, channel)
```

Before dispatching, the worker executes an atomic transactional insert with status `PENDING`. Any concurrent thread or retry attempting to claim the same slot will trigger a database constraint violation and safely abort.

### C. Missed Job Catch-Up Window
If the server or worker is stopped for maintenance, upon reboot the scheduler checks for due reminders within the past 15-minute catch-up window. Unprocessed reminders are dispatched safely without duplicating already completed alerts.

---

## 3. Voice IVR TwiML & Interactive Keypad (DTMF)

When the senior patient answers an outbound reminder call, Twilio fetches dynamic TwiML from `/api/voice/twiml/<dispatch_id>`.

### Keypad Menu Options:
- **Key 1 (Dose Taken)**:
  - Updates `reminder_dispatches` status to `CONFIRMED`.
  - Appends an entry to `dose_logs` with status `Taken`.
  - Decrements the physical tablet stock in `prescriptions`.
  - Checks if the remaining stock is at or below the `refill_threshold`. If so, generates a low-stock alert for the caregiver.
  - Speaks a reassuring voice confirmation: *"Thank you. Your medicine dose has been recorded as taken. Stay healthy!"* or in Tamil: *"நன்றி! உங்கள் மாத்திரை உட்கொள்ளல் வெற்றிகரமாக பதிவு செய்யப்பட்டது."*
- **Key 2 (Dose Not Taken)**:
  - Updates `reminder_dispatches` status to `NOT_TAKEN`.
  - Appends an entry to `dose_logs` with status `Not Taken`.
  - Generates an immediate priority alert in `caregiver_alerts` notifying the family caregiver.
  - Speaks: *"We have noted that you have not taken your dose. Please take it as soon as possible, or consult your caregiver."*
- **Key 3 (Snooze)**:
  - Updates status to `SNOOZED`.
  - Reschedules an automated retry voice call in 15 minutes.
  - Speaks: *"Understood. We will call you again in 15 minutes with a reminder."*

---

## 4. India TRAI & DLT Regulatory Compliance

### A. TRAI Quiet Hours
Under Telecom Regulatory Authority of India (TRAI) regulations and geriatric medicine guidelines, non-emergency automated calls and alerts must not disturb senior citizens during nocturnal rest hours:
- Default quiet window: **22:00 to 06:00 IST**.
- During this window, automated reminder calls are suppressed unless explicitly overridden by a patient's nocturnal medicine regimen.

### B. TRAI DLT Registration Requirements for Live SMS
For production deployment in India using Fast2SMS or Indian SMS routes:
1. **Entity Registration**: Register the organization on a telecom DLT portal (e.g. Jio DLT, Airtel DLT, Vodafone Idea DLT).
2. **Sender ID (Header)**: Obtain a 6-character alpha sender header (e.g. `ELDCARE` or `MEDTRK`).
3. **Template Registration**: Register the exact transactional reminder template with variables:
   `Dear {#var#}, reminder to take your medicine {#var#} ({#var#}) {#var#}. ElderCare MedTrack`

---

## 5. Environment Variables & Gateway Configuration

Create or update `.env` in the project root:

```env
# Flask Application Secret Key
SECRET_KEY=replace-with-a-generated-secret

# Public URL for Twilio Webhooks (Use ngrok for local development)
APP_BASE_URL=https://your-domain-or-ngrok.io

# Twilio Telephony Credentials
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+1234567890

# Optional Alternative India SMS Gateway
FAST2SMS_API_KEY=your_fast2sms_api_key_here
```

### Transparent Sandbox / Simulation Mode
If `TWILIO_ACCOUNT_SID` or `TWILIO_AUTH_TOKEN` is omitted:
- The system operates transparently in **Mock Simulator Mode**.
- Calls and SMS return `NOT_CONFIGURED (Sandbox Mode)` with clear diagnostic explanations.
- DTMF webhooks and scheduler logic continue to function in the test suite without live charges.
- The UI and Admin portal never claim a message was delivered unless confirmed by the provider.

---

## 6. Webhook Endpoints & Signature Security

Twilio invokes these public HTTP endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/voice/twiml/<dispatch_id>` | `GET` / `POST` | Generates initial `<Gather>` TwiML prompt for the call. |
| `/api/voice/gather/<dispatch_id>` | `POST` | Receives DTMF keypad digit (1, 2, 3) from the senior handset. |
| `/api/voice/status_callback/<dispatch_id>` | `POST` | Tracks call lifecycle (`initiated`, `answered`, `completed`, `busy`, `no-answer`). |

### Webhook Signature Verification
Incoming requests from Twilio include an `X-Twilio-Signature` header. ElderCare MedTrack validates the HMAC-SHA1 signature using the configured `TWILIO_AUTH_TOKEN` to reject unauthorized or spoofed webhooks.

---

## 7. Running the Production Worker

### Option A: Standalone Background Process (`worker.py`)
Run the independent worker process alongside your web server:
```bash
python worker.py
```
This is ideal for production deployment using `systemd`, Docker, PM2, or Heroku worker dynos. It handles `SIGINT` and `SIGTERM` gracefully.

### Option B: Built-in Background Thread with Flask
When running `python app.py`, the scheduler starts automatically on a background daemon thread (`interval_seconds=30`).

---

## 8. Admin Telephony Dashboard

Access the Telephony Management Dashboard at:
```
http://127.0.0.1:5000/admin/reminders
```
*(Requires Administrator or Pharmacy staff login)*

Features:
- **Live IST Clock**: Displays current `Asia/Kolkata` time and scheduler status.
- **Provider Status Badges**: Shows whether Voice and SMS gateways are LIVE or in Mock Simulator mode.
- **Manual Trigger Tool**: Click **"Scan & Dispatch Due Now"** to test-dispatch due reminders immediately.
- **Handset Diagnostic Tool**: Enter any phone number to trigger a live or simulated test voice call or SMS directly to a handset.
- **Audit Table**: Full persistent log of attempted dispatches, provider SIDs, DTMF digits, and retry counts.
