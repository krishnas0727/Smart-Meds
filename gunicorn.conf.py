import os

bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
keepalive = 5
preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True


def post_worker_init(worker):
    if os.getenv("RUN_REMINDER_WORKER", "true").strip().lower() in {"1", "true", "yes"}:
        from services.scheduler_service import ReminderScheduler
        ReminderScheduler.start_background_scheduler(interval_seconds=30)
        worker.log.info("Reminder scheduler thread started in the single SQLite-backed web worker.")


def worker_exit(server, worker):
    try:
        from services.scheduler_service import ReminderScheduler
        ReminderScheduler.stop_background_scheduler()
    except Exception:
        pass
