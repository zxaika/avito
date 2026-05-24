"""Настройка файлового логирования приложения."""

from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import DATA_DIR, ensure_data_dir

LOG_DIR = DATA_DIR / "logs"
_current_log_file: Path | None = None


class _FlushFileHandler(RotatingFileHandler):
    """Пишет в файл и сразу сбрасывает буфер — лог виден при зависании."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def setup_logging() -> Path:
    global _current_log_file

    ensure_data_dir()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"avito_{datetime.now():%Y-%m-%d}.log"
    _current_log_file = log_file

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = _FlushFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    app_logger = logging.getLogger("avito")
    app_logger.setLevel(logging.DEBUG)
    app_logger.handlers.clear()
    app_logger.propagate = False
    app_logger.addHandler(file_handler)

    # SDK Avito (HTTP/retry) — только в файл
    for sdk_name in ("avito.transport", "httpx"):
        sdk_logger = logging.getLogger(sdk_name)
        sdk_logger.setLevel(logging.INFO)
        sdk_logger.handlers.clear()
        sdk_logger.addHandler(file_handler)
        sdk_logger.propagate = False

    app_logger.info("=" * 60)
    app_logger.info("Запуск Avito Desktop Manager")
    app_logger.info("Python %s", sys.version.replace("\n", " "))
    app_logger.info("Лог-файл: %s", log_file)
    app_logger.info("=" * 60)

    return log_file


def get_log_file() -> Path:
    if _current_log_file is None:
        return LOG_DIR / f"avito_{datetime.now():%Y-%m-%d}.log"
    return _current_log_file


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"avito.{name}")


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> str:
    """Пишет traceback в лог и возвращает текст для UI."""
    logger.error("%s: %s", message, exc)
    logger.debug("Traceback:\n%s", "".join(traceback.format_exception(exc)))
    return (
        f"{message}\n\n"
        f"Тип: {type(exc).__name__}\n"
        f"Сообщение: {exc}\n\n"
        f"Подробный лог:\n{get_log_file()}"
    )
