# Smart Meds / ElderCare MedTrack

An existing Flask web application for medication schedules, user-reported dose tracking, home-stock estimates, caregiver access, pharmacy order workflows, and order-status records. It is intended as a software project and operational prototype; it is **not** a clinical decision system, pharmacy marketplace, emergency dispatch service, or verified delivery network.

## Current implementation and boundaries

The project includes patient and staff authentication, password hashing, role-based route checks, CSRF validation for browser mutations, explicit patient/caregiver relationship checks, medication and prescription records, reminder scheduling, consent-aware communications, order and pharmacy dashboards, cancellation, order-status history, health-vital records, Tamil/English interface content, voice/read-aloud features, an optional Viva assistant, and a PWA install shell.

Important boundaries:

- Dose records represent patient/caregiver reports; the app cannot verify that a medicine was swallowed.
- Low-stock predictions are advisory. An order is created only after a user or authorized staff member submits it.
- New databases do not include sample patient records, stock, pharmacy credentials, or demo login passwords. A production catalog/pharmacy must be configured with verified data.
- Pharmacy and prescription review actions are staff-recorded. The app does not independently verify pharmacy licenses or clinical source documents.
- Order status is recorded by staff. Live courier GPS, delivery-provider integration, confirmed ETAs, payment settlement, and physical receipt verification are not configured by default.
- SMS/voice delivery requires patient consent and correctly configured provider credentials. Browser push notifications are not implemented. The service worker does not cache authenticated pages or medical records.
- The application currently uses SQLite-specific database APIs and SQL. Render deployment below uses a persistent disk and one web instance; PostgreSQL support and horizontal scaling are not implemented.
- Do not use the app for emergency triage, treatment decisions, or urgent medical advice. Contact a clinician or local emergency services as appropriate.

## Requirements

- Python 3.10+ (Python 3.12 recommended)
- pip
- SQLite (included with supported Python builds)
- For production: a WSGI-capable host, HTTPS, a persistent writable disk, and a strong `SECRET_KEY`

Install dependencies:

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create local configuration:

```bash
cp .env.example .env       # Windows PowerShell: Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `.env` as `SECRET_KEY=...`. Do not paste secrets into source control, logs, screenshots, or support messages. Review `.env.example` for optional integrations; leave provider credentials empty until deliberately configured.

Initialize the schema and start the development server:

```bash
python scripts/migrate_db.py
python run_web.py
```

Open `http://127.0.0.1:5000`. For local development only, set `DATABASE_PATH=./instance/smart_meds.db`; create the parent folder first. Do not expose the Flask development server to the public internet. `FLASK_DEBUG` must remain false outside a disposable local environment.

### Windows notes

From PowerShell, use `py -3.12 -m venv .venv`, activate with `.venv\Scripts\Activate.ps1`, install requirements, copy `.env.example` to `.env`, edit the secret and database path, run `python scripts\migrate_db.py`, then run `python run_web.py`. If script activation is blocked by policy, use the Windows Python launcher directly as appropriate rather than weakening machine-wide execution policy. Keep the SQLite file out of OneDrive/shared folders and restrict filesystem access because it contains sensitive health data.

### Create the first staff account

After configuring `.env` and running migrations, use a trusted terminal:

```bash
python scripts/bootstrap_user.py
```

The CLI creates only ADMIN, PHARMACY, or DELIVERY accounts, hashes passwords, and does not echo the password. Use unique passwords of at least 12 characters. Patient registration is self-service; caregiver registration requires a one-time invitation issued by an authorized patient. Do not enable demo seed mode in production.

## Tests and database tools

Run the isolated unit/security regression tests:

```bash
python -m unittest discover -s tests -v
```

The suite uses temporary databases and should not run against a production patient database. The automated tests cover core security and selected safety properties; they are not a substitute for full clinical, accessibility, penetration, provider, or device testing.

Run migrations at any time with `python scripts/migrate_db.py`. Schema initialization uses idempotent SQLite migrations in `database.py` and reports only the schema version.

Create a consistent manual SQLite backup:

```bash
python scripts/backup_db.py
# optional: set BACKUP_DIR to an access-controlled destination
```

The output contains personal and health information. Encrypt it, restrict access, store it off-host, and test restoration procedures. Before restoring, stop the application and scheduler; restore the database and its permissions to the configured `DATABASE_PATH`, then run migrations. A Render disk is persistent storage, not a backup service.

## Reminder worker and notification integrations

The scheduled reminder loop runs in a single background thread in the one Gunicorn web worker when `RUN_REMINDER_WORKER=true`. This topology avoids two processes racing over the same SQLite file. **Do not also run `worker.py` or a separate Render worker against the same SQLite database.** `worker.py` is for an alternative persistent host that uses one worker process and the same durable database path; do not launch it alongside the web scheduler.

