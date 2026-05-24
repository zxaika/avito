"""Фоновые задачи для PyQt5 (API не блокирует UI)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from avito.ads.models import Listing

from app.avito_service import AvitoService, ListingWithStats, PublishQuote, PublishResult, WalletBalance
from app.config import AppConfig, load_config
from app.database import DraftListing
from app.excel_export import export_listings_report
from app.logging_setup import get_logger, log_exception

logger = get_logger("worker")


class AnalyzeWorker(QObject):
    partial = pyqtSignal(list)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        config = load_config()
        logger.info(
            "AnalyzeWorker: старт (client_id=%s, user_id=%s)",
            "есть" if config.client_id else "НЕТ",
            config.user_id,
        )
        try:
            service = AvitoService(config)
            listings = service.fetch_active_listings()
            if not listings:
                logger.warning("AnalyzeWorker: API вернул 0 объявлений")
                self.finished.emit([])
                return

            preview = service.build_preview_rows(listings)
            logger.info("AnalyzeWorker: превью %s объявлений", len(preview))
            self.partial.emit(preview)

            rows = service.complete_listings_with_stats(listings)
            logger.info("AnalyzeWorker: успех, rows=%s", len(rows))
            self.finished.emit(rows)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка загрузки объявлений", exc))


class BalanceWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            balance = AvitoService(load_config()).fetch_balance()
            self.finished.emit(balance)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка загрузки баланса", exc))


class PublishPrecheckWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, listings_count: int, category_slug: str) -> None:
        super().__init__()
        self._config = config
        self._listings_count = listings_count
        self._category_slug = category_slug

    def run(self) -> None:
        logger.info(
            "PublishPrecheckWorker: listings=%s category=%s",
            self._listings_count,
            self._category_slug,
        )
        try:
            quote = AvitoService(load_config()).build_publish_quote(
                listings_count=self._listings_count,
                category_slug=self._category_slug,
            )
            self.finished.emit(quote)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка проверки баланса", exc))


class PublishWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        items: DraftListing | list[DraftListing] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._items = items

    def run(self) -> None:
        logger.info("PublishWorker: старт")
        try:
            service = AvitoService(load_config())
            if isinstance(self._items, list):
                result = service.add_listings_to_feed(self._items)
            elif self._items is not None:
                result = service.add_listing_to_feed(self._items)
            else:
                result = service.publish_feed()
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка публикации", exc))


@dataclass
class ArchiveRequest:
    feed_ad_id: str | None
    avito_item_id: int | None


class ArchiveWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, request: ArchiveRequest) -> None:
        super().__init__()
        self._config = config
        self._request = request

    def run(self) -> None:
        logger.info("ArchiveWorker: старт feed_ad_id=%s", self._request.feed_ad_id)
        try:
            result = AvitoService(load_config()).archive_listing(
                feed_ad_id=self._request.feed_ad_id,
                avito_item_id=self._request.avito_item_id,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка снятия объявления", exc))


class ImportWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, listings: list[Listing]) -> None:
        super().__init__()
        self._config = config
        self._listings = listings

    def run(self) -> None:
        logger.info("ImportWorker: старт count=%s", len(self._listings))
        try:
            result = AvitoService(load_config()).import_listings_to_feed(self._listings)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка импорта в фид", exc))


class CategoryTreeWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            categories = AvitoService(load_config()).fetch_category_tree()
            self.finished.emit(categories)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка загрузки категорий", exc))


class CategoryFieldsWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, node_slug: str) -> None:
        super().__init__()
        self._config = config
        self._node_slug = node_slug

    def run(self) -> None:
        try:
            fields = AvitoService(load_config()).fetch_category_fields(self._node_slug)
            self.finished.emit(fields)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка загрузки полей категории", exc))


class ExportExcelWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, rows: list[ListingWithStats], path: Path) -> None:
        super().__init__()
        self._rows = rows
        self._path = path

    def run(self) -> None:
        try:
            saved = export_listings_report(self._rows, self._path)
            self.finished.emit(str(saved))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(log_exception(logger, "Ошибка экспорта Excel", exc))
