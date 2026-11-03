"""SQLite schema, migrations, and optional demo medicine catalog."""

import os
import sqlite3


def get_db_path():
    raw_path = os.getenv("DATABASE_PATH")
    if not raw_path:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "medcare.db")
    
    if not os.path.isabs(raw_path):
        resolved = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), raw_path))
    else:
        resolved = raw_path

    # Auto-create directory if missing, with graceful fallback on permission issues
    parent_dir = os.path.dirname(os.path.abspath(resolved))
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except (PermissionError, OSError):
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "medcare.db")
    return resolved


DB_PATH = get_db_path()


def get_db_connection():
    db_path = get_db_path()
    parent_dir = os.path.dirname(os.path.abspath(db_path))
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except (PermissionError, OSError):
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medcare.db")
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
    except sqlite3.OperationalError:
        # Fallback to local file in project directory if custom directory fails to open
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medcare.db")
        conn = sqlite3.connect(db_path, timeout=30.0)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_column(conn, table, column, definition):
    columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(conn):
    """Create missing tables and apply idempotent SQLite schema upgrades."""
    previous_version = conn.execute("PRAGMA user_version").fetchone()[0]
    schema = (
        """
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT,
            phone TEXT UNIQUE NOT NULL,
            pin TEXT DEFAULT NULL,
            email TEXT,
            address TEXT NOT NULL,
            blood_group TEXT,
            medical_conditions TEXT,
            allergies TEXT,
            mobility_status TEXT,
            caregiver_name TEXT,
            caregiver_phone TEXT,
            caregiver_relation TEXT,
            doctor_name TEXT,
            doctor_phone TEXT,
            preferred_language TEXT DEFAULT 'en',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            generic_name TEXT,
            category TEXT,
            letter TEXT NOT NULL,
            default_dosage TEXT,
            form TEXT,
            timing_advice TEXT,
            description TEXT,
            therapeutic_use TEXT,
            precautions TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            medicine_id INTEGER,
            medicine_name TEXT NOT NULL,
            dosage TEXT NOT NULL,
            timing TEXT NOT NULL,
            timing_advice TEXT,
            stock_remaining INTEGER NOT NULL DEFAULT 30,
            daily_doses INTEGER NOT NULL DEFAULT 1,
            refill_threshold INTEGER NOT NULL DEFAULT 5,
            doctor_notes TEXT,
            color_code TEXT DEFAULT '#3B82F6',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id),
            FOREIGN KEY (medicine_id) REFERENCES medicines(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dose_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prescription_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,
            dose_time TEXT NOT NULL,
            scheduled_for DATE NOT NULL,
            status TEXT NOT NULL,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS refill_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            order_number TEXT UNIQUE NOT NULL,
            items_summary TEXT NOT NULL,
            total_pills INTEGER NOT NULL,
            total_amount REAL NOT NULL,
            delivery_address TEXT NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'Cash on Delivery (Doorstep)',
            status TEXT NOT NULL DEFAULT 'Confirmed',
            pharmacy_name TEXT,
            delivery_rider TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            delivered_at TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS caregiver_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'Unread',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_hash TEXT NOT NULL,
            attempted_at INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL,
            patient_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            medicine_id INTEGER,
            medicine_name TEXT NOT NULL,
            dosage TEXT,
            quantity INTEGER NOT NULL DEFAULT 30,
            unit_price REAL NOT NULL DEFAULT 0.0,
            subtotal REAL NOT NULL DEFAULT 0.0,
            requires_prescription INTEGER DEFAULT 0,
            FOREIGN KEY (order_id) REFERENCES refill_orders(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pharmacy_stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            license_number TEXT,
            phone TEXT,
            mobile TEXT,
            email TEXT,
            address TEXT,
            pharmacist_name TEXT,
            operating_hours TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS communication_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            recipient_role TEXT NOT NULL,
            recipient_phone TEXT NOT NULL,
            recipient_name TEXT,
            channel TEXT NOT NULL,
            subject TEXT,
            message_body TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'MOCK_SIMULATOR',
            status TEXT NOT NULL DEFAULT 'DELIVERED',
            simulated INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            order_id INTEGER,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            updated_by TEXT DEFAULT 'System',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES refill_orders(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reminder_dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prescription_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            scheduled_date DATE NOT NULL,
            scheduled_time TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            provider TEXT NOT NULL DEFAULT 'MOCK_SIMULATOR',
            provider_sid TEXT,
            provider_status TEXT,
            dtmf_response TEXT,
            retry_count INTEGER DEFAULT 0,
            locked_until TIMESTAMP,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (prescription_id) REFERENCES prescriptions(id),
            FOREIGN KEY (patient_id) REFERENCES patients(id),
            UNIQUE (prescription_id, scheduled_date, scheduled_time, channel)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS health_vitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            vital_type TEXT NOT NULL,
            systolic INTEGER,
            diastolic INTEGER,
            pulse_bpm INTEGER,
            sugar_value REAL,
            sugar_meal_type TEXT,
            status TEXT NOT NULL DEFAULT 'NORMAL',
            notes TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        )
        """,
    )
    for statement in schema:
        conn.execute(statement)

    columns = {
        "patients": (
            ("consent_recorded", "INTEGER DEFAULT 0"),
            ("caregiver_authorized", "INTEGER DEFAULT 0"),
            ("password_hash", "TEXT"),
            ("sms_notifications_enabled", "INTEGER DEFAULT 0"),
            ("voice_notifications_enabled", "INTEGER DEFAULT 0"),
            ("refill_reminders_enabled", "INTEGER DEFAULT 0"),
            ("timezone", "TEXT DEFAULT 'Asia/Kolkata'"),
            ("reminder_consent", "INTEGER DEFAULT 0"),
            ("quiet_hours_start", "TEXT DEFAULT '22:00'"),
            ("quiet_hours_end", "TEXT DEFAULT '06:00'"),
            ("caregiver_invite_hash", "TEXT"),
            ("caregiver_invite_expires_at", "INTEGER"),
        ),
        "medicines": (
            ("price", "REAL DEFAULT NULL"),
            ("pharmacy_stock", "INTEGER DEFAULT 0"),
            ("requires_prescription", "INTEGER DEFAULT 1"),
        ),
        "prescriptions": (
            ("start_date", "DATE"),
            ("burn_rate", "REAL DEFAULT 1.0"),
            ("schedule_time", "TEXT DEFAULT '08:00'"),
            ("timezone", "TEXT DEFAULT 'Asia/Kolkata'"),
            ("reminder_consent", "INTEGER DEFAULT 0"),
            ("sms_reminder_enabled", "INTEGER DEFAULT 0"),
            ("voice_reminder_enabled", "INTEGER DEFAULT 0"),
        ),
        "dose_logs": (("reason", "TEXT"),),
        "refill_orders": (
            ("order_source", "TEXT DEFAULT 'PATIENT_APP'"),
            ("prescription_verified", "INTEGER DEFAULT 0"),
            ("is_cancelled", "INTEGER DEFAULT 0"),
            ("cancellation_reason", "TEXT"),
            ("prescription_image", "TEXT"),
            ("created_by_staff", "TEXT DEFAULT 'Self / App'"),
            ("idempotency_key", "TEXT"),
            ("contact_phone", "TEXT"),
            ("whatsapp_notified", "INTEGER DEFAULT 0"),
            ("rider_phone", "TEXT"),
            ("rider_vehicle", "TEXT"),
            ("rider_lat", "REAL"),
            ("rider_lng", "REAL"),
            ("delivery_restocked", "INTEGER NOT NULL DEFAULT 0"),
        ),
        "communication_logs": (("order_id", "INTEGER"),),
    }
    for table, table_columns in columns.items():
        for column, definition in table_columns:
            _ensure_column(conn, table, column, definition)

    if previous_version < 5:
        conn.execute(
            "UPDATE prescriptions SET start_date = date('now', '-25 days') "
            "WHERE start_date IS NULL"
        )
        conn.execute(
            "UPDATE prescriptions SET schedule_time = '08:00' "
            "WHERE timing LIKE '%Morning%' "
            "AND (schedule_time IS NULL OR schedule_time = '08:00')"
        )
        conn.execute(
            "UPDATE prescriptions SET schedule_time = '13:00' "
            "WHERE timing LIKE '%Afternoon%'"
        )
        conn.execute(
            "UPDATE prescriptions SET schedule_time = '18:00' "
            "WHERE timing LIKE '%Evening%'"
        )
        conn.execute(
            "UPDATE prescriptions SET schedule_time = '21:00' "
            "WHERE timing LIKE '%Night%'"
        )
        conn.execute(
            "UPDATE prescriptions SET timezone = 'Asia/Kolkata' "
            "WHERE timezone IS NULL"
        )
        conn.execute(
            "UPDATE patients SET timezone = 'Asia/Kolkata' "
            "WHERE timezone IS NULL"
        )
        conn.execute(
            "UPDATE patients SET quiet_hours_start = '22:00', "
            "quiet_hours_end = '06:00' WHERE quiet_hours_start IS NULL"
        )
    if previous_version < 8:
        conn.execute(
            "UPDATE patients SET consent_recorded = 0, caregiver_authorized = 0, "
            "reminder_consent = 0, sms_notifications_enabled = 0, "
            "voice_notifications_enabled = 0, refill_reminders_enabled = 0"
        )
        conn.execute(
            "UPDATE prescriptions SET reminder_consent = 0, "
            "sms_reminder_enabled = 0, voice_reminder_enabled = 0"
        )

    conn.execute(
        "UPDATE refill_orders SET contact_phone = "
        "(SELECT phone FROM patients WHERE patients.id = refill_orders.patient_id) "
        "WHERE contact_phone IS NULL OR contact_phone = ''"
    )
    conn.execute(
        "INSERT INTO order_status_history "
        "(order_id, status, notes, updated_by, created_at) "
        "SELECT orders.id, orders.status, 'Existing order status imported.', "
        "'Database migration', orders.created_at FROM refill_orders AS orders "
        "WHERE NOT EXISTS (SELECT 1 FROM order_status_history AS history "
        "WHERE history.order_id = orders.id)"
    )

    indexes = (
        "CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time "
        "ON login_attempts(ip_hash, attempted_at)",
        "CREATE INDEX IF NOT EXISTS idx_medicines_letter ON medicines(letter)",
        "CREATE INDEX IF NOT EXISTS idx_medicines_name ON medicines(name)",
        "CREATE INDEX IF NOT EXISTS idx_prescriptions_patient "
        "ON prescriptions(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_dose_logs_patient_date "
        "ON dose_logs(patient_id, scheduled_for)",
        "CREATE INDEX IF NOT EXISTS idx_refill_orders_patient "
        "ON refill_orders(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_comm_logs_patient "
        "ON communication_logs(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_comm_logs_order "
        "ON communication_logs(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_status_history_order "
        "ON order_status_history(order_id)",
        "CREATE INDEX IF NOT EXISTS idx_reminder_disp_date "
        "ON reminder_dispatches(scheduled_date, status)",
        "CREATE INDEX IF NOT EXISTS idx_reminder_disp_sid "
        "ON reminder_dispatches(provider_sid)",
        "CREATE INDEX IF NOT EXISTS idx_vitals_patient_date "
        "ON health_vitals(patient_id, recorded_at)",
    )
    for statement in indexes:
        conn.execute(statement)

    if previous_version < 9:
        conn.execute(
            """
            DELETE FROM users WHERE
              (username = 'patient' AND phone = '9443210987'
               AND full_name = 'P. Ramasamy (Senior Citizen)') OR
              (username = 'caregiver' AND phone = '9876543210'
               AND full_name = 'R. Karthik (Eldest Son & Caregiver)') OR
              (username = 'pharmacist' AND phone = '9840012345'
               AND full_name = 'Dr. R. Sundaram, B.Pharm (Apollo Pharmacy)') OR
              (username = 'delivery' AND phone = '9840054321'
               AND full_name = 'M. Senthil (Express Delivery Partner)') OR
              (username = 'admin' AND phone = '9999900000'
               AND full_name = 'Hospital Administrator')
            """
        )
        conn.execute(
            """
            UPDATE pharmacy_stores SET name = 'Unconfigured pharmacy',
              license_number = NULL, phone = NULL, mobile = NULL, email = NULL,
              address = NULL, pharmacist_name = NULL, operating_hours = NULL
            WHERE license_number = 'DL-TN-CH-2024-9988'
            """
        )
        conn.execute(
            "UPDATE refill_orders SET pharmacy_name = NULL "
            "WHERE pharmacy_name = 'Apollo Senior Care Pharmacy'"
        )
        conn.execute(
            "UPDATE refill_orders SET delivery_rider = NULL "
            "WHERE delivery_rider = "
            "'Assigned Delivery Partner (Ph: 98765-43210)'"
        )
        conn.execute(
            "UPDATE refill_orders SET rider_phone = NULL, rider_vehicle = NULL, "
            "rider_lat = NULL, rider_lng = NULL "
            "WHERE rider_phone = '9876543210' OR rider_vehicle = "
            "'Honda Activa Electric (TN-09-CQ-2819)'"
        )

    conn.execute("PRAGMA user_version = 9")
    conn.commit()


