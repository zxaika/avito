"""Настройки приложения и пути к данным."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


DATA_DIR = Path.home() / ".avito_desktop"
CONFIG_PATH = DATA_DIR / "config.json"
FEED_PATH = DATA_DIR / "autoload_feed.xml"


@dataclass
class AppConfig:
    client_id: str = ""
    client_secret: str = ""
    user_id: int | None = None
    feed_public_url: str = ""
    contact_phone: str = ""
    default_category_slug: str = ""
    stats_period_days: int = 7
    min_views_baseline: int = 5
    drop_percent_threshold: int = 50
    auto_archive_enabled: bool = False
    scheduler_enabled: bool = False
    scheduler_interval_minutes: int = 60

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AppConfig:
        return cls(
            client_id=str(data.get("client_id", "")),
            client_secret=str(data.get("client_secret", "")),
            user_id=int(data["user_id"]) if data.get("user_id") else None,
            feed_public_url=str(data.get("feed_public_url", "")),
            contact_phone=str(data.get("contact_phone", "")),
            default_category_slug=str(data.get("default_category_slug", "")),
            stats_period_days=int(data.get("stats_period_days", 7)),
            min_views_baseline=int(data.get("min_views_baseline", 5)),
            drop_percent_threshold=int(data.get("drop_percent_threshold", 50)),
            auto_archive_enabled=bool(data.get("auto_archive_enabled", False)),
            scheduler_enabled=bool(data.get("scheduler_enabled", False)),
            scheduler_interval_minutes=int(data.get("scheduler_interval_minutes", 60)),
        )


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def load_config() -> AppConfig:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        return AppConfig()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return AppConfig.from_dict(data)


def save_config(config: AppConfig) -> None:
    ensure_data_dir()
    CONFIG_PATH.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_config_to_env(config: AppConfig) -> None:
    import os

    if config.client_id:
        os.environ["AVITO_CLIENT_ID"] = config.client_id
    if config.client_secret:
        os.environ["AVITO_CLIENT_SECRET"] = config.client_secret
    if config.user_id:
        os.environ["AVITO_USER_ID"] = str(config.user_id)
