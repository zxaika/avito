"""Прямые запросы к Stats API Avito (обход несовместимого парсинга avito-py)."""

from __future__ import annotations

from typing import Any

import httpx

from avito.ads.models import ListingStats

from app.config import AppConfig, apply_config_to_env
from app.logging_setup import get_logger

API_BASE = "https://api.avito.ru"
logger = get_logger("stats")


def _fetch_token(http: httpx.Client, config: AppConfig) -> str:
    apply_config_to_env(config)
    response = http.post(
        f"{API_BASE}/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def parse_stats_payload(payload: object) -> list[ListingStats]:
    """Суммирует дневные uniqViews/uniqContacts/uniqFavorites по каждому объявлению."""
    if not isinstance(payload, dict):
        return []

    result = payload.get("result")
    if isinstance(result, dict):
        items = result.get("items")
    else:
        items = payload.get("items")

    if not isinstance(items, list):
        return []

    stats: list[ListingStats] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("itemId") or item.get("item_id") or item.get("id")
        if item_id is None:
            continue

        daily_rows = item.get("stats") or []
        views = 0
        contacts = 0
        favorites = 0
        if isinstance(daily_rows, list):
            for row in daily_rows:
                if not isinstance(row, dict):
                    continue
                views += int(row.get("uniqViews") or row.get("views") or 0)
                contacts += int(row.get("uniqContacts") or row.get("contacts") or 0)
                favorites += int(row.get("uniqFavorites") or row.get("favorites") or 0)

        stats.append(
            ListingStats(
                item_id=int(item_id),
                views=views,
                contacts=contacts,
                favorites=favorites,
            )
        )
    return stats


def fetch_item_stats(
    config: AppConfig,
    *,
    user_id: int,
    date_from: str,
    date_to: str,
    item_ids: list[int],
) -> list[ListingStats]:
    if not item_ids:
        return []

    body: dict[str, Any] = {
        "dateFrom": date_from,
        "dateTo": date_to,
        "itemIds": item_ids,
    }

    with httpx.Client(trust_env=False, timeout=60) as http:
        token = _fetch_token(http, config)
        response = http.post(
            f"{API_BASE}/stats/v1/accounts/{user_id}/items",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        if response.status_code >= 400:
            logger.error(
                "Stats API user_id=%s HTTP %s — %s",
                user_id,
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        parsed = parse_stats_payload(response.json())
        logger.info(
            "Stats API: period %s..%s, requested=%s, parsed=%s",
            date_from,
            date_to,
            len(item_ids),
            len(parsed),
        )
        return parsed
