#!/usr/bin/env python3
"""Provision an administrator/pharmacy/delivery account from a trusted shell."""
from getpass import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from werkzeug.security import generate_password_hash
from database import get_db_connection, init_db


def main():
    init_db()
    username = input("Username (3-50 letters/numbers/._-): ").strip().lower()
    phone = "".join(ch for ch in input("Phone number (10 digits): ") if ch.isdigit())[-10:]
    full_name = input("Staff full name: ").strip()
    role = input("Role (ADMIN, PHARMACY, DELIVERY): ").strip().upper()
    password = getpass("Password (minimum 12 characters): ")
    confirm = getpass("Confirm password: ")
    if not re.fullmatch(r"[a-z0-9._-]{3,50}", username):
        raise SystemExit("Invalid username format.")
    if len(phone) != 10 or not full_name or len(full_name) > 120:
        raise SystemExit("Provide a valid 10-digit phone and name.")
    if role not in {"ADMIN", "PHARMACY", "DELIVERY"}:
        raise SystemExit("Only staff roles may be created by this tool; patient/caregiver accounts must use their self-service flows.")
    if len(password) < 12 or len(password) > 128 or password != confirm:
        raise SystemExit("Password must be 12-128 characters and match the confirmation.")

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, phone, password_hash, role, full_name, patient_id) VALUES (?, ?, ?, ?, ?, NULL)",
            (username, phone, generate_password_hash(password), role, full_name),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise SystemExit(f"Account was not created ({type(exc).__name__}). Check for duplicate username/phone.")
    finally:
        conn.close()
    print(f"Created {role} account for username {username!r}. No password was displayed or stored in plaintext.")


if __name__ == "__main__":
    main()
