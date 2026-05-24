"""Точка входа Avito Desktop Manager."""

from app.logging_setup import get_log_file, get_logger, setup_logging
from app.main_window import run_app

if __name__ == "__main__":
    setup_logging()
    get_logger("app").info("run_app.py started, log=%s", get_log_file())
    run_app()
