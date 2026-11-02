# Smart Meds / ElderCare MedTrack — Phase 1 Source Audit

**Audit date:** 2026-11-01  
**Scope:** Uploaded `EBI.zip`, active Flask application, database module/schema metadata, service modules, worker, templates/static files, project docs/config, and test-source structure. This is the required pre-change audit. No application source was modified before this report was created.

## Executive assessment

The project is a substantial Flask/SQLite application with medicine cataloguing, prescriptions, dose logging, refill forecasting, inventory/order workflows, caregiver and pharmacy interfaces, vitals, bilingual/voice features, telephony/WhatsApp adapters, and an optional Gemini-backed Viva assistant. The current implementation is **not safe for public deployment or real patient data**. Critical authorization and authentication defects allow role elevation and public access to sensitive patient, order, and health data. CSRF tokens are created but not validated. Production secret handling, deployment infrastructure, isolated tests, and PWA files are absent or incomplete.

**Release recommendation: DO NOT DEPLOY until the critical/high findings are corrected and the acceptance tests are repeatable against a disposable database.** The supplied SQLite files and `.env` are treated as sensitive artifacts; their record/secret contents were not emitted or included in this report.

## Project inventory and initial checks

- Main application: `app.py` (~118 KB); database: `database.py` (~64 KB); services include inventory, refill prediction, reminder scheduler, communications and Viva Q&A.
- Active UI includes patient, caregiver, pharmacy, order tracking, prescriptions, vitals, SOS, admin reminders, and Viva templates, plus shared responsive CSS/JS.
- Historical/duplicate copies exist in `backup_archive/` and `templates_backup/`. The active and archived implementations diverge; the active tree should remain authoritative and archives immutable.
- Two local SQLite databases are present (active and archived). Active DB schema metadata lists 13 tables: `patients`, `medicines`, `prescriptions`, `dose_logs`, `refill_orders`, `caregiver_alerts`, `order_items`, `pharmacy_stores`, `communication_logs`, `users`, `order_status_history`, `reminder_dispatches`, and `health_vitals`. Only table counts/schema metadata were inspected; no rows were read.
- Root `.env` is present in the uploaded archive. Values were not read or reported. It must not ship in a deployment ZIP/repository. Database files and SQLite sidecars must also be excluded from any shareable deployment artifact unless confirmed sanitized and intentionally needed.
- All Python files passed `ast.parse` syntax validation in the initial static check. This does not establish import/runtime correctness.
- Existing tests are primarily `scratch/` scripts. There are 12 `test_*.py` files and at least 53 functions named `test_*`; several scripts mutate the configured application DB. They were not run during audit to avoid altering the supplied database.
- Current `requirements.txt` declares Flask, python-dotenv and Twilio only. At audit time, Flask was installed in the sandbox; dotenv, Twilio, pytest, psycopg and Gunicorn were not. `requests` is imported by a service but is not declared directly.
- No active PWA manifest, service worker, or app icon was found. Responsive layout and browser speech synthesis are present. External CDN assets mean offline use is not established.
- No Render manifest/Procfile/Gunicorn config, PostgreSQL backend, migration framework, CI configuration, or root ignore rules were found.

## Existing functionality observed

The application has meaningful implementations, which should be preserved and secured rather than replaced wholesale:

- Patient/caregiver registration and sign-in pages; session-based role state.
- Medicine catalog and prescription views/CRUD; stock tracking and dose logs.
- Refill prediction and medicine ordering; pharmacy order/inventory dashboard and assisted ordering; order status history and tracking views.
- Caregiver portal, SOS, health vitals, printable medication card, Tamil/English interface elements, and browser speech features.
- Reminder scheduler service and standalone `worker.py`; SMS/voice/WhatsApp communication paths with mock/optional live-provider behavior; Twilio callbacks/DTMF handling.
- Optional Gemini integration in the Viva assistant.
- Positive existing practices include widespread SQL parameter binding, a transactional conditional stock reservation query, selected explicit role checks, and a README disclaimer that dose adherence is self/caregiver-reported rather than physical proof of ingestion.

