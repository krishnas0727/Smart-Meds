#!/usr/bin/env python3
"""Create a consistent SQLite backup using the SQLite backup API."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from database import DB_PATH


def main():
    source = Path(DB_PATH).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Database file does not exist: {source}")
    output_dir = Path(os.environ.get("BACKUP_DIR", ROOT / "backups")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir / f"smart-meds-{timestamp}.sqlite3"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("Backup integrity check failed")
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(destination, 0o600)
    print(f"SQLite backup created: {destination} ({destination.stat().st_size} bytes)")
    print("This file contains sensitive personal/health information; encrypt it and store it in access-controlled off-host storage.")


if __name__ == "__main__":
    main()
