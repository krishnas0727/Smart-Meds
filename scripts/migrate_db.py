#!/usr/bin/env python3
"""Run the application's idempotent SQLite schema/migration initializer."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from database import get_db_connection, init_db


def main():
    init_db()
    conn = get_db_connection()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    print(f"Database initialized; SQLite schema version={version}.")


if __name__ == "__main__":
    main()