Static presence or README claims do not prove these modules are complete or production-tested.

## Critical findings — release blockers

1. **Unauthenticated role elevation:** `app.py:58–70, 217–254` exempts `/switch_role/<role>` from login and lets a GET request write `ADMIN`, `PHARMACY`, `CAREGIVER`, or `PATIENT` into the session. This is direct privilege escalation.
2. **Public health/patient-data access and mutation:** the global allowlist exposes caregiver, vitals, voice summary and rider-location endpoints (`app.py:53–70`). The caregiver route can show fallback patient data (`:1875–1913`); vitals endpoints accept supplied patient IDs and permit writes/deletes (`:2015–2031, 2096–2143, 2213–2250`); voice summary reveals regimen-related information (`:2271–2323`); rider-location data includes order/delivery/patient details (`:1395–1459`). Authorization must be enforced per handler and record, not merely at the UI layer.
3. **Session forgery risk:** `app.py:28–30` uses a predictable hard-coded fallback `SECRET_KEY` when environment configuration is absent. Flask signed sessions can be forged when that key is known.
4. **Weak/PIN authentication and plaintext storage:** patient sign-in accepts a blank or common PIN fallback for a matching phone (`app.py:193–196`); user login contains a fixed-PIN legacy fallback (`:168–175`). Registration stores a hash but also persists the PIN to the patient record (`:285–300`). Schema/seed defaults include a plaintext PIN (`database.py:24–43`). README/sign-in template also contain demo-login behavior (`README.md:184–198`, `templates/signin.html:125–164`).
5. **CSRF is not enforced:** a token is generated and passed to templates (`app.py:128–140`), but no request-time validation or active global CSRF middleware was found. Forms do not consistently submit tokens. State-changing GETs include role switch and logout.
6. **Inadequate object-level access checks:** prescription/stock/order actions use submitted IDs without consistent ownership filtering. Examples: stock update/delete (`app.py:832–858`), refill prescription selection (`:1067–1076`), order placement target patient (`:870–897`), and WhatsApp order action (`:1620–1673`). Caregiver linkage checks exist only in selected paths.
7. **Pharmacy data exposure:** `/pharmacy` returns all orders/contact information and inventory (`app.py:2326–2352`) but does not enforce pharmacy/admin role at the route. Any authenticated role can reach it; the public switcher makes this readily exploitable.
8. **Sensitive records and live integrations:** communication logs store recipient identifiers and full message bodies; notification messages may contain medicine, dosage, schedule, address, or order details. Limits, consent, retention, encryption, and access auditing require production review.

## High-priority findings

- `app.py:2655–2665` starts Flask with `debug=True`; production should use Gunicorn or equivalent, with safe proxy/TLS/session-cookie settings and no debugger.
- Webhook authentication is inconsistent: gather callback checks Twilio signature only when present, while status callback lacks verification; TwiML endpoint exposes specific reminder text from predictable IDs. DTMF actions can be replayed to create repeated dose records/stock deductions (`services/scheduler_service.py:261–293`). Require signatures and idempotent one-time state transitions.
- Scheduler records snooze/retry dispatches, but polling is based on configured prescription schedule and does not consume pending retry rows (`services/scheduler_service.py:83–120, 322–345, 385–415`). Failure handling can leave pending rows that prevent recovery. Thus background reminders exist, but retry/snooze end-to-end behavior is not verified.
- Inventory reservation trusts quantities without requiring positive bounded values (`services/inventory_manager.py:62–84`); negative quantity can increase stock. Delivery restocking is not guarded against duplicate invocation (`:172–213`). Cancellation/restoration needs atomic conditional order-state transitions and rollback coverage.
- SQLite FK declarations are not enforced on every connection: `database.py:11–15` lacks `PRAGMA foreign_keys=ON`; several relations/constraints are missing. PostgreSQL compatibility is not implemented: source uses `sqlite3`, PRAGMA/user_version, SQLite SQL, `?` placeholders and `lastrowid`.
- Notification consent defaults/backfills may enable communication without explicit auditable opt-in (`database.py:191–199, 367–375, 443–473`). Explicit channel-specific consent is needed.
- Provider reporting can be inaccurate: Fast2SMS configuration is detected but no live dispatch implementation was found in the inspected path (`services/communication_service.py:94–107, 427–448`); WhatsApp result code reports success unconditionally in one path (`:679–718`). Mocked, queued, failed and confirmed-delivered results must remain distinct.
- Gemini Viva Q&A uses static project context and generic fallback rather than live verified sources (`services/viva_qa.py:16–105, 150–225`). Its key appears in request URL query construction (`:184–217`) and errors are swallowed. Do not submit patient data; redact keys from URLs/logs; label as educational, not clinical or prescribing advice.
- Input coercions can raise uncaught exceptions, and direct order failure paths may expose exception details to users. Add bounded validation and generic errors.