def init_db():
    conn = get_db_connection()
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE;")
        migrate_db(conn)
        med_count = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        demo_seed_enabled = os.getenv("SMART_MEDS_DEMO_SEED", "").lower() in {
            "true",
            "1",
            "yes",
        }
        if med_count == 0 and demo_seed_enabled:
            seed_medicines(conn.cursor())
            conn.commit()
        if demo_seed_enabled:
            pharmacy_count = conn.execute(
                "SELECT COUNT(*) FROM pharmacy_stores"
            ).fetchone()[0]
            if pharmacy_count == 0:
                conn.execute(
                    """
                    INSERT INTO pharmacy_stores (
                        name, license_number, phone, mobile, email, address,
                        pharmacist_name, operating_hours
                    ) VALUES (
                        'DEMO ONLY - Fictional Pharmacy', NULL, NULL, NULL,
                        NULL, NULL, NULL, NULL
                    )
                    """
                )
                conn.commit()
    finally:
        conn.close()


def seed_medicines(cursor):
    """Seed the optional A-to-Z demo medicine catalog."""
    medicines_list = [
        # A
        ("Amlodipine (Amlong)", "Amlodipine Besylate", "BP & Heart", "A", "5 mg", "Tablet", "After Food", "Lowers high blood pressure and prevents chest angina pain", "Blood Pressure Reduction", "Avoid grapefruit juice."),
        ("Atorvastatin (Atorva)", "Atorvastatin Calcium", "Cholesterol & Heart", "A", "10 mg", "Tablet", "Night / Before Bed", "Reduces bad cholesterol (LDL) and triglycerides in arteries", "Cholesterol Management", "Take regularly at night."),
        ("Aspirin 75 (Ecosprin)", "Acetylsalicylic Acid", "Heart & Blood Thinner", "A", "75 mg", "Tablet", "After Food", "Blood thinner that helps prevent heart attack and stroke", "Cardiovascular Protection", "Take strictly with or after food."),
        ("Azithromycin (Azithral)", "Azithromycin", "Antibiotic & Infection", "A", "500 mg", "Tablet", "1 hr Before or 2 hrs After Food", "Treats bacterial chest, throat, and sinus infections", "Bacterial Infection Control", "Complete the full course."),
        ("Allopurinol (Zyloric)", "Allopurinol", "Gout & Uric Acid", "A", "100 mg", "Tablet", "After Food", "Reduces uric acid levels in blood to relieve joint pain", "Uric Acid Reduction", "Drink plenty of water."),
        ("Alprazolam (Alprax)", "Alprazolam", "Sleep & Anxiety", "A", "0.25 mg", "Tablet", "Bedtime", "Short-term relief of severe restlessness and sleep anxiety", "Sleep & Anxiety Relief", "Use strictly under doctor supervision."),

        # B
        ("Bisoprolol (Concor)", "Bisoprolol Fumarate", "BP & Heart", "B", "2.5 mg", "Tablet", "Morning with Food", "Slows heart rate and makes it easier for heart to pump blood", "Heart Rate Stabilization", "Do not stop suddenly."),
        ("Baclofen (Lioresal)", "Baclofen", "Muscle Relaxant", "B", "10 mg", "Tablet", "After Food", "Relieves muscle spasms, joint stiffness, and spinal tension", "Muscle Spasm Relief", "May cause slight drowsiness."),
        ("B-Complex with B12 (Becosules)", "Vitamin B Complex + C", "Vitamins & Energy", "B", "1 Cap", "Capsule", "After Breakfast", "Boosts cellular energy, nerve health, and ulcer healing", "Nerve & Energy Nutrition", "Harmless yellow urine is normal."),
        ("Budesonide Inhaler (Budecort)", "Budesonide", "Respiratory & Asthma", "B", "200 mcg", "Inhaler", "Twice Daily", "Prevents breathing difficulty, wheezing, and COPD airway swelling", "Asthma & Airway Control", "Rinse mouth after each puff."),
        ("Bromhexine (Bisolvon)", "Bromhexine HCl", "Cough & Cold", "B", "8 mg", "Tablet", "After Food", "Breaks down thick mucus and phlegm in throat and chest", "Mucus Clearance", "Drink warm fluids."),

        # C
        ("Clopidogrel (Clopilet)", "Clopidogrel Bisulfate", "Heart & Blood Thinner", "C", "75 mg", "Tablet", "After Food", "Prevents blood clots after heart stent or stroke", "Antiplatelet Clot Prevention", "Inform dentist before extraction."),
        ("Calcium + Vitamin D3 (Shelcal)", "Calcium Carbonate + Vit D3", "Bones & Joints", "C", "500 mg", "Tablet", "After Lunch", "Strengthens aging bones and reduces fracture risk", "Bone & Joint Strength", "Take with plenty of water."),
        ("Carvedilol (Carca)", "Carvedilol", "Heart Failure & BP", "C", "6.25 mg", "Tablet", "With Food", "Treats heart failure and high blood pressure", "Cardiac Function Support", "Take with meals to prevent dizziness."),
        ("Cilnidipine (Cilacar)", "Cilnidipine", "BP & Kidney Protection", "C", "10 mg", "Tablet", "After Breakfast", "Dual L/N-type calcium channel blocker protecting kidney filters", "Kidney-Safe BP Control", "Do not skip doses."),
        ("Ciprofloxacin (Cifran)", "Ciprofloxacin", "Antibiotic", "C", "500 mg", "Tablet", "After Food", "Broad-spectrum antibiotic for urinary and chest infections", "Urinary Infection Relief", "Avoid taking with milk products."),
        ("Cetirizine (Cetzine)", "Cetirizine HCl", "Allergy & Cold", "C", "10 mg", "Tablet", "Night", "Relieves runny nose, itching, sneezing, and allergy symptoms", "Allergy & Itch Relief", "Take before sleep as it causes drowsiness."),

        # D
        ("Dapagliflozin (Forxiga)", "Dapagliflozin", "Diabetes & Heart", "D", "10 mg", "Tablet", "Morning with/without Food", "Removes excess sugar via urine; protects heart and kidneys", "Glycemic & Renal Protection", "Drink at least 2 liters of water daily."),
        ("Digoxin (Lanoxin)", "Digoxin", "Heart Rhythm", "D", "0.25 mg", "Tablet", "Morning Empty Stomach", "Treats irregular heartbeat (atrial fibrillation) and weakness", "Heart Rhythm Regulation", "Check pulse regularly."),
        ("Diltiazem (Dilzem)", "Diltiazem HCl", "BP & Angina", "D", "30 mg", "Tablet", "Before Food", "Controls high BP and prevents severe chest squeezing pain", "Angina & BP Management", "Do not crush extended-release tablets."),
        ("Donepezil (Donecept)", "Donepezil HCl", "Memory & Dementia", "D", "5 mg", "Tablet", "Bedtime", "Improves memory and thinking in elderly Alzheimer's patients", "Cognitive & Memory Support", "Take right before sleeping."),
        ("Domperidone (Domstal)", "Domperidone", "Digestion & Nausea", "D", "10 mg", "Tablet", "15-30 min Before Food", "Relieves nausea, vomiting, feeling bloated, and indigestion", "Anti-Nausea & Digestion", "Take before meals."),
        ("Diclofenac Gel / Tab (Voveran)", "Diclofenac Sodium", "Joint & Knee Pain", "D", "50 mg", "Tablet", "Strictly After Food", "Relieves knee arthritis, joint stiffness, and back pain", "Arthritis & Pain Relief", "Elderly should use lowest effective dose."),

        # E
        ("Enalapril (Envas)", "Enalapril Maleate", "BP & Heart", "E", "5 mg", "Tablet", "Morning", "Relaxes blood vessels to lower BP and eases cardiac workload", "Hypertension Reduction", "Report persistent dry cough to doctor."),
        ("Empagliflozin (Jardiance)", "Empagliflozin", "Diabetes & Heart", "E", "10 mg", "Tablet", "Morning", "Lowers blood glucose and reduces risk of cardiovascular death", "Cardioprotective Diabetes Care", "Keep hydrated throughout the day."),
        ("Ezetimibe (Ezentia)", "Ezetimibe", "Cholesterol", "E", "10 mg", "Tablet", "With or without Food", "Prevents cholesterol absorption from digested food in intestines", "Cholesterol Absorption Blocker", "Often paired with statins."),
        ("Escitalopram (Nexito)", "Escitalopram", "Mood & Depression", "E", "10 mg", "Tablet", "Morning or Night", "Helps elderly cope with anxiety, low mood, and grief", "Mood & Emotional Balance", "Takes 2-3 weeks for full effect."),
        ("Eye Lubricant Drops (Refresh Tears)", "Carboxymethylcellulose", "Eye Health", "E", "0.5% Drops", "Drops", "1-2 drops 3 times daily", "Relieves burning, dryness, and irritation in senior eyes", "Dry Eye Hydration", "Discard bottle 1 month after opening."),

        # F
        ("Furosemide (Lasix)", "Furosemide", "Water Pill & Swelling", "F", "40 mg", "Tablet", "Morning (Never at night)", "Removes excess fluid; reduces swollen feet and lungs", "Edema & Fluid Relief", "Take in morning to avoid midnight urination."),
        ("Fenofibrate (Lipicard)", "Fenofibrate", "Triglycerides", "F", "145 mg", "Tablet", "With Meals", "Lowers dangerously high triglycerides in blood", "Triglyceride Reduction", "Always swallow whole with a full meal."),
        ("Folic Acid (Folvite)", "Folic Acid / Vitamin B9", "Blood & Anemia", "F", "5 mg", "Tablet", "After Food", "Helps produce healthy red blood cells; treats fatigue", "Red Blood Cell Support", "Safe for daily consumption."),
        ("Famotidine (Famocid)", "Famotidine", "Acidity & Ulcer", "F", "20 mg", "Tablet", "Before Food or Bedtime", "Decreases stomach acid production and eases burning", "Heartburn & Acidity Relief", "Quick relief from acid reflux."),
        ("Finasteride (Finast)", "Finasteride", "Prostate Care (Men)", "F", "5 mg", "Tablet", "With or without Food", "Shrinks enlarged prostate, restores smooth urine flow in men", "Prostate Health (BPH)", "Must take consistently."),

        # G
        ("Glimepiride (Amaryl)", "Glimepiride", "Diabetes", "G", "1 mg", "Tablet", "Breakfast First Bite", "Stimulates pancreas to produce insulin during meals", "Blood Sugar Control", "Never skip breakfast after taking this."),
        ("Gliclazide (Diamicron XR)", "Gliclazide", "Diabetes", "G", "60 mg", "Tablet", "With Breakfast", "Long-acting diabetes medicine that prevents sugar spikes", "Extended Sugar Regulation", "Swallow whole, do not chew."),
        ("Gabapentin (Gabapin)", "Gabapentin", "Nerve Pain & Neuropathy", "G", "100 mg", "Capsule", "Night", "Soothes burning, tingling sensation in diabetic feet", "Diabetic Neuropathy Relief", "Helps improve sound sleep."),
        ("Glucosamine + Chondroitin", "Glucosamine Sulfate", "Joint Cartilage", "G", "1 Tab", "Tablet", "After Food", "Nourishes knee cartilage and relieves arthritic stiffness", "Cartilage & Joint Repair", "Consistent use for 2-3 months."),
        ("Gaviscon Liquid (Antacid)", "Sodium Alginate + Bicarbonate", "Acid Reflux & Heartburn", "G", "10 ml", "Syrup", "After Meals & Bedtime", "Forms a protective barrier over stomach to stop reflux", "Instant Heartburn Barrier", "Shake bottle well before use."),

        # H
        ("Hydrochlorothiazide (Aquazide)", "Hydrochlorothiazide", "BP & Fluid", "H", "12.5 mg", "Tablet", "Morning After Breakfast", "Mild diuretic that keeps blood pressure in safe zone", "Mild Diuretic for BP", "Eat potassium-rich foods like bananas."),
        ("Hydroxychloroquine (HCQS)", "Hydroxychloroquine", "Rheumatoid Arthritis", "H", "200 mg", "Tablet", "With Food", "Reduces severe joint swelling and autoimmune stiffness", "Rheumatoid Joint Therapy", "Annual eye checkup recommended."),
        ("Haloperidol (Serenace)", "Haloperidol", "Psychiatric & Agitation", "H", "0.5 mg", "Tablet", "Bedtime", "Controls extreme agitation, confusion, or hallucinations", "Agitation & Calm Support", "Use strict doctor supervision."),
        ("Heparin Cream (Thrombophob)", "Heparin Sodium + Benzyl Nicotinate", "Vein Pain & Bruises", "H", "Gel", "Ointment", "Apply 2-3 times locally", "Reduces painful blood clots and bruises after injections/IVs", "Bruise & Vein Recovery", "Gently apply without rubbing."),

        # I
        ("Insulin Glargine (Lantus)", "Insulin Glargine (rDNA)", "Diabetes", "I", "10-20 units", "Injection", "Fixed Time at Night", "24-hour basal insulin to maintain steady sugar levels", "24-Hour Basal Insulin", "Rotate injection sites on abdomen/thigh."),
        ("Irbesartan (Irovel)", "Irbesartan", "BP & Kidney Protection", "I", "150 mg", "Tablet", "Morning", "Controls hypertension and delays diabetic kidney damage", "Renal-Protective BP Control", "Check blood pressure weekly."),
        ("Isosorbide Mononitrate (Monotrate)", "Isosorbide Mononitrate", "Heart & Angina", "I", "20 mg", "Tablet", "Morning & Afternoon", "Prevents sudden chest pain and keeps coronary arteries open", "Coronary Artery Dilation", "Take last dose before 6 PM."),
        ("Ibuprofen (Brufen)", "Ibuprofen", "Pain & Inflammation", "I", "400 mg", "Tablet", "Strictly After Full Meal", "Relieves fever, toothache, headache, and severe body aches", "Pain & Inflammation Relief", "Always take with food."),
        ("Ipratropium Inhaler (Ipavent)", "Ipratropium Bromide", "COPD & Breathing", "I", "20 mcg", "Inhaler", "3-4 times daily", "Opens bronchial air passages for easy breathing in seniors", "Bronchial Airway Dilation", "Rinse mouth after inhalation."),

        # J
        ("Januvia (Sitagliptin)", "Sitagliptin Phosphate", "Diabetes", "J", "50 mg", "Tablet", "Morning with/without Food", "Boosts body's natural incretin hormones to lower sugar", "Incretin-Based Sugar Control", "Safe for kidneys in appropriate dose."),
        ("Jardiance (Empagliflozin)", "Empagliflozin", "Diabetes & Heart", "J", "25 mg", "Tablet", "Morning", "Cardioprotective SGLT2 inhibitor for elderly diabetics", "Heart-Protective Glucose Reducer", "Maintain good personal hygiene."),
        ("Jylamvo (Methotrexate)", "Methotrexate", "Severe Arthritis", "J", "7.5 mg", "Tablet", "ONCE A WEEK ONLY", "Disease modifying antirheumatic drug for chronic joint pain", "Weekly Antirheumatic Therapy", "NEVER take daily! Take only once weekly."),

        # K
        ("Ketorolac Eye Drops (Acular)", "Ketorolac Tromethamine", "Eye Surgery & Pain", "K", "0.5%", "Drops", "1 drop 3 times daily", "Relieves eye inflammation and pain after cataract surgery", "Post-Op Ophthalmic Pain Relief", "Do not touch dropper tip with fingers."),
        ("Potassium Citrate (K-Cit)", "Potassium Citrate", "Kidney Stones & Urine pH", "K", "Syrup/Tab", "Syrup", "After Meals with Water", "Prevents recurring kidney stones and neutralizes urine acidity", "Urinary Alkalinizer", "Mix syrup in a glass of water."),
        ("Ketoconazole (Nizral)", "Ketoconazole", "Fungal Skin Care", "K", "2% Cream", "Cream", "Twice Daily Locally", "Treats senior skin fold fungal infections, itching, rashes", "Antifungal Skin Therapy", "Keep skin clean and dry."),

        # L
        ("Losartan (Repace)", "Losartan Potassium", "BP & Heart", "L", "50 mg", "Tablet", "Morning", "First-choice medicine for high BP and stroke prevention", "Hypertension & Stroke Prevention", "Do not use salt substitutes with potassium."),
        ("Levothyroxine (Thyronorm / Eltroxin)", "Levothyroxine Sodium", "Thyroid Deficiency", "L", "50 mcg", "Tablet", "Early Morning Empty Stomach", "Replaces deficient thyroid hormone; treats fatigue and chills", "Thyroid Hormone Replacement", "Wait 45 minutes before tea or breakfast."),
        ("Lipaglyn (Saroglitazar)", "Saroglitazar", "Diabetic Dyslipidemia", "L", "4 mg", "Tablet", "Before Food", "Addresses high triglycerides and uncontrolled blood sugar", "Triglyceride & Sugar Balancer", "Take once daily before main meal."),
        ("Levetiracetam (Levipil)", "Levetiracetam", "Seizures & Epilepsy", "L", "500 mg", "Tablet", "Twice Daily", "Controls neurological seizures and stabilizes brain activity", "Anticonvulsant Therapy", "Do not miss doses or alter timings."),
        ("Lorazepam (Ativan)", "Lorazepam", "Anxiety & Sleep", "L", "1 mg", "Tablet", "Bedtime", "Short-term relief for severe restlessness or bedtime insomnia", "Sedative & Restlessness Relief", "Use strictly under doctor supervision."),
        ("Linagliptin (Trajenta)", "Linagliptin", "Diabetes (Kidney Safe)", "L", "5 mg", "Tablet", "Any Time of Day", "Safe diabetes medication for elderly patients with kidney weakness", "Kidney-Safe Diabetes Pill", "No dose adjustment needed for renal impairment."),

        # M
        ("Metformin (Glycomet)", "Metformin HCl", "Type 2 Diabetes", "M", "500 mg", "Tablet", "After Meals", "Cornerstone medicine for lowering liver glucose output", "Primary Glycemic Control", "Take strictly after meals."),
        ("Metoprolol (Betaloc / Met XL)", "Metoprolol Succinate", "BP & Heart Rate", "M", "25 mg", "Tablet", "Morning After Breakfast", "Slows racing heartbeat, protects heart after heart attack", "Beta-Blocker Heart Protection", "Do not chew sustained-release tablets."),
        ("Methylcobalamin / Vit B12 (Nurokind)", "Methylcobalamin", "Nerve Strength & B12", "M", "1500 mcg", "Tablet", "After Food", "Restores damaged nerve sheath; prevents foot numbness", "Nerve Regeneration & B12", "Very safe and beneficial for seniors."),
        ("Montelukast (Montair)", "Montelukast Sodium", "Allergy & Asthma", "M", "10 mg", "Tablet", "Night Before Sleep", "Prevents night-time coughing, wheezing and dust allergies", "Nocturnal Asthma Prevention", "Take consistently at nighttime."),
        ("Meloxicam (Mobic)", "Meloxicam", "Osteoarthritis Pain", "M", "7.5 mg", "Tablet", "After Food", "Gentler anti-inflammatory for chronic knee and hip joint pain", "Osteoarthritis Relief", "Drink plenty of water."),
        ("Moxifloxacin Eye Drops (Vigamox)", "Moxifloxacin HCl", "Eye Infection & Pink Eye", "M", "0.5%", "Drops", "1 drop 4 times daily", "Cures bacterial eye redness, discharge, and infections", "Ophthalmic Antibiotic", "Keep bottle refrigerated if directed."),

        # N
        ("Nebivolol (Nebicard)", "Nebivolol", "Hypertension & Heart", "N", "5 mg", "Tablet", "Morning", "Modern cardio-selective beta blocker with nitric oxide release", "Vasodilating Beta-Blocker", "Safe for seniors with mild asthma."),
        ("Nifedipine (Nicardia Retard)", "Nifedipine Retard", "Severe BP & Chest Spasm", "N", "20 mg", "Tablet", "After Meals", "Relaxes cardiac muscle cells and widens stiff blood vessels", "Arterial Vasodilator", "Swallow whole with water."),
        ("Nitroglycerin Sublingual (Sorbitrate)", "Isosorbide Dinitrate", "Emergency Angina Pain", "N", "5 mg", "Tablet", "Place Under Tongue during pain", "Immediate SOS relief for acute squeezing chest angina", "Immediate Angina Emergency Relief", "Keep handy in pocket at all times."),
        ("Nicorandil (Korandil)", "Nicorandil", "Heart & Angina", "N", "5 mg", "Tablet", "Twice Daily After Food", "Potassium channel opener that keeps heart vessels relaxed", "Nitrate-like Vasodilation", "Do not combine with ED pills."),
        ("Naproxen (Naprosyn)", "Naproxen", "Arthritis & Muscle Aches", "N", "250 mg", "Tablet", "After Meals", "Long-acting non-steroidal anti-inflammatory for joint pain", "Joint & Musculoskeletal Relief", "Take with food or antacid."),
        ("Neurobion Forte", "Vitamin B1, B6, B12", "Nerve Repair & Energy", "N", "1 Tab", "Tablet", "Daily After Meal", "Essential senior vitamin supplement for strong healthy nerves", "Nerve Nourishment & Vitality", "Daily nutritional support."),

        # O
        ("Omeprazole (Omez)", "Omeprazole", "Acidity & GERD", "O", "20 mg", "Capsule", "30 min Before Breakfast", "Reduces gastric acid secretion; heals ulcers and reflux", "Proton Pump Inhibitor for Acidity", "Take early in the morning."),
        ("Olmesartan (Olmecip)", "Olmesartan Medoxomil", "Hypertension", "O", "20 mg", "Tablet", "Morning with/without Food", "Long-acting angiotensin receptor blocker for stable 24h BP", "24-Hour Blood Pressure Stability", "Check BP monthly."),
        ("Ondansetron (Emeset)", "Ondansetron HCl", "Nausea & Vomiting", "O", "4 mg", "Tablet", "Before Food as needed", "Stops feeling sick, nausea, dizziness and vomiting", "Anti-Emetic (Nausea Reliever)", "Dissolves rapidly on tongue."),
        ("Oxcarbazepine (Trileptal)", "Oxcarbazepine", "Neuropathy & Seizures", "O", "300 mg", "Tablet", "Twice Daily", "Soothes facial nerve pain (trigeminal neuralgia) and seizures", "Nerve Stabilization & Anticonvulsant", "Check blood sodium levels."),
        ("Ofloxacin Eye/Ear Drops (Zenflox)", "Ofloxacin", "Ear / Eye Infection", "O", "0.3%", "Drops", "2 drops twice daily", "Treats discharging ear, itching, or bacterial eye redness", "Topical Antibiotic Drops", "Warm bottle in palms before ear use."),

        # P
        ("Pantoprazole (Pan 40)", "Pantoprazole Sodium", "Acidity & Gastric Protection", "P", "40 mg", "Tablet", "30 min Before Breakfast", "Protects stomach lining against irritation from other medicines", "Gastric Mucosa Protection", "Swallow whole before breakfast."),
        ("Paracetamol (Dolo 650 / Calpol)", "Paracetamol", "Fever & Body Pain", "P", "650 mg", "Tablet", "After Food as needed", "Safe pain and fever reliever for seniors when used responsibly", "Antipyretic & Analgesic", "Do not exceed 3 tablets in 24 hours."),
        ("Prasugrel (Prasita)", "Prasugrel HCl", "Heart Stent Protection", "P", "10 mg", "Tablet", "Once Daily with Food", "Prevents blood clotting after heart angioplasty and stenting", "Post-Stent Antiplatelet", "Do not skip without cardiologist consent."),
        ("Pioglitazone (Pioz)", "Pioglitazone", "Insulin Resistance", "P", "15 mg", "Tablet", "Once Daily", "Increases body's insulin sensitivity in stubborn diabetes", "Insulin Sensitizer", "Monitor for foot swelling."),
        ("Pregabalin (Pregeb)", "Pregabalin", "Neuropathic Pain & Sciatica", "P", "75 mg", "Capsule", "Bedtime", "Relieves shooting sciatica nerve pain, spine ache and tingling", "Neuropathic Pain Relief", "Take strictly at bedtime."),
        ("Prednisolone (Omnacortil)", "Prednisolone", "Severe Inflammation & Allergy", "P", "5 mg", "Tablet", "Morning with Breakfast", "Steroid medicine to quiet down sudden severe allergic flare-ups", "Corticosteroid Anti-Inflammatory", "Do not stop abruptly; taper slowly."),

        # Q
        ("Quetiapine (Qutipin)", "Quetiapine Fumarate", "Elderly Agitation & Sleep", "Q", "25 mg", "Tablet", "Bedtime", "Helps seniors with dementia agitation, hallucinations or sundowning", "Elderly Behavioral Support", "Start with the lowest prescribed dose."),
        ("Quinapril (Accupril)", "Quinapril HCl", "Hypertension & Heart", "Q", "10 mg", "Tablet", "Morning", "ACE inhibitor reducing cardiac workload and high pressure", "ACE-Inhibitor Vasodilation", "Drink adequate water."),
        ("Coenzyme Q-10 (CoQ10)", "Ubiquinone", "Heart & Muscle Energy", "Q", "100 mg", "Capsule", "With Meal containing fat", "Restores muscle energy and reduces statin-induced muscle aches", "Mitochondrial Energy Support", "Helps senior stamina."),

        # R
        ("Rosuvastatin (Rozavel)", "Rosuvastatin Calcium", "High Cholesterol", "R", "10 mg", "Tablet", "Night Before Bed", "Potent statin to sweep away arterial plaque and prevent stroke", "High-Potency Plaque Reduction", "Take every night."),
        ("Ramipril (Cardace)", "Ramipril", "BP & Heart Protection", "R", "2.5 mg", "Tablet", "Morning with/without Food", "Protects heart and kidneys; lowers high blood pressure", "Cardio-Renal Protection", "Consult doctor if persistent dry cough."),
        ("Ranolazine (Rancad)", "Ranolazine", "Chronic Chest Angina", "R", "500 mg", "Tablet", "Twice Daily with Food", "Improves blood flow without dropping BP or pulse", "Chronic Angina Management", "Swallow whole, do not chew."),
        ("Risedronate (Actonel)", "Risedronate Sodium", "Osteoporosis & Weak Bones", "R", "35 mg", "Tablet", "Once Weekly on Empty Stomach", "Rebuilds porous brittle bones to prevent hip and spine fractures", "Bone Density Enhancement", "Stay upright for 30 mins after taking."),
        ("Rivaroxaban (Xarelto)", "Rivaroxaban", "Blood Thinner & DVT", "R", "10 mg", "Tablet", "With Dinner", "Direct oral anticoagulant preventing deep vein clots and stroke", "Novel Oral Anticoagulant (NOAC)", "Take with evening meal."),
        ("Rabeprazole (Razo 20)", "Rabeprazole Sodium", "Acid Peptic Disease", "R", "20 mg", "Tablet", "Before Breakfast", "Rapidly suppresses heartburn, sour burps and indigestion", "Fast-Acting Acid Suppression", "Take 30 minutes before first morning tea."),

        # S
        ("Spironolactone (Aldactone)", "Spironolactone", "Heart Failure & Fluid", "S", "25 mg", "Tablet", "Morning After Breakfast", "Potassium-sparing diuretic that protects weak heart muscle", "Aldosterone Antagonist", "Avoid excess bananas or salt substitutes."),
        ("Sitagliptin + Metformin (Janumet)", "Sitagliptin + Metformin", "Diabetes Combination", "S", "50/500 mg", "Tablet", "Twice Daily with Meals", "Dual-action control for stubborn diabetic blood sugar levels", "Dual-Action Glycemic Control", "Take with meals."),
        ("Salbutamol Inhaler (Asthalin)", "Salbutamol Sulfate", "Emergency Asthma Relief", "S", "100 mcg", "Inhaler", "As needed for breathlessness", "Instant bronchodilator puff for sudden asthma or breathlessness", "Emergency Bronchodilator", "Keep by bedside table."),
        ("Sertraline (Daxid)", "Sertraline HCl", "Depression & Anxiety", "S", "25 mg", "Tablet", "Morning with Breakfast", "Elevates mood and removes constant worry in lonely seniors", "Serotonin Balance Support", "Take in morning to prevent sleeplessness."),
        ("Sucralfate Liquid (Sucrafil)", "Sucralfate Suspension", "Gastric & Mouth Ulcers", "S", "10 ml", "Suspension", "1 hr Before Meals & Bed", "Coats stomach ulcer craters like a protective band-aid", "Ulcer Protective Coating", "Take on an empty stomach."),

        # T
        ("Telmisartan (Telma 40)", "Telmisartan", "High Blood Pressure", "T", "40 mg", "Tablet", "Morning After Breakfast", "Most widely used senior BP pill; provides 24-hour protection", "24-Hour Antihypertensive", "Take at the exact same time every morning."),
        ("Torsemide (Dytor 10)", "Torsemide", "Foot Swelling & Edema", "T", "10 mg", "Tablet", "Morning Only", "Powerful diuretic flushing out retained water from legs and abdomen", "Loop Diuretic for Swelling", "Take strictly in the morning."),
        ("Tamsulosin (Urimax 0.4)", "Tamsulosin HCl", "Prostate & Easy Urination", "T", "0.4 mg", "Capsule", "30 min After Dinner", "Relaxes prostate and bladder neck; restores smooth night urination", "Prostatic Muscle Relaxation", "Take every night after dinner."),
        ("Tramadol + Paracetamol (Ultracet)", "Tramadol + Acetaminophen", "Severe Joint Pain", "T", "37.5/325 mg", "Tablet", "After Food as needed", "Prescription relief for severe unbearable knee or spine agony", "Synergistic Pain Relief", "May cause mild drowsiness."),
        ("Thyroxine 100mcg (Thyronorm)", "Thyroxine Sodium", "Hypothyroidism", "T", "100 mcg", "Tablet", "Early Morning Empty Stomach", "Full replacement therapy for inactive thyroid gland", "Thyroid Metabolism Restorer", "Wait 45 minutes before tea or breakfast."),
        ("Ticagrelor (Brilinta)", "Ticagrelor", "Post-Heart Attack Care", "T", "90 mg", "Tablet", "Twice Daily with/without Food", "Next-generation platelet inhibitor preventing re-infarction", "Reversible Platelet Inhibitor", "Do not skip any doses."),
        ("Teneligliptin (Ziten)", "Teneligliptin", "Type 2 Diabetes", "T", "20 mg", "Tablet", "Once Daily Before Breakfast", "Daily DPP-4 inhibitor that controls post-meal sugar spikes", "Post-Prandial Sugar Balancer", "Affordable and senior-friendly."),

        # U
        ("Ursodeoxycholic Acid (Udiliv)", "Ursodeoxycholic Acid", "Gallbladder & Liver", "U", "300 mg", "Tablet", "With Meals", "Dissolves small cholesterol gallstones and detoxifies liver", "Hepatobiliary Health Support", "Take with meals with water."),
        ("Ubrogepant (Ubrelvy)", "Ubrogepant", "Migraine Relief", "U", "50 mg", "Tablet", "At onset of headache", "Targeted relief for acute senior migraines without heart side effects", "CGRP Migraine Blocker", "Use at first sign of headache."),

        # V
        ("Vildagliptin (Galvus)", "Vildagliptin", "Type 2 Diabetes", "V", "50 mg", "Tablet", "Twice Daily with Meals", "Controls glucose levels smoothly with very low risk of hypoglycemia", "Low-Hypo Diabetes Therapy", "Take morning and evening."),
        ("Voglibose (Volibo 0.2)", "Voglibose", "Post-Meal Sugar Spikes", "V", "0.2 mg", "Tablet", "First Bite of Every Meal", "Alpha-glucosidase inhibitor preventing sugar rush after rice/bread", "Carbohydrate Absorption Delayer", "Chew or swallow with first bite of food."),
        ("Valsartan (Valzaar)", "Valsartan", "Heart Failure & BP", "V", "80 mg", "Tablet", "Morning with Food", "Reduces hospitalizations in patients with weak heart pumping", "Heart Failure Support", "Regular blood pressure check advised."),
        ("Vitamin C + Zinc (Limcee)", "Ascorbic Acid + Zinc", "Immunity Booster", "V", "500 mg", "Chewable Tab", "After Breakfast", "Strengthens senior immune response against infections and colds", "Antioxidant & Immune Defense", "Chewable tablet."),
        ("Venlafaxine (Venlor)", "Venlafaxine XR", "Nerve Pain & Chronic Anxiety", "V", "37.5 mg", "Capsule", "Morning with Food", "Dual reuptake inhibitor relieving persistent burning nerve agony", "Serotonin-Norepinephrine Reuptake", "Do not crush extended-release capsule."),

        # W
        ("Warfarin (Warf 2 / 5)", "Warfarin Sodium", "Anticoagulant & Blood Thinner", "W", "2 mg", "Tablet", "Evening Fixed Time", "Traditional anticoagulant for artificial heart valves and deep clots", "Vitamin K Antagonist Anticoagulant", "Regular INR blood test is mandatory."),

        # X
        ("Xipamide (Xipamid)", "Xipamide", "Severe Swelling & BP", "X", "20 mg", "Tablet", "Morning", "Potent diuretic for resistant leg edema and cardiac fluid buildup", "Potent Diuretic Therapy", "Take in morning hours."),
        ("Xylometazoline Drops (Otrivin Adult)", "Xylometazoline HCl", "Nasal Congestion", "X", "0.1%", "Nasal Drops", "1-2 drops as needed", "Opens blocked senior nostrils for clear sleep breathing", "Nasal Decongestant", "Do not use for more than 5 consecutive days."),

        # Y
        ("Yeast Extract / Vitamin B-Complex", "Brewers Yeast + Complex", "Gut Health & Vitamins", "Y", "1 Tab", "Tablet", "After Meals", "Natural source of B-vitamins for senior gut digestion and stamina", "Digestive & Nutrient Support", "Supports healthy digestion."),

        # Z
        ("Zincovit Tablet", "Multivitamins + Minerals + Zinc", "Daily Vitality & Nutrition", "Z", "1 Tab", "Tablet", "After Lunch", "All-in-one daily multivitamin for elderly vitality, eyes and joints", "Comprehensive Senior Multivitamin", "Contains grape seed extract for vitality."),
        ("Zolpidem (Zolfresh)", "Zolpidem Tartrate", "Severe Insomnia", "Z", "5 mg", "Tablet", "Immediately Before Bed", "Short-term treatment for elderly severe sleeplessness", "Non-Benzodiazepine Hypnotic", "Take only in bed when ready to sleep."),
        ("Zafirlukast (Accolate)", "Zafirlukast", "Chronic Asthma", "Z", "20 mg", "Tablet", "1 hr Before or 2 hrs After Food", "Leukotriene receptor blocker keeping senior air passages clear", "Leukotriene Receptor Antagonist", "Take on an empty stomach.")
    ]

    for med in medicines_list:
        cursor.execute("""
        INSERT INTO medicines (
            name, generic_name, category, letter, default_dosage,
            form, timing_advice, description, therapeutic_use, precautions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, med)
