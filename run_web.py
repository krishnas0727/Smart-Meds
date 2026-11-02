"""Launch the single-process Gunicorn deployment used with the persistent SQLite disk."""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

args = [
    sys.executable,
    "-m",
    "gunicorn",
    "--config",
    "gunicorn.conf.py",
    "app:app",
]
os.execv(sys.executable, args)
