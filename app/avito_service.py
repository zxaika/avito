"""Обёртка над avito-py для операций приложения."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from avito import AvitoClient
from avito.ads.models import AutoloadField, Listing, ListingStats
from avito.core.exceptions import AvitoError

from app.category_utils import flatten_category_tree
from app.config import AppConfig, FEED_PATH, apply_config_to_env
from app.database import (
    DraftListing,
    StatsSnapshot,
    archive_feed_item,
    get_feed_item_by_avito_id,
    list_feed_items,
    save_feed_item,
    save_stats_snapshot,
)
from app.feed_builder import write_feed_file


@dataclass
class ListingWithStats:
    listing: Listing
    current_views: int
    previous_views: int
    current_contacts: int
    views_delta_pct: float | None
    should_archive: bool
    in_local_feed: bool
    feed_ad_id: str | None


@dataclass
class PublishResult:
    feed_path: Path
    report_id: int | None
    items_count: int


@dataclass
class ImportResult:
    imported: int
    skipped: int


class AvitoService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        apply_config_to_env(config)

    def _client(self) -> AvitoClient:
        return AvitoClient(
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
        )

    def resolve_user_id(self, client: AvitoClient) -> int:
        if self.config.user_id:
            return self.config.user_id
        profile = client.account().get_self()
        if profile.id is None:
            raise AvitoError("Не удалось определить user_id.")
        return profile.id

    def fetch_active_listings(self) -> list[Listing]:
        with self._client() as client:
            user_id = self.resolve_user_id(client)
            items = client.ad(user_id=user_id).list(status="active", page_size=100)
            return items.materialize()

    def fetch_category_tree(self) -> list[tuple[str, str]]:
        with self._client() as client:
            tree = client.autoload_profile().get_tree()
            return flatten_category_tree(tree.items)

    def fetch_category_fields(self, node_slug: str) -> list[AutoloadField]:
        with self._client() as client:
            result = client.autoload_profile().get_node_fields(node_slug=node_slug)
            return result.items

    def fetch_stats_for_items(
        self,
        item_ids: list[int],
        *,
        period_days: int,
    ) -> tuple[dict[int, ListingStats], dict[int, ListingStats]]:
        if not item_ids:
            return {}, {}

        today = date.today()
        current_end = today
        current_start = today - timedelta(days=period_days - 1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)

        with self._client() as client:
            user_id = self.resolve_user_id(client)
            stats_api = client.ad_stats(user_id=user_id)

            current = stats_api.get_item_stats(
                date_from=current_start.isoformat(),
                date_to=current_end.isoformat(),
                item_ids=item_ids,
            )
            previous = stats_api.get_item_stats(
                date_from=previous_start.isoformat(),
                date_to=previous_end.isoformat(),
                item_ids=item_ids,
            )

        current_map = {s.item_id: s for s in current.items if s.item_id is not None}
        previous_map = {s.item_id: s for s in previous.items if s.item_id is not None}
        return current_map, previous_map

    def analyze_listings(self) -> list[ListingWithStats]:
        listings = self.fetch_active_listings()
        item_ids = [item.item_id for item in listings if item.item_id is not None]
        current_map, previous_map = self.fetch_stats_for_items(
            item_ids,
            period_days=self.config.stats_period_days,
        )

        feed_items = list_feed_items()
        feed_by_avito_id = {
            item.avito_item_id: item.ad_id
            for item in feed_items
            if item.avito_item_id is not None
        }

        now = date.today().isoformat()
        period_label = f"current_{self.config.stats_period_days}d"
        prev_label = f"previous_{self.config.stats_period_days}d"

        result: list[ListingWithStats] = []
        for listing in listings:
            item_id = listing.item_id
            if item_id is None:
                continue

            cur = current_map.get(item_id)
            prev = previous_map.get(item_id)
            current_views = cur.views or 0 if cur else 0
            previous_views = prev.views or 0 if prev else 0
            current_contacts = cur.contacts or 0 if cur else 0

            save_stats_snapshot(
                StatsSnapshot(
                    item_id=item_id,
                    period_label=period_label,
                    views=current_views,
                    contacts=current_contacts,
                    favorites=(cur.favorites or 0) if cur else 0,
                    fetched_at=now,
                )
            )
            save_stats_snapshot(
                StatsSnapshot(
                    item_id=item_id,
                    period_label=prev_label,
                    views=previous_views,
                    contacts=(prev.contacts or 0) if prev else 0,
                    favorites=(prev.favorites or 0) if prev else 0,
                    fetched_at=now,
                )
            )

            delta_pct: float | None = None
            if previous_views > 0:
                delta_pct = round((current_views - previous_views) / previous_views * 100, 1)

            should_archive = self._should_archive(current_views, previous_views, delta_pct)
            feed_ad_id = feed_by_avito_id.get(item_id)

            result.append(
                ListingWithStats(
                    listing=listing,
                    current_views=current_views,
                    previous_views=previous_views,
                    current_contacts=current_contacts,
                    views_delta_pct=delta_pct,
                    should_archive=should_archive,
                    in_local_feed=feed_ad_id is not None,
                    feed_ad_id=feed_ad_id,
                )
            )

        result.sort(key=lambda row: (not row.should_archive, row.current_views))
        return result

    def import_listings_to_feed(self, listings: list[Listing]) -> ImportResult:
        imported = 0
        skipped = 0

        for listing in listings:
            item_id = listing.item_id
            if item_id is None:
                skipped += 1
                continue
            if get_feed_item_by_avito_id(item_id) is not None:
                skipped += 1
                continue

            price = int(listing.price) if listing.price is not None else 0
            item = DraftListing(
                ad_id=f"avito-{item_id}",
                title=listing.title or f"Объявление {item_id}",
                description=listing.description or listing.title or "",
                price=price,
                category=listing.category or "",
                category_slug=self.config.default_category_slug,
                city=listing.city or "",
                phone=self.config.contact_phone,
                images=[],
                avito_item_id=item_id,
                extra_fields={},
                in_feed=True,
                status="published",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            save_feed_item(item)
            imported += 1

        return ImportResult(imported=imported, skipped=skipped)

    def _should_archive(
        self,
        current_views: int,
        previous_views: int,
        delta_pct: float | None,
    ) -> bool:
        threshold = self.config.drop_percent_threshold
        baseline = self.config.min_views_baseline

        if previous_views >= baseline and delta_pct is not None and delta_pct <= -threshold:
            return True
        if previous_views >= baseline and current_views == 0:
            return True
        return False

    def publish_feed(self) -> PublishResult:
        items = [item for item in list_feed_items(in_feed_only=True)]
        feed_path = write_feed_file(items, FEED_PATH)

        report_id: int | None = None
        if self.config.feed_public_url:
            with self._client() as client:
                upload = client.autoload_profile().upload_by_url(url=self.config.feed_public_url)
                report_id = upload.report_id

        return PublishResult(
            feed_path=feed_path,
            report_id=report_id,
            items_count=len(items),
        )

    def add_listing_to_feed(self, item: DraftListing) -> PublishResult:
        save_feed_item(item)
        return self.publish_feed()

    def archive_listing(self, *, feed_ad_id: str | None, avito_item_id: int | None) -> PublishResult:
        if feed_ad_id:
            archive_feed_item(feed_ad_id)
            return self.publish_feed()

        raise AvitoError(
            "Объявление не из фида автозагрузки — снять через API нельзя. "
            "Снимите вручную в личном кабинете Avito."
        )
