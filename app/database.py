"""Локальная SQLite-база: черновики, фид, снимки статистики."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from app.config import DATA_DIR, ensure_data_dir

DB_PATH = DATA_DIR / "avito.db"


@dataclass
class DraftListing:
    ad_id: str
    title: str
    description: str
    price: int
    category: str
    city: str
    phone: str
    images: list[str]
    avito_item_id: int | None = None
    category_slug: str = ""
    category_path: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)
    in_feed: bool = True
    status: str = "draft"  # draft | published | archived
    created_at: str = ""


@dataclass
class StatsSnapshot:
    item_id: int
    period_label: str
    views: int
    contacts: int
    favorites: int
    fetched_at: str


def _connect() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(feed_items)")}
    if "category_slug" not in columns:
        conn.execute(
            "ALTER TABLE feed_items ADD COLUMN category_slug TEXT NOT NULL DEFAULT ''"
        )
    if "extra_fields_json" not in columns:
        conn.execute(
            "ALTER TABLE feed_items ADD COLUMN extra_fields_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "category_path" not in columns:
        conn.execute(
            "ALTER TABLE feed_items ADD COLUMN category_path TEXT NOT NULL DEFAULT ''"
        )


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS feed_items (
                ad_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL,
                category TEXT NOT NULL,
                city TEXT NOT NULL,
                phone TEXT NOT NULL,
                images_json TEXT NOT NULL DEFAULT '[]',
                avito_item_id INTEGER,
                category_slug TEXT NOT NULL DEFAULT '',
                category_path TEXT NOT NULL DEFAULT '',
                extra_fields_json TEXT NOT NULL DEFAULT '{}',
                in_feed INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stats_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                period_label TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                contacts INTEGER NOT NULL DEFAULT 0,
                favorites INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL,
                UNIQUE(item_id, period_label)
            );
            """
        )
        _migrate(conn)


def save_feed_item(item: DraftListing) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO feed_items (
                ad_id, title, description, price, category, city, phone,
                images_json, avito_item_id, category_slug, category_path, extra_fields_json,
                in_feed, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ad_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                price=excluded.price,
                category=excluded.category,
                city=excluded.city,
                phone=excluded.phone,
                images_json=excluded.images_json,
                avito_item_id=excluded.avito_item_id,
                category_slug=excluded.category_slug,
                category_path=excluded.category_path,
                extra_fields_json=excluded.extra_fields_json,
                in_feed=excluded.in_feed,
                status=excluded.status
            """,
            (
                item.ad_id,
                item.title,
                item.description,
                item.price,
                item.category,
                item.city,
                item.phone,
                json.dumps(item.images, ensure_ascii=False),
                item.avito_item_id,
                item.category_slug,
                item.category_path,
                json.dumps(item.extra_fields, ensure_ascii=False),
                1 if item.in_feed else 0,
                item.status,
                item.created_at or datetime.now().isoformat(timespec="seconds"),
            ),
        )


def list_feed_items(*, in_feed_only: bool = False) -> list[DraftListing]:
    query = "SELECT * FROM feed_items"
    if in_feed_only:
        query += " WHERE in_feed = 1 AND status != 'archived'"
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_item(row) for row in rows]


def get_feed_item_by_avito_id(avito_item_id: int) -> DraftListing | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM feed_items WHERE avito_item_id = ?",
            (avito_item_id,),
        ).fetchone()
    return _row_to_item(row) if row else None


def archive_feed_item(ad_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE feed_items SET in_feed = 0, status = 'archived' WHERE ad_id = ?",
            (ad_id,),
        )


def link_avito_id(ad_id: str, avito_item_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE feed_items SET avito_item_id = ?, status = 'published' WHERE ad_id = ?",
            (avito_item_id, ad_id),
        )


def save_stats_snapshot(snapshot: StatsSnapshot) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO stats_snapshots (
                item_id, period_label, views, contacts, favorites, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id, period_label) DO UPDATE SET
                views=excluded.views,
                contacts=excluded.contacts,
                favorites=excluded.favorites,
                fetched_at=excluded.fetched_at
            """,
            (
                snapshot.item_id,
                snapshot.period_label,
                snapshot.views,
                snapshot.contacts,
                snapshot.favorites,
                snapshot.fetched_at,
            ),
        )


def get_stats_snapshot(item_id: int, period_label: str) -> StatsSnapshot | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM stats_snapshots WHERE item_id = ? AND period_label = ?",
            (item_id, period_label),
        ).fetchone()
    return _row_to_stats(row) if row else None


def _row_to_item(row: sqlite3.Row) -> DraftListing:
    extra_raw = row["extra_fields_json"] if "extra_fields_json" in row.keys() else "{}"
    slug_raw = row["category_slug"] if "category_slug" in row.keys() else ""
    path_raw = row["category_path"] if "category_path" in row.keys() else ""
    return DraftListing(
        ad_id=row["ad_id"],
        title=row["title"],
        description=row["description"],
        price=row["price"],
        category=row["category"],
        city=row["city"],
        phone=row["phone"],
        images=json.loads(row["images_json"]),
        avito_item_id=row["avito_item_id"],
        category_slug=slug_raw or "",
        category_path=path_raw or "",
        extra_fields=json.loads(extra_raw or "{}"),
        in_feed=bool(row["in_feed"]),
        status=row["status"],
        created_at=row["created_at"],
    )


def _row_to_stats(row: sqlite3.Row) -> StatsSnapshot:
    return StatsSnapshot(
        item_id=row["item_id"],
        period_label=row["period_label"],
        views=row["views"],
        contacts=row["contacts"],
        favorites=row["favorites"],
        fetched_at=row["fetched_at"],
    )
