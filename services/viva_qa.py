"""
Viva Question-Answer Assistant Service (Grounded in Actual Source Code)
Prepares students for external project viva examination with accurate,
defensible answers strictly derived from this codebase and project architecture.

Every answer outputs 4 structured parts:
1. spoken_answer: 2-3 concise, confident English sentences to recite to the examiner.
2. technical_explanation: Deep engineering, algorithms, database logic, and constraints.
3. tanglish_explanation: Simple Tamil + English breakdown for quick understanding and prep.
4. code_reference: Exact file paths, routes, and database tables.
"""

import os
import re
import math

# Comprehensive grounded knowledge repository for Viva Voce examinations
VIVA_KNOWLEDGE_BASE = [
    {
        "id": "project_objective",
        "keywords": ["objective", "goal", "aim", "purpose", "why this project", "overview", "what is this app"],
        "spoken_answer": "The objective of Smart Meds is to help users record medication schedules, dose confirmations, and home-stock estimates, with caregiver and order-status workflows where configured. It does not prescribe, verify ingestion, or automatically dispatch refills.",
        "technical_explanation": "The Flask application stores user-entered regimen and dose-confirmation data, estimates remaining supply, and can display a refill recommendation. A patient, caregiver, or authorized staff member must explicitly submit an order; pharmacy and delivery providers are not configured by default.",
        "tanglish_explanation": "Intha app-la schedule, dose confirmation, stock estimate paarkkalaam. Refill recommendation automatic order illa; patient, caregiver, allathu authorized staff thaniya submit pannanum.",
        "code_reference": "README.md, app.py#L250-L330, database.py#L20-L120"
    },
    {
        "id": "non_smartphone_elderly",
        "keywords": ["without smartphone", "no smartphone", "keypad phone", "basic phone", "smart phone illa", "elderly no phone", "non smartphone", "feature phone"],
        "spoken_answer": "Our system does not require the senior citizen to operate a smartphone. We provide three dedicated solutions: a remote caregiver portal for family members, a pharmacy-assisted telephone ordering workflow, and a high-contrast physical printable timetable card.",
        "technical_explanation": "To achieve 100% digital accessibility for non-smartphone seniors: (1) The Caregiver Portal (/caregiver) exposes records only when the patient has explicitly authorized the caregiver; SMS requires recorded consent and an active provider; (2) The Pharmacy Assisted Order Module (/pharmacy/assisted_order) allows pharmacists to enter orders when seniors call from basic landlines/keypad phones; (3) The Printable Schedule Card (/patient/print_card) generates an analog, high-contrast, color-coded timetable to place on the senior's refrigerator; (4) SMS/voice delivery depends on configured external providers and consent; the app's browser pages are not a substitute for a phone service.",
        "tanglish_explanation": "Periyavanga kitta smartphone illanaalum paravaayilla! (1) Pillaiyanga caregiver portal vazhiyaaga paathukalam; (2) Periyavanga basic keypad phone-la irundhu pharmacy-ku call panni solla pharmacy assisted order poduvom; (3) Refrigerator-la ottikka printable timetable card print eduthu tharuvom.",
        "code_reference": "templates/caregiver.html, templates/pharmacy_assisted_order.html, services/communication_service.py"
    },
    {
        "id": "refill_prediction_algorithm",
        "keywords": ["algorithm", "refill prediction", "predict", "forecast", "burn rate", "calculation", "how stock decreases"],
        "spoken_answer": "The refill predictor estimates remaining supply from the recorded home-stock amount and schedule, then presents an advisory threshold; it does not submit or dispatch an order.",
        "technical_explanation": "Mathematically: Burn Rate (B) = daily_doses (tablets/day). Days Remaining (D) = floor(stock_remaining / B). Predicted Runout Date = current_date + D days. When estimated supply reaches the configured threshold, the app can show a refill recommendation. Orders remain user-initiated and start Pending; external notices require consent and configured providers.",
        "tanglish_explanation": "Oru naalukku ethanai maathirai thevaiyo athan peyarthaan Burn Rate. Mathirai irukkura stock-ai burn rate-al vaguthu ethanai naal varum endru compute seigirom. Threshold-ku keezhe irundhaal app refill recommendation kaattum; order-ai user thaniya submit seiyanum.",
        "code_reference": "services/refill_predictor.py#L20-L75, app.py#L350-L400"
    },
    {
        "id": "technologies_implemented",
        "keywords": ["technologies", "tech stack", "technology", "tools", "languages", "backend", "frontend", "which technologies", "framework", "what technologies"],
        "spoken_answer": "The application uses Python/Flask, SQLite, HTML/CSS/JavaScript, and browser speech APIs; the deployed build has a privacy-conscious PWA shell. External telephony and optional AI require credentials.",
        "technical_explanation": "Comprehensive Technology Architecture: (1) Backend: Python 3.12, Flask; (2) Database: SQLite3 with schema migrations, foreign-key enforcement, and conditional stock updates; the application uses SQLite-specific SQL and a persistent disk is required for Render; (3) Frontend: Accessible HTML5, Tailwind CSS, 3-stage font scaler (A, A+, A++), Tamil bilingual labels; (4) Voice & Audio: Web Speech API (speechSynthesis and webkitSpeechRecognition); (5) AI & Viva Engine: Code-grounded Viva Voce Assistant with Google Gemini API integration; (6) Telephony: Configurable Twilio SMS/Voice gateway with local transparent sandbox simulator.",
        "tanglish_explanation": "Intha project-il use panna technologies: Python Flask backend, 8-table SQLite relational database, responsive Tailwind CSS frontend, kural vazhi kettu theriya Web Speech API, AI external viva assistant, matrum automated SMS communication service.",
        "code_reference": "requirements.txt, app.py, database.py, services/inventory_manager.py, services/viva_qa.py"
    },
    {
        "id": "stock_reservation_overselling",
        "keywords": ["stock reservation", "overselling", "pharmacy stock", "inventory", "atomic", "race condition", "cancel order"],
        "spoken_answer": "We strictly distinguish estimated home stock from actual pharmacy warehouse inventory, and prevent overselling using atomic SQL conditional decrements that safely reserve stock and restore it if an order is cancelled.",
        "technical_explanation": "When an order is created, InventoryManager executes: 'UPDATE medicines SET pharmacy_stock = pharmacy_stock - requested_qty WHERE id = ? AND pharmacy_stock >= requested_qty'. If rowcount is 0, the transaction rolls back, preventing overselling. If an order is cancelled prior to delivery, 'restore_stock_on_cancellation()' executes an atomic increment, restoring units and writing an audit trail in 'caregiver_alerts'. Patient home stock ('stock_remaining') is only incremented when delivery reaches 'Delivered'.",
        "tanglish_explanation": "Veetula irukkura stock vera, Medical shop-la irukkura pharmacy stock vera. Order podum pothu pharmacy stock irukkaa nu paathu atomicaa reserve pannuvom. Oruvelai order cancel aanaal, antha stock thirumba pharmacy-kkey add aagidum. Overselling aagave aagathu.",
        "code_reference": "services/inventory_manager.py#L30-L130, database.py#PRAGMA table_info"
    },
    {
        "id": "prescription_validation",
        "keywords": ["prescription", "rx", "validation", "otc", "doctor", "requires prescription"],
        "spoken_answer": "Medicines in our database are classified as prescription-required or over-the-counter; any order containing Schedule-H medicines is flagged for mandatory pharmacist verification before packaging.",
        "technical_explanation": "The 'medicines' table contains a 'requires_prescription' flag. Orders containing such items are blocked from pharmacy confirmation/processing until a user with the PHARMACY role records a review. The app does not independently validate that user's license or the source document.",
        "tanglish_explanation": "Table-il 'requires_prescription' flag irukku. BP, Heart maathirai ellam doctor prescription thevai. OTC supplements-ku thevai illa. Pharmacy dashboard-il pharmacist verify seitha pinbey packing-ku poga mudiyum.",
        "code_reference": "services/inventory_manager.py#L20-L50, database.py#L320-L345, templates/pharmacy_dashboard.html"
    },
    {
        "id": "system_limitations_truth",
        "keywords": ["limitation", "swallow", "swallowed", "swallowing", "disadvantage", "truth", "sensor", "drawback", "failure", "confirm swallow"],
        "spoken_answer": "The app records patient or caregiver dose confirmations; it cannot physically verify ingestion. Pharmacy licensing, provider delivery, and receipt are also not independently verified by this software.",
        "technical_explanation": "Adherence views reflect recorded reports only. SMS/voice require explicit consent and provider configuration; delivery status is manually recorded and there is no default GPS integration. Do not use the app for medical decisions or emergencies.",
        "tanglish_explanation": "App-la patient allathu caregiver record panna confirmation mattum save aagum; maathirai unmaiyil vizhunginaangala nu verify panna mudiyadhu. GPS/provider delivery default-aa illa.",
        "code_reference": "services/communication_service.py#L20-L60, services/viva_qa.py#L1-L50"
    },
    {
        "id": "database_architecture",
        "keywords": ["database", "schema", "sqlite", "tables", "normalization", "foreign key", "relational"],
        "spoken_answer": "The project uses SQLite tables for patient, medication, prescription, dose, order, notification, caregiver, and vital records. The complete schema contains more than eight tables and evolves through migrations.",
        "technical_explanation": "The database includes patient and staff accounts, medicines, prescriptions, dose logs, orders and line items, notification logs, caregiver alerts, vital readings, and other supporting tables. Pharmacy licensing and provider delivery are not independently verified.",
        "tanglish_explanation": "SQLite database-la patient, medicine, prescription, dose, order, alert, vital records-oda pala tables irukku; schema migrations use panniyirukkom.",
        "code_reference": "database.py#L20-L150"
    },
    {
        "id": "security_and_privacy",
        "keywords": ["security", "sql injection", "privacy", "pin", "auth", "session", "csrf", "safety"],
        "spoken_answer": "The application uses password hashing, server-side authorization checks, CSRF validation for browser mutations, secure cookie settings in production, and parameterized SQL. Deployment still requires a strong secret and operational security review.",
        "technical_explanation": "Security implementations: (1) SQL Injection Prevention: 100% of database interactions use DB-API parameterized queries with placeholder '?'; (2) Authentication: password hashes and signed Flask sessions; (3) Route Enforcement: @app.before_request interceptor blocks unauthenticated access to internal routes; (4) XSS Prevention: Jinja2 auto-escapes all output expressions.",
        "tanglish_explanation": "Security-ku: (1) Parameterized query '?' use pannuvadhal SQL Injection varaadhu; (2) Secret key session vazhiyaaga Login guard panniyirukkom; (3) Jinja2 auto-escaping moolamaaga XSS attack thadukkappadugiradhu.",
        "code_reference": "app.py#L25-L60, database.py"
    },
    {
        "id": "existing_vs_proposed",
        "keywords": ["existing system", "proposed system", "1mg", "pharmeasy", "medisafe", "comparison", "why better"],
        "spoken_answer": "This project provides a responsive interface, optional browser speech support, caregiver workflows, and printable schedules. It does not make verified comparative claims about other products or promise automatic pharmacy dispatch.",
        "technical_explanation": "Features present in this repository include responsive pages, Tamil/English labels, recorded schedules and dose actions, authorized caregiver views, printable schedules, order-status pages, and an in-app SOS record. Live emergency services, pharmacy fulfillment, browser push, and GPS require separate integrations.",
        "tanglish_explanation": "Intha project-oda available features-a mattum explain pannuvom; vera commercial apps pathi unverified comparison panna maattom. Refill order-ai user explicit-aa submit seiyanum.",
        "code_reference": "templates/caregiver.html#MARKET_COMPARISON, templates/index.html"
    },
    {
        "id": "future_scope_iot",
        "keywords": ["future scope", "future enhancement", "iot", "smart pillbox", "hardware", "scale"],
        "spoken_answer": "Potential future work includes validated pharmacy integrations, opt-in browser push, an IoT pillbox, PostgreSQL support, and provider-backed delivery tracking; these are not implemented by default.",
        "technical_explanation": "Planned architectural enhancements: (1) IoT Hardware Integration: An ESP32 micro-controller pillbox equipped with HX711 strain-gauge load cells to detect when a pill is physically lifted from the slot; (2) Telephony IVR Server: Full Asterisk/Twilio IVR tree allowing an elderly patient to press '1' on any landline to record dose; (3) Multi-pharmacy bidding to secure lowest medicine prices across local chemists.",
        "tanglish_explanation": "Edhirkaala valarchi (Future Scope): (1) Smart IoT Pillbox hardware connection — weight sensor moolamaaga maathirai eduthathaaga verify seivadhu; (2) Keypad phone-il '1' amukkinaal dose record aagum Interactive Voice Response (IVR) system.",
        "code_reference": "services/communication_service.py, README.md#Future_Scope"
    }
]