Reminder dispatch is gated by explicit consent and per-prescription/account preferences. Provider credentials are optional and unset by default. Configure the chosen provider and callback URLs through environment variables and test with non-patient test numbers before enabling production sends. Inbound Twilio webhooks reject missing or invalid signatures when Twilio credentials are configured. Never use real patient data in provider test accounts.

Browser notifications are not implemented; the PWA intentionally does not request notification permission. Scheduled background SMS/voice reminders are separate and require a live provider. Browser speech synthesis/recognition support depends on the device/browser and may not provide Tamil in every environment.

## Optional Viva assistant

The in-repository Viva answers are a deterministic educational feature. Optional Gemini expansion is disabled by default (`VIVA_EXTERNAL_AI_ENABLED=false`). If enabled, only short, non-sensitive project-engineering questions should be forwarded; confidential patient or medication records must never be sent. The assistant must not prescribe, change doses, diagnose, or replace a clinician. External AI availability and output quality depend on credentials and provider service.

## PWA installation

The web app includes a manifest, generated Smart Meds icon, a root-scoped service worker, an install prompt where supported, and a static offline notice. The service worker caches only explicitly allowlisted public shell assets. Authenticated HTML pages, API responses, and patient records always require a network connection and are never cached for offline access. Serve over HTTPS (or localhost) for service-worker support. Android installation behavior has not been device-tested in this environment; browser and OS support vary.

## Render deployment

A `render.yaml` Blueprint defines one Python web service, `/healthz`, a persistent disk mounted at `/var/data`, generated `SECRET_KEY`, SQLite at `/var/data/medcare.db`, demo seeding disabled, and the in-process reminder scheduler. The Blueprint is a deployment configuration; this project has **not** been deployed or provider-tested from this environment.

1. Create a private GitHub repository and upload the clean source tree. Confirm that `.env`, database files, backup archives, and generated test data are not tracked.
2. In Render, create a Blueprint instance from that repository and review the plan/disk size and current pricing before confirming. Render persistent disks may incur charges and are available only on supported plans.
3. Verify `SECRET_KEY` is generated and persistent. Keep `FLASK_DEBUG=false`, `SMART_MEDS_DEMO_SEED=false`, and Inbound Twilio webhooks reject missing or invalid signatures; keep the Auth Token private and configure the exact public callback URLs.
4. Confirm the disk mount is `/var/data` and the database is `/var/data/medcare.db`. Use one service instance/one Gunicorn worker with the scheduler thread; do not scale horizontally with this SQLite architecture.
5. After deploy, test `/healthz`, login, CSRF behavior, ownership restrictions, caregiver invitations, order lifecycle, worker logs, and database persistence using synthetic accounts only.
6. Create the initial staff account through a protected one-off shell using `python scripts/bootstrap_user.py` if the hosting environment permits. Otherwise use an approved secure administrative provisioning procedure. Never add a default password or create an account in a public startup route.
7. Configure and separately test SMS/voice/AI providers only after consent, callbacks, and data handling have been reviewed. No live-provider integration is assumed by the code.
8. Set up encrypted off-host backups and a restore drill. A persistent Render disk protects against ordinary instance replacement but does not protect against deletion, corruption, or account loss.

`render.yaml` currently does not configure PostgreSQL. SQLite is selected for a single instance with persistent storage. Before using multiple instances or requiring stronger database operations, implement and test a PostgreSQL adapter/migrations and move session/worker coordination off SQLite.

## GitHub upload and secret hygiene

Before the first commit:

```bash
# Check tracked files and ignored secrets
 git status --short
 git check-ignore .env medcare.db instance/smart_meds.db
```

Create a private repository, then from this project directory:

```bash
git init
git add .
git status --short
git commit -m "Prepare Smart Meds for secure deployment"
git branch -M main
git remote add origin <your-private-repository-url>
git push -u origin main
```

Review `git status` and the staged diff before every push. If a secret was ever committed, deleting it in a later commit is insufficient: revoke/rotate it and remove it from repository history. Never upload database files or backups containing personal information.

## Audit and delivered changes

- `docs/PHASE1_AUDIT.md` contains the pre-change source audit.
- `docs/FINAL_READINESS.md`, `docs/FINAL_SECURITY_REVIEW.md`, and `docs/TEST_RESULTS.txt` record implementation status, residual risks, and executed checks.
- Authentication, role authorization, CSRF, session cookies, default-deny routing, patient/caregiver ownership, login throttling, input validation, consent gates, reminder callback idempotency, inventory restoration, and delivery status checks were hardened in the existing application.
- Added secure caregiver invitation/revocation flow, staff bootstrap CLI, failure-delivery transition, PWA shell assets, migration/backup commands, Render/Gunicorn configuration, and regression tests.
- The source archive is not a certification of regulatory compliance, clinical safety, third-party provider verification, or a completed production deployment. Complete a privacy/security review and formal UAT before storing real patient data.