## Medium findings / delivery gaps

- Current dependencies are incomplete/unpinned for the deployment path; no production WSGI server, PostgreSQL driver, migration tooling, or CI setup is declared.
- SQLite DB path is fixed beside the source. This is not appropriate for Render ephemeral filesystems or horizontally scaled instances.
- Current tests are scratch scripts that may mutate the source DB and do not establish current pass/fail status. README claims of “100% passing” are not independently substantiated and should not be repeated without a clean, isolated run.
- README includes demo credentials and claims more security/offline behavior than source proves. README should be rewritten for the secured behavior and clearly distinguish mock providers from credentials-backed integrations.
- Multiple historical code/template copies can be mistaken for the active source. Preserve them as historical backups, but exclude unnecessary archive copies from deployment.
- Browser reminders/notifications cannot be guaranteed when a browser is closed; actual delivery requires a server worker and configured provider. Browser notification permission and PWA background behavior need device/browser tests before being claimed.

## External provider and device limitations

- Twilio SMS, voice, DTMF callbacks, and WhatsApp cannot be verified without live credentials, compliant webhook URLs, provider setup and a real callback test.
- Fast2SMS is configured by name but a live implementation was not confirmed.
- Gemini calls cannot be verified without an API credential; content safety/source provenance needs independent validation.
- Android installation, background notifications and Tamil speech vary by browser/device and require real-device tests. No native APK is present or claimed.
- PostgreSQL migration and Render deployment cannot be declared successful without configured services and an actual deployment run.

## Audit positive controls

- Python syntax parsing succeeded for all Python files checked.
- Parameterized SQL is used in many app/database paths.
- Inventory reservation uses a conditional stock update and transactional design in its primary path.
- New account code invokes Werkzeug password hashing, although other paths still retain plaintext/fallback credentials.
- UI includes responsive viewport/layout elements and browser speech synthesis; these are useful foundations to preserve.

## Prioritized remediation sequence

1. Remove public role-switch functionality and demo credential bypasses; require an environment session secret.
2. Enforce authentication, role checks, caregiver-to-patient linkage, and object-level ownership on every HTML/API operation; default deny sensitive paths.
3. Add real CSRF verification for browser mutations and keep signed provider webhooks on a separate strict signature-validation path.
4. Fix password/PIN storage and defaults, explicit consent, input validation, response/error handling, and sensitive log minimization.
5. Correct idempotency and quantity/state validation for reminder callbacks and inventory/order transitions.
6. Isolate and make tests repeatable using a temporary database; report actual test outcomes only.
7. Add production configuration, Render/Gunicorn/worker setup, database migrations and a documented PostgreSQL choice; exclude secrets/data artifacts from deployment ZIP.
8. Implement/verify optional provider integration and PWA behavior only to the extent credentials/devices permit.

**This report records the pre-change state. Any remediation should be documented separately and validated against the audit findings.**
