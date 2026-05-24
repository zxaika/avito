"""Прямые запросы к API автозагрузки (обход несовместимого парсинга avito-py)."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import AppConfig, apply_config_to_env
from app.logging_setup import get_logger

AUTOLOAD_BASE = "https://api.avito.ru"
logger = get_logger("autoload")


def _fetch_token(http: httpx.Client, config: AppConfig) -> str:
    apply_config_to_env(config)
    response = http.post(
        f"{AUTOLOAD_BASE}/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def autoload_get(config: AppConfig, path: str) -> dict[str, Any]:
    with httpx.Client(trust_env=False, timeout=60) as http:
        token = _fetch_token(http, config)
        response = http.get(
            f"{AUTOLOAD_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            logger.error(
                "Autoload API %s: HTTP %s — %s",
                path,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError(f"Ожидался JSON-объект: {path}")
        return payload


def fetch_category_tree_raw(config: AppConfig) -> list[dict[str, Any]]:
    payload = autoload_get(config, "/autoload/v1/user-docs/tree")
    categories = payload.get("categories")
    if not isinstance(categories, list):
        return []
    return categories


def fetch_category_fields_raw(config: AppConfig, node_slug: str) -> list[dict[str, Any]]:
    payload = autoload_get(
        config,
        f"/autoload/v1/user-docs/node/{node_slug}/fields",
    )
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return []
    return fields
