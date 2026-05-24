"""Обёртка над avito-py для операций приложения."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from avito import AvitoClient
from avito.ads.models import Listing, ListingStats
from avito.core.exceptions import AvitoError

from app.autoload_api import fetch_category_fields_raw, fetch_category_tree_raw
from app.avito_client_factory import create_avito_client
from app.category_templates import (
    CategoryFieldsBundle,
    apply_template_fields,
    get_category_option,
    get_template_categories,
)
from app.category_utils import (
    AutoloadFeedField,
    CategoryMeta,
    build_category_meta,
    find_category_option,
    flatten_category_tree,
    parse_category_fields,
)
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
from app.stats_api import fetch_item_stats
from app.logging_setup import get_logger

logger = get_logger("service")
STATS_BATCH_SIZE = 20


def parse_comma_separated(raw: str) -> list[str]:
    """Разбивает строку по запятым, убирает пустые элементы."""
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class ListingWithStats:
    listing: Listing
    category_display: str
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
    added_count: int = 0
    cities: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    imported: int
    skipped: int


@dataclass(frozen=True)
class WalletBalance:
    real: float
    bonus: float
    total: float


@dataclass(frozen=True)
class PublishQuote:
    balance: WalletBalance
    cost_per_listing: float
    listings_count: int
    category_slug: str = ""

    @property
    def total_cost(self) -> float:
        return round(self.cost_per_listing * self.listings_count, 2)

    @property
    def can_afford(self) -> bool:
        return self.balance.total >= self.total_cost


class AvitoService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        apply_config_to_env(config)

    def _client(self) -> AvitoClient:
        return create_avito_client(self.config)

    def fetch_balance(self) -> WalletBalance:
        logger.info("Запрос баланса кошелька Avito")
        with self._client() as client:
            user_id = self.resolve_user_id(client)
            account_balance = client.account(user_id=user_id).get_balance()
            real = float(account_balance.real or 0)
            bonus = float(account_balance.bonus or 0)
            total = float(account_balance.total if account_balance.total is not None else real + bonus)
            balance = WalletBalance(real=real, bonus=bonus, total=total)
            logger.info(
                "Баланс получен: real=%.2f bonus=%.2f total=%.2f",
                balance.real,
                balance.bonus,
                balance.total,
            )
            return balance

    def resolve_publish_cost_per_listing(self, category_slug: str) -> float:
        if category_slug and self.config.category_publish_costs:
            cached = self.config.category_publish_costs.get(category_slug)
            if cached and cached > 0:
                return float(cached)
        if self.config.publish_cost_per_listing > 0:
            return float(self.config.publish_cost_per_listing)
        raise AvitoError(
            "Не задана ориентировочная стоимость размещения. "
            "Укажите её на вкладке «Настройки» (поле «Стоимость размещения, ₽»)."
        )

    def build_publish_quote(self, *, listings_count: int, category_slug: str) -> PublishQuote:
        if listings_count <= 0:
            raise AvitoError("Нет объявлений для публикации.")
        balance = self.fetch_balance()
        cost_per_listing = self.resolve_publish_cost_per_listing(category_slug)
        quote = PublishQuote(
            balance=balance,
            cost_per_listing=cost_per_listing,
            listings_count=listings_count,
            category_slug=category_slug,
        )
        logger.info(
            "Проверка публикации: balance=%.2f cost=%.2f listings=%s affordable=%s",
            quote.balance.total,
            quote.total_cost,
            listings_count,
            quote.can_afford,
        )
        return quote

    def remember_category_publish_cost(self, category_slug: str, cost: float) -> None:
        if not category_slug or cost <= 0:
            return
        costs = dict(self.config.category_publish_costs or {})
        costs[category_slug] = round(cost, 2)
        self.config.category_publish_costs = costs

    def resolve_user_id(self, client: AvitoClient) -> int:
        if self.config.user_id:
            logger.debug("user_id из конфигурации: %s", self.config.user_id)
            return self.config.user_id
        started = time.perf_counter()
        logger.info("Запрос профиля account().get_self()")
        profile = client.account().get_self()
        elapsed = time.perf_counter() - started
        logger.info(
            "Профиль получен за %.2f с: user_id=%s name=%s",
            elapsed,
            profile.user_id,
            profile.name,
        )
        if profile.user_id is None:
            raise AvitoError("Не удалось определить user_id.")
        return profile.user_id

    def fetch_active_listings(self) -> list[Listing]:
        started = time.perf_counter()
        logger.info("Загрузка активных объявлений (status=active)")
        with self._client() as client:
            user_id = self.resolve_user_id(client)
            list_started = time.perf_counter()
            items = client.ad(user_id=user_id).list(status="active", page_size=100)
            listings = list(items.materialize())
            logger.info(
                "Объявления загружены за %.2f с: count=%s user_id=%s",
                time.perf_counter() - list_started,
                len(listings),
                user_id,
            )
        logger.info(
            "fetch_active_listings завершён за %.2f с, всего %s объявлений",
            time.perf_counter() - started,
            len(listings),
        )
        return listings

    def fetch_category_tree(self) -> list[tuple[str, str]]:
        categories = get_template_categories()
        logger.info("Категорий из шаблона: %s", len(categories))
        if categories:
            return categories
        logger.info("Шаблон пуст — загрузка полного дерева автозагрузки")
        raw_nodes = fetch_category_tree_raw(self.config)
        return flatten_category_tree(raw_nodes)

    def fetch_category_fields(self, node_slug: str) -> CategoryFieldsBundle:
        logger.info("Загрузка полей категории: %s", node_slug)
        raw_fields = fetch_category_fields_raw(self.config, node_slug)
        api_fields = parse_category_fields(raw_fields)
        bundle = apply_template_fields(node_slug, api_fields)
        logger.info(
            "Полей для UI: %s, auto: %s",
            len(bundle.fields),
            len(bundle.auto_fields),
        )
        return bundle

    def resolve_category_meta(self, slug: str) -> CategoryMeta:
        option = get_category_option(slug)
        if option is None:
            raw_nodes = fetch_category_tree_raw(self.config)
            option = find_category_option(raw_nodes, slug)
        if option is None:
            raise AvitoError(f"Категория не найдена: {slug}")
        fields = parse_category_fields(fetch_category_fields_raw(self.config, slug))
        return build_category_meta(option, fields)

    def fetch_stats_for_items(
        self,
        item_ids: list[int],
        *,
        period_days: int,
    ) -> tuple[dict[int, ListingStats], dict[int, ListingStats]]:
        if not item_ids:
            logger.info("Статистика: список item_ids пуст, пропуск")
            return {}, {}

        today = date.today()
        current_end = today
        current_start = today - timedelta(days=period_days - 1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=period_days - 1)

        logger.info(
            "Статистика для %s объявлений, период %s дней: текущий %s..%s, прошлый %s..%s",
            len(item_ids),
            period_days,
            current_start,
            current_end,
            previous_start,
            previous_end,
        )

        current_map: dict[int, ListingStats] = {}
        previous_map: dict[int, ListingStats] = {}
        batches = [
            item_ids[i : i + STATS_BATCH_SIZE]
            for i in range(0, len(item_ids), STATS_BATCH_SIZE)
        ]
        logger.info("Статистика: %s пакет(ов) по до %s ID", len(batches), STATS_BATCH_SIZE)

        with self._client() as client:
            user_id = self.resolve_user_id(client)

            for index, batch in enumerate(batches, start=1):
                batch_started = time.perf_counter()
                logger.info("Пакет %s/%s: item_ids=%s", index, len(batches), batch)

                cur_items = fetch_item_stats(
                    self.config,
                    user_id=user_id,
                    date_from=current_start.isoformat(),
                    date_to=current_end.isoformat(),
                    item_ids=batch,
                )
                prev_items = fetch_item_stats(
                    self.config,
                    user_id=user_id,
                    date_from=previous_start.isoformat(),
                    date_to=previous_end.isoformat(),
                    item_ids=batch,
                )

                for stat in cur_items:
                    if stat.item_id is not None:
                        current_map[stat.item_id] = stat
                for stat in prev_items:
                    if stat.item_id is not None:
                        previous_map[stat.item_id] = stat

                logger.info(
                    "Пакет %s/%s готов за %.2f с (ответ: current=%s, previous=%s)",
                    index,
                    len(batches),
                    time.perf_counter() - batch_started,
                    len(cur_items),
                    len(prev_items),
                )

        logger.info(
            "Статистика собрана: current_map=%s previous_map=%s",
            len(current_map),
            len(previous_map),
        )
        return current_map, previous_map

    def _feed_by_avito_id(self) -> dict[int, str]:
        return {
            item.avito_item_id: item.ad_id
            for item in list_feed_items()
            if item.avito_item_id is not None
        }

    def _feed_items_by_avito_id(self) -> dict[int, DraftListing]:
        return {
            item.avito_item_id: item
            for item in list_feed_items()
            if item.avito_item_id is not None
        }

    def _category_display(
        self,
        listing: Listing,
        feed_item: DraftListing | None,
    ) -> str:
        if feed_item and feed_item.category_path:
            return feed_item.category_path
        if feed_item and feed_item.category_slug:
            try:
                return self.resolve_category_meta(feed_item.category_slug).path
            except AvitoError:
                pass
        if self.config.default_category_path:
            return self.config.default_category_path
        if listing.category:
            return f"{listing.category} (без подкатегории — укажите категорию в настройках)"
        return "—"

    def build_preview_rows(self, listings: list[Listing]) -> list[ListingWithStats]:
        feed_by_avito_id = self._feed_by_avito_id()
        feed_items = self._feed_items_by_avito_id()
        result: list[ListingWithStats] = []
        for listing in listings:
            item_id = listing.item_id
            if item_id is None:
                continue
            feed_ad_id = feed_by_avito_id.get(item_id)
            result.append(
                ListingWithStats(
                    listing=listing,
                    category_display=self._category_display(
                        listing,
                        feed_items.get(item_id),
                    ),
                    current_views=0,
                    previous_views=0,
                    current_contacts=0,
                    views_delta_pct=None,
                    should_archive=False,
                    in_local_feed=feed_ad_id is not None,
                    feed_ad_id=feed_ad_id,
                )
            )
        logger.info("Превью объявлений без статистики: %s", len(result))
        return result

    def complete_listings_with_stats(self, listings: list[Listing]) -> list[ListingWithStats]:
        total_started = time.perf_counter()
        item_ids = [item.item_id for item in listings if item.item_id is not None]
        current_map: dict[int, ListingStats] = {}
        previous_map: dict[int, ListingStats] = {}

        try:
            current_map, previous_map = self.fetch_stats_for_items(
                item_ids,
                period_days=self.config.stats_period_days,
            )
        except AvitoError as exc:
            logger.warning("Статистика недоступна: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ошибка статистики: %s", exc)

        feed_by_avito_id = self._feed_by_avito_id()
        feed_items = self._feed_items_by_avito_id()
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
                    category_display=self._category_display(
                        listing,
                        feed_items.get(item_id),
                    ),
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
        flagged = sum(1 for row in result if row.should_archive)
        logger.info(
            "complete_listings_with_stats за %.2f с | объявлений=%s | просадка=%s",
            time.perf_counter() - total_started,
            len(result),
            flagged,
        )
        return result

    def analyze_listings(self) -> list[ListingWithStats]:
        logger.info("=== analyze_listings: старт ===")
        listings = self.fetch_active_listings()
        if not listings:
            logger.warning("API вернул 0 активных объявлений")
            return []
        return self.complete_listings_with_stats(listings)

    def import_listings_to_feed(self, listings: list[Listing]) -> ImportResult:
        logger.info("Импорт в фид: %s объявлений", len(listings))
        if not self.config.default_category_slug:
            raise AvitoError(
                "Укажите категорию автозагрузки по умолчанию на вкладке «Новое объявление» "
                "и сохраните настройки. Нужен полный путь, например: "
                "Услуги / Предложение услуг / Строительство / Строительство домов под ключ."
            )

        category_meta = self.resolve_category_meta(self.config.default_category_slug)
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
                category=category_meta.category,
                category_path=category_meta.path,
                category_slug=category_meta.slug,
                city=listing.city or "",
                phone=self.config.contact_phone,
                images=[],
                avito_item_id=item_id,
                extra_fields=dict(category_meta.auto_fields),
                in_feed=True,
                status="published",
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            save_feed_item(item)
            imported += 1

        logger.info("Импорт завершён: imported=%s skipped=%s", imported, skipped)
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
        logger.info("Публикация фида: %s объявлений", len(items))
        feed_path = write_feed_file(items, FEED_PATH)

        report_id: int | None = None
        if self.config.feed_public_url:
            with self._client() as client:
                upload = client.autoload_profile().upload_by_url(url=self.config.feed_public_url)
                report_id = upload.report_id
                logger.info("Автозагрузка запущена, report_id=%s", report_id)

        return PublishResult(
            feed_path=feed_path,
            report_id=report_id,
            items_count=len(items),
            added_count=0,
        )

    def add_listings_to_feed(self, items: list[DraftListing]) -> PublishResult:
        if not items:
            raise AvitoError("Нет объявлений для публикации.")
        logger.info("Добавление в фид: %s объявлений", len(items))
        for item in items:
            save_feed_item(item)
        result = self.publish_feed()
        return PublishResult(
            feed_path=result.feed_path,
            report_id=result.report_id,
            items_count=result.items_count,
            added_count=len(items),
            cities=[item.city for item in items],
        )

    def add_listing_to_feed(self, item: DraftListing) -> PublishResult:
        return self.add_listings_to_feed([item])

    def archive_listing(self, *, feed_ad_id: str | None, avito_item_id: int | None) -> PublishResult:
        logger.info("Снятие объявления: feed_ad_id=%s avito_item_id=%s", feed_ad_id, avito_item_id)
        if feed_ad_id:
            archive_feed_item(feed_ad_id)
            return self.publish_feed()

        raise AvitoError(
            "Объявление не из фида автозагрузки — снять через API нельзя. "
            "Снимите вручную в личном кабинете Avito."
        )
