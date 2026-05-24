"""Главное окно Avito Desktop Manager."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QTimer, QThread, QObject
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.avito_service import (
    ImportResult,
    ListingWithStats,
    PublishQuote,
    PublishResult,
    WalletBalance,
    parse_comma_separated,
)
from app.config import AppConfig, load_config, save_config
from app.database import DraftListing, init_db
from app.logging_setup import get_log_file, get_logger
from app.workers import (
    AnalyzeWorker,
    ArchiveRequest,
    ArchiveWorker,
    BalanceWorker,
    CategoryFieldsWorker,
    CategoryTreeWorker,
    ExportExcelWorker,
    ImportWorker,
    PublishPrecheckWorker,
    PublishWorker,
)

SKIP_FIELD_TAGS = {
    "title",
    "description",
    "price",
    "address",
    "contactphone",
    "images",
    "category",
    "id",
    "avitoid",
    "allowemail",
}

logger = get_logger("ui")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        init_db()
        self.config = load_config()
        self._threads: list[QThread] = []
        self._workers: list[QObject] = []
        self._rows: list[ListingWithStats] = []
        self._category_field_inputs: dict[str, QLineEdit] = {}
        self._category_auto_fields: dict[str, str] = {}
        self._pending_publish_items: list[DraftListing] = []
        self._current_balance: WalletBalance | None = None
        self._balance_show_error_dialog = True

        logger.info(
            "Конфиг: client_id=%s user_id=%s",
            "есть" if self.config.client_id else "НЕТ",
            self.config.user_id,
        )

        self.setWindowTitle("Avito Desktop Manager")
        self.resize(1150, 760)

        self.tabs = QTabWidget()

        wallet_row = QHBoxLayout()
        self.balance_label = QLabel("Кошелёк Avito: —")
        self.balance_label.setWordWrap(True)
        self.refresh_balance_btn = QPushButton("Обновить баланс")
        self.refresh_balance_btn.clicked.connect(self._refresh_balance)
        wallet_row.addWidget(self.balance_label, stretch=1)
        wallet_row.addWidget(self.refresh_balance_btn)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.addLayout(wallet_row)
        container_layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self._build_ads_tab()
        self._build_create_tab()
        self._build_settings_tab()

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"Лог: {get_log_file()}")

        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.timeout.connect(self._on_scheduler_tick)
        self._apply_scheduler()

        QTimer.singleShot(0, self._load_initial_data)

    def _load_initial_data(self) -> None:
        logger.info("Автозагрузка объявлений при старте приложения")
        if not self._has_api_credentials():
            logger.warning("API-ключи не заданы — автозагрузка пропущена")
            self.status.showMessage("Укажите API-ключи в настройках или .env")
            return
        self.status.showMessage("Загрузка объявлений...")
        self._refresh_balance(show_credentials_warning=False)
        self._load_categories(show_credentials_warning=False)
        self._start_analyze(show_credentials_warning=False)

    def _has_api_credentials(self) -> bool:
        return bool(self.config.client_id and self.config.client_secret)

    def _run_worker(
        self,
        worker: QObject,
        thread: QThread,
        *,
        on_finished,
        on_error=None,
        on_partial=None,
    ) -> None:
        """Запуск фоновой задачи; worker хранится в self._workers (иначе GC в PyQt)."""
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.started.connect(lambda: logger.debug("QThread started: %s", type(worker).__name__))
        if on_partial is not None and hasattr(worker, "partial"):
            worker.partial.connect(on_partial)  # type: ignore[attr-defined]
        worker.finished.connect(on_finished)
        if on_error is not None:
            worker.error.connect(on_error)  # type: ignore[attr-defined]
        worker.finished.connect(thread.quit)
        if on_error is not None:
            worker.error.connect(thread.quit)  # type: ignore[attr-defined]
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread, worker))
        self._workers.append(worker)
        self._threads.append(thread)
        thread.start()

    def _cleanup_thread(self, thread: QThread, worker: QObject) -> None:
        if thread in self._threads:
            self._threads.remove(thread)
        if worker in self._workers:
            self._workers.remove(worker)

    # ------------------------------------------------------------------ tabs

    def _build_ads_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QLabel(
            "Активные объявления и аналитика просмотров. "
            "Импорт в фид позволяет снимать объявления через автозагрузку."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить данные")
        self.refresh_btn.clicked.connect(self._start_analyze)
        self.import_btn = QPushButton("Импортировать в фид")
        self.import_btn.clicked.connect(self._import_selected)
        self.export_btn = QPushButton("Экспорт в Excel")
        self.export_btn.clicked.connect(self._export_excel)
        self.open_log_btn = QPushButton("Открыть лог")
        self.open_log_btn.clicked.connect(self._open_log_file)
        self.auto_archive_btn = QPushButton("Снять просевшие (из фида)")
        self.auto_archive_btn.clicked.connect(self._archive_flagged)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.open_log_btn)
        btn_row.addWidget(self.auto_archive_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.ads_table = QTableWidget(0, 10)
        self.ads_table.setHorizontalHeaderLabels(
            [
                "ID",
                "Категория",
                "Заголовок",
                "Цена",
                "Просмотры",
                "Было",
                "Δ %",
                "Статус",
                "В фиде",
                "Действие",
            ]
        )
        self.ads_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.ads_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.ads_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ads_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.ads_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.ads_table)

        self.tabs.addTab(widget, "Объявления")

    def _build_create_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        cat_row = QHBoxLayout()
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(400)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        load_cat_btn = QPushButton("Загрузить категории")
        self.load_cat_btn = load_cat_btn
        load_cat_btn.clicked.connect(self._load_categories)
        cat_row.addWidget(QLabel("Категория Avito:"))
        cat_row.addWidget(self.category_combo, stretch=1)
        cat_row.addWidget(load_cat_btn)
        layout.addLayout(cat_row)

        form_box = QGroupBox("Новое объявление (автозагрузка)")
        form = QFormLayout(form_box)

        self.title_input = QLineEdit()
        self.price_input = QSpinBox()
        self.price_input.setMaximum(999_999_999)
        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("Иваново, Кинешма, Шуя")
        self.phone_input = QLineEdit()
        self.phone_input.setText(self.config.contact_phone)
        self.images_input = QLineEdit()
        self.images_input.setPlaceholderText("URL фото через запятую")
        self.description_input = QTextEdit()
        self.description_input.setMinimumHeight(100)

        form.addRow("Заголовок", self.title_input)
        form.addRow("Цена, ₽", self.price_input)
        form.addRow("Города (через запятую)", self.city_input)
        form.addRow("Телефон", self.phone_input)
        form.addRow("Фото (URL)", self.images_input)
        form.addRow("Описание", self.description_input)
        layout.addWidget(form_box)

        self.category_fields_box = QGroupBox("Поля категории")
        self.category_fields_layout = QFormLayout(self.category_fields_box)
        scroll = QScrollArea()
        scroll.setWidget(self.category_fields_box)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(180)
        layout.addWidget(scroll)

        hint = QLabel(
            "Выберите конечную категорию в дереве (например: Услуги / Предложение услуг / "
            "Строительство / Строительство домов под ключ). Поля Category, ServiceType и "
            "ServiceSubtype подставятся автоматически. Несколько городов через запятую — "
            "будет создано отдельное объявление для каждого города."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        publish_btn = QPushButton("Опубликовать через автозагрузку")
        publish_btn.clicked.connect(self._start_publish)
        layout.addWidget(publish_btn)
        layout.addStretch()

        self.tabs.addTab(widget, "Новое объявление")

    def _build_settings_tab(self) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        api_box = QGroupBox("API Avito")
        api_form = QFormLayout(api_box)
        self.client_id_input = QLineEdit(self.config.client_id)
        self.client_secret_input = QLineEdit(self.config.client_secret)
        self.client_secret_input.setEchoMode(QLineEdit.Password)
        self.user_id_input = QLineEdit(
            str(self.config.user_id) if self.config.user_id else ""
        )
        api_form.addRow("Client ID", self.client_id_input)
        api_form.addRow("Client Secret", self.client_secret_input)
        api_form.addRow("User ID", self.user_id_input)

        feed_box = QGroupBox("Автозагрузка")
        feed_form = QFormLayout(feed_box)
        self.feed_url_input = QLineEdit(self.config.feed_public_url)
        self.feed_url_input.setPlaceholderText("https://example.com/autoload_feed.xml")
        self.contact_phone_setting = QLineEdit(self.config.contact_phone)
        self.default_category_label = QLabel(self.config.default_category_path or "—")
        self.default_category_label.setWordWrap(True)
        feed_form.addRow("Публичный URL фида", self.feed_url_input)
        feed_form.addRow("Телефон по умолчанию", self.contact_phone_setting)
        feed_form.addRow("Категория по умолчанию", self.default_category_label)

        wallet_box = QGroupBox("Кошелёк и публикация")
        wallet_form = QFormLayout(wallet_box)
        self.publish_cost_input = QSpinBox()
        self.publish_cost_input.setRange(0, 999_999)
        self.publish_cost_input.setSuffix(" ₽")
        self.publish_cost_input.setValue(int(self.config.publish_cost_per_listing))
        self.publish_cost_input.setToolTip(
            "Ориентировочная стоимость размещения одного объявления в одном городе. "
            "Avito не предоставляет API для точного расчёта до публикации."
        )
        wallet_form.addRow("Стоимость размещения, ₽", self.publish_cost_input)

        rules_box = QGroupBox("Правила снятия по просмотрам")
        rules_form = QFormLayout(rules_box)
        self.period_spin = QSpinBox()
        self.period_spin.setRange(1, 30)
        self.period_spin.setValue(self.config.stats_period_days)
        self.baseline_spin = QSpinBox()
        self.baseline_spin.setRange(0, 1000)
        self.baseline_spin.setValue(self.config.min_views_baseline)
        self.drop_spin = QSpinBox()
        self.drop_spin.setRange(1, 99)
        self.drop_spin.setValue(self.config.drop_percent_threshold)
        self.auto_archive_check = QCheckBox("Автоматически снимать при обновлении")
        self.auto_archive_check.setChecked(self.config.auto_archive_enabled)
        rules_form.addRow("Период анализа, дней", self.period_spin)
        rules_form.addRow("Мин. просмотров в прошлом периоде", self.baseline_spin)
        rules_form.addRow("Падение просмотров, %", self.drop_spin)
        rules_form.addRow("", self.auto_archive_check)

        sched_box = QGroupBox("Планировщик")
        sched_form = QFormLayout(sched_box)
        self.scheduler_check = QCheckBox("Автоматически проверять просмотры")
        self.scheduler_check.setChecked(self.config.scheduler_enabled)
        self.scheduler_interval_spin = QSpinBox()
        self.scheduler_interval_spin.setRange(5, 1440)
        self.scheduler_interval_spin.setValue(self.config.scheduler_interval_minutes)
        self.scheduler_interval_spin.setSuffix(" мин")
        sched_form.addRow("", self.scheduler_check)
        sched_form.addRow("Интервал проверки", self.scheduler_interval_spin)

        save_btn = QPushButton("Сохранить настройки")
        save_btn.clicked.connect(self._save_settings)

        layout.addWidget(api_box)
        layout.addWidget(feed_box)
        layout.addWidget(wallet_box)
        layout.addWidget(rules_box)
        layout.addWidget(sched_box)
        layout.addWidget(save_btn)
        layout.addStretch()

        self.tabs.addTab(widget, "Настройки")

    # ------------------------------------------------------------------ scheduler

    def _apply_scheduler(self) -> None:
        self._scheduler_timer.stop()
        if self.config.scheduler_enabled and self.config.scheduler_interval_minutes > 0:
            interval_ms = self.config.scheduler_interval_minutes * 60 * 1000
            self._scheduler_timer.start(interval_ms)

    def _on_scheduler_tick(self) -> None:
        if self.refresh_btn.isEnabled():
            self.status.showMessage("Планировщик: проверка просмотров...")
            self._start_analyze()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._scheduler_timer.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------ categories

    def _load_categories(self, *, show_credentials_warning: bool = True) -> None:
        if not self._validate_api_config(show_warning=show_credentials_warning):
            return
        self.load_cat_btn.setEnabled(False)
        self.status.showMessage("Загрузка категорий Avito...")
        worker = CategoryTreeWorker(self.config)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_categories_loaded,
            on_error=self._on_worker_error,
        )

    def _on_categories_loaded(self, categories: list) -> None:
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for slug, label in categories:
            self.category_combo.addItem(label, slug)
        if self.config.default_category_slug:
            index = self.category_combo.findData(self.config.default_category_slug)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
        if self.config.default_category_path:
            self.default_category_label.setText(self.config.default_category_path)
        self.category_combo.blockSignals(False)
        self.load_cat_btn.setEnabled(True)
        self.status.showMessage(f"Загружено категорий: {len(categories)}")
        if self.config.default_category_slug:
            self._load_category_fields(self.config.default_category_slug)

    def _on_category_changed(self, _index: int) -> None:
        slug = self.category_combo.currentData()
        if slug:
            self._load_category_fields(str(slug))

    def _load_category_fields(self, node_slug: str) -> None:
        worker = CategoryFieldsWorker(self.config, node_slug)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_category_fields_loaded,
            on_error=self._on_worker_error,
        )

    def _on_category_fields_loaded(self, fields: list) -> None:
        self._category_field_inputs.clear()
        self._category_auto_fields.clear()
        while self.category_fields_layout.rowCount():
            self.category_fields_layout.removeRow(0)

        for field in fields:
            tag = field.tag or ""
            if not tag:
                continue
            if field.auto_value:
                self._category_auto_fields[tag] = field.auto_value
                continue
            if tag.lower() in SKIP_FIELD_TAGS:
                continue
            label = field.label or tag
            if field.required:
                label += " *"
            if field.values:
                label += f" ({', '.join(field.values[:3])}{'…' if len(field.values) > 3 else ''})"
            input_widget = QLineEdit()
            if field.default:
                input_widget.setText(field.default)
            self._category_field_inputs[tag] = input_widget
            self.category_fields_layout.addRow(label, input_widget)

    # ------------------------------------------------------------------ wallet

    def _format_balance(self, balance: WalletBalance) -> str:
        parts = [f"Кошелёк Avito: {balance.real:,.2f} ₽".replace(",", " ")]
        if balance.bonus > 0:
            parts.append(f"бонусы: {balance.bonus:,.2f} ₽".replace(",", " "))
        parts.append(f"всего: {balance.total:,.2f} ₽".replace(",", " "))
        return " · ".join(parts)

    def _update_balance_label(self, balance: WalletBalance | None = None) -> None:
        if balance is not None:
            self._current_balance = balance
        if self._current_balance is None:
            self.balance_label.setText("Кошелёк Avito: —")
            return
        self.balance_label.setText(self._format_balance(self._current_balance))

    def _refresh_balance(self, *, show_credentials_warning: bool = True) -> None:
        if not self._validate_api_config(show_warning=show_credentials_warning):
            return
        self._balance_show_error_dialog = show_credentials_warning
        self.refresh_balance_btn.setEnabled(False)
        self.balance_label.setText("Кошелёк Avito: загрузка...")
        worker = BalanceWorker(self.config)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_balance_loaded,
            on_error=self._on_balance_error,
        )

    def _on_balance_loaded(self, balance: WalletBalance) -> None:
        self._update_balance_label(balance)
        self.refresh_balance_btn.setEnabled(True)
        logger.info("Баланс обновлён в UI: total=%.2f", balance.total)

    def _on_balance_error(self, message: str) -> None:
        self.refresh_balance_btn.setEnabled(True)
        self.balance_label.setText("Кошелёк Avito: ошибка загрузки")
        logger.error("Ошибка баланса в UI: %s", message.replace("\n", " | "))
        if self._balance_show_error_dialog:
            QMessageBox.warning(self, "Баланс", message)

    # ------------------------------------------------------------------ settings

    def _save_settings(self) -> None:
        user_id_raw = self.user_id_input.text().strip()
        slug = self.category_combo.currentData()
        category_path = self.category_combo.currentText().strip()
        self.config = AppConfig(
            client_id=self.client_id_input.text().strip(),
            client_secret=self.client_secret_input.text().strip(),
            user_id=int(user_id_raw) if user_id_raw.isdigit() else None,
            feed_public_url=self.feed_url_input.text().strip(),
            contact_phone=self.contact_phone_setting.text().strip(),
            default_category_slug=str(slug) if slug else self.config.default_category_slug,
            default_category_path=category_path if slug else self.config.default_category_path,
            stats_period_days=self.period_spin.value(),
            min_views_baseline=self.baseline_spin.value(),
            drop_percent_threshold=self.drop_spin.value(),
            auto_archive_enabled=self.auto_archive_check.isChecked(),
            scheduler_enabled=self.scheduler_check.isChecked(),
            scheduler_interval_minutes=self.scheduler_interval_spin.value(),
            publish_cost_per_listing=float(self.publish_cost_input.value()),
            category_publish_costs=self.config.category_publish_costs,
        )
        save_config(self.config)
        self.phone_input.setText(self.config.contact_phone)
        if self.config.default_category_path:
            self.default_category_label.setText(self.config.default_category_path)
        self._apply_scheduler()
        self.status.showMessage("Настройки сохранены", 3000)

    def _validate_api_config(self, *, show_warning: bool = True) -> bool:
        if self._has_api_credentials():
            return True
        if show_warning:
            QMessageBox.warning(
                self,
                "Настройки",
                "Укажите Client ID и Client Secret на вкладке «Настройки» или в файле .env.",
            )
        return False

    # ------------------------------------------------------------------ analyze

    def _start_analyze(self, *, show_credentials_warning: bool = True) -> None:
        if not self._validate_api_config(show_warning=show_credentials_warning):
            return
        logger.info("Запуск обновления объявлений (UI)")
        self._set_busy(True, "Загрузка объявлений...")
        worker = AnalyzeWorker()
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_partial=self._on_analyze_partial,
            on_finished=self._on_analyze_finished,
            on_error=self._on_worker_error,
        )

    def _on_analyze_partial(self, rows: list) -> None:
        self._rows = rows
        self._fill_ads_table(rows)
        self.status.showMessage(f"Загружено {len(rows)} объявлений, получаю статистику...")
        logger.info("Таблица обновлена (превью): %s строк", len(rows))

    def _on_analyze_finished(self, rows: list) -> None:
        self._rows = rows
        self._fill_ads_table(rows)
        msg = f"Загружено объявлений: {len(rows)}"
        logger.info(msg)
        self._set_busy(False, msg)

        if self.config.auto_archive_enabled:
            flagged = [r for r in rows if r.should_archive and r.in_local_feed]
            if flagged:
                self._archive_rows(flagged)

    def _fill_ads_table(self, rows: list[ListingWithStats]) -> None:
        self.ads_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            listing = row.listing
            item_id = listing.item_id or ""
            self.ads_table.setItem(row_idx, 0, QTableWidgetItem(str(item_id)))
            self.ads_table.setItem(row_idx, 1, QTableWidgetItem(row.category_display or "—"))
            self.ads_table.setItem(row_idx, 2, QTableWidgetItem(listing.title or ""))
            self.ads_table.setItem(row_idx, 3, QTableWidgetItem(str(listing.price or "")))
            self.ads_table.setItem(row_idx, 4, QTableWidgetItem(str(row.current_views)))
            self.ads_table.setItem(row_idx, 5, QTableWidgetItem(str(row.previous_views)))
            delta = "—" if row.views_delta_pct is None else f"{row.views_delta_pct:+.1f}"
            self.ads_table.setItem(row_idx, 6, QTableWidgetItem(delta))

            status = "Просадка" if row.should_archive else "OK"
            status_item = QTableWidgetItem(status)
            if row.should_archive:
                status_item.setBackground(QColor("#ffe0e0"))
            self.ads_table.setItem(row_idx, 7, status_item)

            feed_item = QTableWidgetItem("Да" if row.in_local_feed else "Нет")
            self.ads_table.setItem(row_idx, 8, feed_item)

            btn = QPushButton("Снять")
            btn.setEnabled(row.in_local_feed)
            btn.clicked.connect(lambda _checked, r=row: self._archive_single(r))
            self.ads_table.setCellWidget(row_idx, 9, btn)

    # ------------------------------------------------------------------ import / export

    def _selected_rows(self) -> list[ListingWithStats]:
        indexes = sorted({idx.row() for idx in self.ads_table.selectedIndexes()})
        return [self._rows[i] for i in indexes if 0 <= i < len(self._rows)]

    def _import_selected(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Импорт", "Сначала обновите список объявлений.")
            return
        selected = self._selected_rows()
        if not selected:
            QMessageBox.information(self, "Импорт", "Выберите объявления в таблице.")
            return

        to_import = [row.listing for row in selected if not row.in_local_feed]
        if not to_import:
            QMessageBox.information(self, "Импорт", "Выбранные объявления уже в фиде.")
            return

        answer = QMessageBox.question(
            self,
            "Импорт в фид",
            f"Импортировать {len(to_import)} объявлений в фид автозагрузки?\n"
            "После этого их можно снимать через приложение.",
        )
        if answer != QMessageBox.Yes:
            return

        self._set_busy(True, "Импорт объявлений в фид...")
        worker = ImportWorker(self.config, to_import)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_import_finished,
            on_error=self._on_worker_error,
        )

    def _on_import_finished(self, result: ImportResult) -> None:
        msg = f"Импортировано: {result.imported}, пропущено: {result.skipped}"
        QMessageBox.information(self, "Импорт", msg)
        self._set_busy(False, msg)
        self._start_analyze()

    def _export_excel(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Экспорт", "Нет данных. Сначала обновите список.")
            return

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        default_name = f"avito_report_{stamp}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить отчёт Excel",
            default_name,
            "Excel (*.xlsx)",
        )
        if not path:
            return

        self._set_busy(True, "Экспорт в Excel...")
        worker = ExportExcelWorker(self._rows, Path(path))
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_export_finished,
            on_error=self._on_worker_error,
        )

    def _on_export_finished(self, path: str) -> None:
        self._set_busy(False, f"Отчёт сохранён: {path}")
        QMessageBox.information(self, "Экспорт", f"Файл сохранён:\n{path}")

    # ------------------------------------------------------------------ archive

    def _archive_single(self, row: ListingWithStats) -> None:
        title = row.listing.title or str(row.listing.item_id)
        answer = QMessageBox.question(
            self,
            "Снять объявление",
            f"Убрать «{title}» из фида автозагрузки?\n"
            "После загрузки фида объявление уйдёт в архив на Avito.",
        )
        if answer != QMessageBox.Yes:
            return
        self._archive_rows([row])

    def _archive_flagged(self) -> None:
        flagged = [r for r in self._rows if r.should_archive and r.in_local_feed]
        if not flagged:
            QMessageBox.information(self, "Снятие", "Нет объявлений из фида с просадкой просмотров.")
            return
        answer = QMessageBox.question(
            self,
            "Снять объявления",
            f"Снять {len(flagged)} объявлений с просадкой просмотров?",
        )
        if answer != QMessageBox.Yes:
            return
        self._archive_rows(flagged)

    def _archive_rows(self, rows: list[ListingWithStats]) -> None:
        if not rows:
            return
        if not self._validate_api_config():
            return
        self._set_busy(True, f"Снятие объявлений: {len(rows)}...")
        row = rows[0]
        request = ArchiveRequest(
            feed_ad_id=row.feed_ad_id,
            avito_item_id=row.listing.item_id,
        )
        worker = ArchiveWorker(self.config, request)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=lambda result: self._on_archive_finished(result, rows[1:]),
            on_error=self._on_worker_error,
        )

    def _on_archive_finished(self, result: PublishResult, remaining: list[ListingWithStats]) -> None:
        if remaining:
            self._archive_rows(remaining)
            return
        msg = f"Фид обновлён: {result.feed_path}"
        if result.report_id:
            msg += f", отчёт автозагрузки #{result.report_id}"
        self.status.showMessage(msg, 5000)
        self._set_busy(False, msg)
        self._start_analyze()

    # ------------------------------------------------------------------ publish

    def _start_publish(self) -> None:
        if not self._validate_api_config():
            return
        if not self.config.feed_public_url:
            QMessageBox.warning(
                self,
                "Автозагрузка",
                "Укажите публичный URL фида в настройках.",
            )
            return

        title = self.title_input.text().strip()
        category_path = self.category_combo.currentText().strip()
        category_slug = self.category_combo.currentData()
        category_value = self._category_auto_fields.get("Category", "")
        if not title:
            QMessageBox.warning(self, "Публикация", "Заполните заголовок.")
            return
        if not category_path or not category_slug:
            QMessageBox.warning(self, "Публикация", "Загрузите и выберите категорию Avito.")
            return
        if not category_value:
            QMessageBox.warning(
                self,
                "Публикация",
                "Не удалось определить поля категории. Подождите загрузку шаблона.",
            )
            return

        extra_fields = dict(self._category_auto_fields)
        for tag, widget in self._category_field_inputs.items():
            value = widget.text().strip()
            if value:
                extra_fields[tag] = value

        cities = parse_comma_separated(self.city_input.text())
        if not cities:
            QMessageBox.warning(self, "Публикация", "Укажите хотя бы один город через запятую.")
            return

        images = parse_comma_separated(self.images_input.text())
        description = self.description_input.toPlainText().strip()
        phone = self.phone_input.text().strip() or self.config.contact_phone
        created_at = datetime.now().isoformat(timespec="seconds")
        items = [
            DraftListing(
                ad_id=str(uuid.uuid4())[:8],
                title=title,
                description=description,
                price=self.price_input.value(),
                category=category_value,
                category_path=category_path,
                category_slug=str(category_slug) if category_slug else "",
                city=city,
                phone=phone,
                images=images,
                extra_fields=dict(extra_fields),
                status="draft",
                created_at=created_at,
            )
            for city in cities
        ]

        if category_slug:
            self.config.default_category_slug = str(category_slug)
            self.config.default_category_path = category_path
            save_config(self.config)

        self._pending_publish_items = items
        busy_msg = (
            f"Проверка баланса для {len(cities)} объявлений..."
            if len(cities) > 1
            else "Проверка баланса перед публикацией..."
        )
        self._set_busy(True, busy_msg)
        worker = PublishPrecheckWorker(
            self.config,
            listings_count=len(items),
            category_slug=str(category_slug),
        )
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_publish_precheck_finished,
            on_error=self._on_worker_error,
        )

    def _on_publish_precheck_finished(self, quote: PublishQuote) -> None:
        if not quote.can_afford:
            self._set_busy(False, "Публикация отменена: недостаточно средств")
            QMessageBox.critical(
                self,
                "Недостаточно средств",
                (
                    f"На кошельке Avito недостаточно средств для публикации.\n\n"
                    f"Баланс: {quote.balance.total:,.2f} ₽\n"
                    f"Требуется: {quote.total_cost:,.2f} ₽ "
                    f"({quote.listings_count} × {quote.cost_per_listing:,.2f} ₽)\n"
                    f"Не хватает: {quote.total_cost - quote.balance.total:,.2f} ₽"
                ).replace(",", " "),
            )
            self._pending_publish_items = []
            return

        reply = QMessageBox.question(
            self,
            "Подтверждение публикации",
            (
                f"Публикация спишет средства с кошелька Avito.\n\n"
                f"Баланс: {quote.balance.total:,.2f} ₽\n"
                f"Стоимость: {quote.total_cost:,.2f} ₽ "
                f"({quote.listings_count} × {quote.cost_per_listing:,.2f} ₽)\n"
                f"После публикации останется: {quote.balance.total - quote.total_cost:,.2f} ₽\n\n"
                f"Продолжить?"
            ).replace(",", " "),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self._set_busy(False, "Публикация отменена пользователем")
            self._pending_publish_items = []
            return

        items = self._pending_publish_items
        self._pending_publish_items = []
        if not items:
            self._set_busy(False, "Нет объявлений для публикации")
            return

        busy_msg = (
            f"Публикация объявления в {len(items)} городах..."
            if len(items) > 1
            else "Публикация объявления..."
        )
        self.status.showMessage(busy_msg)
        worker = PublishWorker(self.config, items)
        thread = QThread()
        self._run_worker(
            worker,
            thread,
            on_finished=self._on_publish_finished,
            on_error=self._on_worker_error,
        )

    def _on_publish_finished(self, result: PublishResult) -> None:
        if result.added_count > 1 and result.cities:
            msg = (
                f"Добавлено {result.added_count} объявлений для городов: "
                f"{', '.join(result.cities)}.\n"
                f"Всего в фиде: {result.items_count}. Файл: {result.feed_path}"
            )
        else:
            msg = f"Фид сохранён ({result.items_count} объявл.): {result.feed_path}"
        if result.report_id:
            msg += f"\nЗагрузка запущена, отчёт #{result.report_id}"
        QMessageBox.information(self, "Публикация", msg)
        self._set_busy(False, msg.replace("\n", " "))
        self._refresh_balance(show_credentials_warning=False)
        self.title_input.clear()
        self.description_input.clear()
        self.price_input.setValue(0)
        self.city_input.clear()
        self.images_input.clear()
        for widget in self._category_field_inputs.values():
            widget.clear()

    def _open_log_file(self) -> None:
        import os

        log_path = get_log_file()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.write_text("", encoding="utf-8")
        logger.info("Открытие лог-файла: %s", log_path)
        os.startfile(log_path)  # noqa: S606 — Windows only

    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False, f"Ошибка — см. лог: {get_log_file().name}")
        self.load_cat_btn.setEnabled(True)
        logger.error("Ошибка в UI: %s", message.replace("\n", " | "))
        QMessageBox.critical(self, "Ошибка", message)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.refresh_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy)
        self.export_btn.setEnabled(not busy)
        self.auto_archive_btn.setEnabled(not busy)
        self.refresh_balance_btn.setEnabled(not busy)
        self.status.showMessage(message)


def run_app() -> None:
    import sys

    ui_logger = get_logger("ui")
    ui_logger.info("Инициализация QApplication")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    ui_logger.info("Главное окно отображено")
    sys.exit(app.exec_())
