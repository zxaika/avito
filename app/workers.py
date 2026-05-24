"""Фоновые задачи для PyQt5 (API не блокирует UI)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from avito.ads.models import AutoloadField, Listing

from app.avito_service import AvitoService, ListingWithStats, PublishResult
from app.config import AppConfig
from app.database import DraftListing
from app.excel_export import export_listings_report


class AnalyzeWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            rows = AvitoService(self._config).analyze_listings()
            self.finished.emit(rows)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class PublishWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, item: DraftListing | None = None) -> None:
        super().__init__()
        self._config = config
        self._item = item

    def run(self) -> None:
        try:
            service = AvitoService(self._config)
            if self._item:
                result = service.add_listing_to_feed(self._item)
            else:
                result = service.publish_feed()
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


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
        try:
            result = AvitoService(self._config).archive_listing(
                feed_ad_id=self._request.feed_ad_id,
                avito_item_id=self._request.avito_item_id,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class ImportWorker(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, listings: list[Listing]) -> None:
        super().__init__()
        self._config = config
        self._listings = listings

    def run(self) -> None:
        try:
            result = AvitoService(self._config).import_listings_to_feed(self._listings)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class CategoryTreeWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            categories = AvitoService(self._config).fetch_category_tree()
            self.finished.emit(categories)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class CategoryFieldsWorker(QObject):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config: AppConfig, node_slug: str) -> None:
        super().__init__()
        self._config = config
        self._node_slug = node_slug

    def run(self) -> None:
        try:
            fields = AvitoService(self._config).fetch_category_fields(self._node_slug)
            self.finished.emit(fields)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


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
            self.error.emit(str(exc))
