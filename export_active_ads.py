"""Выгрузка всех активных объявлений Avito в CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path

from avito import AvitoClient
from avito.ads.models import Listing
from avito.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    AvitoError,
    ConfigurationError,
    RateLimitError,
)

CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("item_id", "ID объявления"),
    ("user_id", "ID пользователя"),
    ("title", "Заголовок"),
    ("description", "Описание"),
    ("status", "Статус"),
    ("price", "Цена"),
    ("url", "Ссылка"),
    ("category", "Категория"),
    ("city", "Город"),
    ("published_at", "Дата публикации"),
    ("updated_at", "Дата обновления"),
    ("is_moderated", "На модерации"),
    ("is_visible", "Видимо"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Выгрузить все активные объявления Avito в CSV.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Путь к CSV-файлу (по умолчанию: active_ads_YYYY-MM-DD_HH-MM-SS.csv)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="ID пользователя Avito (если не задан — из AVITO_USER_ID или профиля)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Размер страницы при загрузке (по умолчанию: 100)",
    )
    return parser.parse_args()


def default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path(f"active_ads_{stamp}.csv")


def format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def listing_to_row(listing: Listing) -> dict[str, str]:
    data = listing.to_dict()
    return {
        field: format_cell(data.get(field))
        for field, _ in CSV_COLUMNS
    }


def resolve_user_id(avito: AvitoClient, explicit_user_id: int | None) -> int:
    if explicit_user_id is not None:
        return explicit_user_id

    profile = avito.account().get_self()
    if profile.id is None:
        raise ConfigurationError(
            "Не удалось определить user_id. Укажите --user-id или AVITO_USER_ID в .env."
        )
    return profile.id


def export_active_ads(
    *,
    output_path: Path,
    user_id: int | None,
    page_size: int,
) -> int:
    with AvitoClient.from_env() as avito:
        resolved_user_id = resolve_user_id(avito, user_id)
        print(f"Загружаю активные объявления для user_id={resolved_user_id}...")

        listings = avito.ad(user_id=resolved_user_id).list(
            status="active",
            page_size=page_size,
        )
        items = listings.materialize()
        print(f"Получено объявлений: {len(items)}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=[field for field, _ in CSV_COLUMNS],
                extrasaction="ignore",
            )
            writer.writerow({field: title for field, title in CSV_COLUMNS})
            for listing in items:
                writer.writerow(listing_to_row(listing))

    print(f"CSV сохранён: {output_path.resolve()}")
    return len(items)


def main() -> None:
    args = parse_args()
    output_path = args.output or default_output_path()

    try:
        count = export_active_ads(
            output_path=output_path,
            user_id=args.user_id,
            page_size=args.page_size,
        )
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        print("Проверьте .env: AVITO_CLIENT_ID, AVITO_CLIENT_SECRET, AVITO_USER_ID.", file=sys.stderr)
        sys.exit(1)
    except AuthenticationError as exc:
        print(f"Ошибка авторизации (401): {exc}", file=sys.stderr)
        sys.exit(1)
    except AuthorizationError as exc:
        print(f"Нет доступа (403): {exc}", file=sys.stderr)
        print("Проверьте права приложения в кабинете Avito API.", file=sys.stderr)
        sys.exit(1)
    except RateLimitError as exc:
        print(f"Лимит запросов (429): {exc}", file=sys.stderr)
        if exc.retry_after:
            print(f"Повторите через {exc.retry_after} сек.", file=sys.stderr)
        sys.exit(1)
    except AvitoError as exc:
        print(f"Ошибка Avito API: {exc}", file=sys.stderr)
        sys.exit(1)

    if count == 0:
        print("Активных объявлений не найдено — CSV создан с заголовками.")


if __name__ == "__main__":
    main()
