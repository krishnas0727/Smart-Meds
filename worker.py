"""Standalone reminder worker for a host that shares the application's SQLite file.

Do not run this alongside the Gunicorn scheduler thread or as a separate Render
service with SQLite. Use one scheduler instance per shared SQLite database.
"""
import logging
import signal
import threading
import time
from dotenv import load_dotenv

load_dotenv()
from database import init_db
from services.scheduler_service import ReminderScheduler, get_current_ist_datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("smart-meds-worker")
stop_event = threading.Event()


def request_stop(signum, frame):
    LOG.info("Received shutdown signal %s", signum)
    stop_event.set()


def main():
    init_db()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    LOG.info("Reminder worker started; local time is %s", get_current_ist_datetime().isoformat())
    while not stop_event.is_set():
        try:
            result = ReminderScheduler.check_and_dispatch_due_reminders()
            if result.get("dispatched_sms_count") or result.get("dispatched_voice_count"):
                LOG.info("Dispatch counts: sms=%s voice=%s", result.get("dispatched_sms_count"), result.get("dispatched_voice_count"))
        except Exception as exc:
            LOG.error("Reminder cycle failed (%s)", type(exc).__name__)
        stop_event.wait(30)
    LOG.info("Reminder worker stopped cleanly")


if __name__ == "__main__":
    main()
