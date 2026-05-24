"""Настройки приложения и пути к данным."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


DATA_DIR = Path.home() / ".avito_desktop"
CONFIG_PATH = DATA_DIR / "config.json"
FEED_PATH = DATA_DIR / "autoload_feed.xml"
PROJECT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass
class AppConfig:
    client_id: str = ""
    client_secret: str = ""
    user_id: int | None = None
    feed_public_url: str = ""
    contact_phone: str = ""
    default_category_slug: str = ""
    default_category_path: str = ""
    stats_period_days: int = 7
    min_views_baseline: int = 5
    drop_percent_threshold: int = 50
    auto_archive_enabled: bool = False
    scheduler_enabled: bool = False
    scheduler_interval_minutes: int = 60
    publish_cost_per_listing: float = 0.0
    category_publish_costs: dict[str, float] | None = None

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
            default_category_path=str(data.get("default_category_path", "")),
            stats_period_days=int(data.get("stats_period_days", 7)),
            min_views_baseline=int(data.get("min_views_baseline", 5)),
            drop_percent_threshold=int(data.get("drop_percent_threshold", 50)),
            auto_archive_enabled=bool(data.get("auto_archive_enabled", False)),
            scheduler_enabled=bool(data.get("scheduler_enabled", False)),
            scheduler_interval_minutes=int(data.get("scheduler_interval_minutes", 60)),
            publish_cost_per_listing=float(data.get("publish_cost_per_listing", 0) or 0),
            category_publish_costs={
                str(key): float(value)
                for key, value in (data.get("category_publish_costs") or {}).items()
            }
            or None,
        )


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def load_config() -> AppConfig:
    ensure_data_dir()
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config = AppConfig.from_dict(data)
    else:
        config = AppConfig()

    env: dict[str, str] = {}
    for env_path in (PROJECT_ENV_PATH, Path.cwd() / ".env"):
        if env_path.exists():
            env.update(_read_env_file(env_path))

    if not config.client_id and env.get("AVITO_CLIENT_ID"):
        config.client_id = env["AVITO_CLIENT_ID"]
    if not config.client_secret and env.get("AVITO_CLIENT_SECRET"):
        config.client_secret = env["AVITO_CLIENT_SECRET"]
    if config.user_id is None and env.get("AVITO_USER_ID", "").isdigit():
        config.user_id = int(env["AVITO_USER_ID"])

    return config


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