COMMON_STOPWORDS = {
    "how", "what", "which", "where", "when", "why", "who", "whom",
    "is", "are", "was", "were", "be", "been", "being",
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "does", "do", "did", "can", "could", "will", "would", "shall", "should",
    "this", "that", "these", "those", "it", "its", "my", "your", "our", "their",
    "about", "with", "from", "by", "as", "into", "through"
}

class VivaAssistant:
    @staticmethod
    def answer_question(user_query):
        """
        Processes a user question and generates an examiner-ready 4-part response.
        Uses exact keyword matching and semantic scoring against verified project facts.
        """
        clean_query = user_query.lower().strip()
        tokens = set(re.findall(r'\b\w+\b', clean_query))
        meaningful_query_tokens = tokens - COMMON_STOPWORDS

        best_match = None
        best_score = 0

        for entry in VIVA_KNOWLEDGE_BASE:
            score = 0
            for kw in entry["keywords"]:
                kw_lower = kw.lower().strip()
                kw_tokens = set(re.findall(r'\b\w+\b', kw_lower))
                meaningful_kw_tokens = kw_tokens - COMMON_STOPWORDS

                # Exact phrase match in query (for multi-word keywords)
                if len(kw_lower) > 3 and kw_lower in clean_query:
                    score += 6

                # Meaningful token intersection
                overlap = len(meaningful_query_tokens.intersection(meaningful_kw_tokens))
                score += overlap * 3

            if score > best_score:
                best_score = score
                best_match = entry

        # Require a threshold score of >= 3 to avoid spurious matches on stopwords
        if best_match and best_score >= 3:
            return {
                "question": user_query,
                "confidence": "HIGH",
                "matched_topic": best_match["id"],
                "spoken_answer": best_match["spoken_answer"],
                "technical_explanation": best_match["technical_explanation"],
                "tanglish_explanation": best_match["tanglish_explanation"],
                "code_reference": best_match["code_reference"]
            }

        # Optional external expansion is restricted to technical project questions;
        # never forward likely identifiers or personal/clinical questions.
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key and os.getenv("VIVA_EXTERNAL_AI_ENABLED", "").lower() in {"1", "true", "yes"} and VivaAssistant._safe_project_question(user_query):
            try:
                dynamic_resp = VivaAssistant._query_gemini(user_query, gemini_key)
                if dynamic_resp:
                    return dynamic_resp
            except Exception:
                pass

        # Intelligent fallback for unmatched queries
        return {
            "question": user_query,
            "confidence": "FALLBACK",
            "matched_topic": "general_architecture",
            "spoken_answer": f"This question concerns the Smart Meds Flask application. I can describe only the features verified in this repository; consult the source or project audit for implementation details.",
            "technical_explanation": f"Regarding '{user_query}': The project uses Flask and SQLite with role/ownership checks, recorded schedules and orders, and optional consent-gated communications. Low-stock is advisory; an order requires an explicit user action. PostgreSQL, browser push, live GPS, and verified pharmacy fulfillment are not configured.",
            "tanglish_explanation": f"Intha kelvikku source code-la verify panna mudinja features mattum solla mudiyum. Low-stock recommendation automatic order illa; user submit seiyanum. Live GPS, browser push, PostgreSQL support default-aa illa.",
            "code_reference": "app.py, database.py, services/refill_predictor.py, services/inventory_manager.py"
        }

    @staticmethod
    def _safe_project_question(question):
        text = str(question or "").strip()
        if not text or len(text) > 300:
            return False
        if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b\+?\d[\d\s().-]{8,}\b", text):
            return False
        lower = text.lower()
        restricted = ("my health", "my diagnosis", "my medicine", "my medication", "my dosage", "prescribe", "dose for me", "patient name", "medical record")
        if any(term in lower for term in restricted):
            return False
        project_terms = ("flask", "python", "database", "sqlite", "postgres", "route", "api", "csrf", "session", "security", "architecture", "algorithm", "scheduler", "reminder", "inventory", "order", "caregiver", "project", "code", "module", "deployment", "render", "test")
        return any(term in lower for term in project_terms)

    @staticmethod
    def _query_gemini(user_query, api_key):
        """
        Queries Gemini API if configured, strictly enforcing 4-part project-grounded output.
        """
        import urllib.request
        import json

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        prompt = f"""
You are the Viva Voce Examination Assistant for the project "Smart Meds / ElderCare MedTrack".
Answer this viva question strictly grounded in the project codebase:
Question: {user_query}

PROJECT CONTEXT:
- Tech Stack: Python/Flask, SQLite-specific database layer, HTML/CSS/JavaScript, optional browser speech, and an installable PWA shell.
- Features: authorized caregiver workflows, pharmacy-assisted order entry, printable schedules, recorded dose status, advisory stock estimates, and status-based order management.
- Orders: user/staff submitted, start Pending, and require configured catalog data; no automatic refill dispatch, pharmacy partnership, GPS, or ETA is assumed.
- Pharmacy: conditional inventory reservation and restoration are implemented; Rx review is staff-recorded and licenses/source prescriptions are not independently validated.
- Privacy: recorded dose confirmations do not prove ingestion; reminders require explicit consent and provider configuration; do not include patient information in answers.

Format your response strictly as valid JSON with these 4 keys:
{{
  "spoken_answer": "2-3 short confident sentences to say aloud to the examiner",
  "technical_explanation": "Detailed engineering, architecture, queries, algorithms",
  "tanglish_explanation": "Simple Tamil + English conversational explanation for student",
  "code_reference": "File paths, line numbers, routes"
}}
Return only the raw JSON.
"""
        req_data = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        req = urllib.request.Request(url, data=json.dumps(req_data).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as response:
            res_json = json.loads(response.read().decode("utf-8"))
            candidate = res_json["candidates"][0]["content"]["parts"][0]["text"]
            # Clean markdown codeblocks
            clean_json = re.sub(r'```json\s*|\s*```', '', candidate).strip()
            parsed = json.loads(clean_json)
            expected = {"spoken_answer", "technical_explanation", "tanglish_explanation", "code_reference"}
            if not isinstance(parsed, dict) or not expected.issubset(parsed):
                return None
            if any(not isinstance(parsed[key], str) or len(parsed[key]) > 5000 for key in expected):
                return None
            parsed["question"] = user_query
            parsed["confidence"] = "AI_GROUNDED"
            return parsed
