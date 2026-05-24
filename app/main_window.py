"""Главное окно Avito Desktop Manager."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QTimer, QThread
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

from app.avito_service import ImportResult, ListingWithStats, PublishResult
from app.config import AppConfig, load_config, save_config
from app.database import DraftListing, init_db
from app.workers import (
    AnalyzeWorker,
    ArchiveRequest,
    ArchiveWorker,
    CategoryFieldsWorker,
    CategoryTreeWorker,
    ExportExcelWorker,
    ImportWorker,
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        init_db()
        self.config = load_config()
        self._threads: list[QThread] = []
        self._rows: list[ListingWithStats] = []
        self._category_field_inputs: dict[str, QLineEdit] = {}

        self.setWindowTitle("Avito Desktop Manager")
        self.resize(1150, 760)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_ads_tab()
        self._build_create_tab()
        self._build_settings_tab()

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.timeout.connect(self._on_scheduler_tick)
        self._apply_scheduler()
        self.status.showMessage("Готово")

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
        self.auto_archive_btn = QPushButton("Снять просевшие (из фида)")
        self.auto_archive_btn.clicked.connect(self._archive_flagged)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.auto_archive_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.ads_table = QTableWidget(0, 9)
        self.ads_table.setHorizontalHeaderLabels(
            [
                "ID",
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
        self.ads_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
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
        self.phone_input = QLineEdit()
        self.phone_input.setText(self.config.contact_phone)
        self.images_input = QLineEdit()
        self.images_input.setPlaceholderText("URL фото через запятую")
        self.description_input = QTextEdit()
        self.description_input.setMinimumHeight(100)

        form.addRow("Заголовок", self.title_input)
        form.addRow("Цена, ₽", self.price_input)
        form.addRow("Город / адрес", self.city_input)
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
            "Выберите категорию — подгрузятся обязательные поля шаблона Avito. "
            "Публикация идёт через XML-фид автозагрузки."
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
        feed_form.addRow("Публичный URL фида", self.feed_url_input)
        feed_form.addRow("Телефон по умолчанию", self.contact_phone_setting)

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

    def _load_categories(self) -> None:
        if not self._validate_api_config():
            return
        self._set_busy(True, "Загрузка категорий Avito...")
        worker = CategoryTreeWorker(self.config)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_categories_loaded)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

    def _on_categories_loaded(self, categories: list) -> None:
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for slug, label in categories:
            self.category_combo.addItem(label, slug)
        if self.config.default_category_slug:
            index = self.category_combo.findData(self.config.default_category_slug)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
        self.category_combo.blockSignals(False)
        self._set_busy(False, f"Загружено категорий: {len(categories)}")
        if self.category_combo.currentData():
            self._load_category_fields(str(self.category_combo.currentData()))

    def _on_category_changed(self, _index: int) -> None:
        slug = self.category_combo.currentData()
        if slug:
            self._load_category_fields(str(slug))

    def _load_category_fields(self, node_slug: str) -> None:
        worker = CategoryFieldsWorker(self.config, node_slug)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_category_fields_loaded)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

    def _on_category_fields_loaded(self, fields: list) -> None:
        self._category_field_inputs.clear()
        while self.category_fields_layout.rowCount():
            self.category_fields_layout.removeRow(0)

        for field in fields:
            slug = field.slug or ""
            if not slug or slug.lower() in SKIP_FIELD_TAGS:
                continue
            label = field.title or slug
            if field.required:
                label += " *"
            input_widget = QLineEdit()
            self._category_field_inputs[slug] = input_widget
            self.category_fields_layout.addRow(label, input_widget)

    # ------------------------------------------------------------------ settings

    def _save_settings(self) -> None:
        user_id_raw = self.user_id_input.text().strip()
        slug = self.category_combo.currentData()
        self.config = AppConfig(
            client_id=self.client_id_input.text().strip(),
            client_secret=self.client_secret_input.text().strip(),
            user_id=int(user_id_raw) if user_id_raw.isdigit() else None,
            feed_public_url=self.feed_url_input.text().strip(),
            contact_phone=self.contact_phone_setting.text().strip(),
            default_category_slug=str(slug) if slug else self.config.default_category_slug,
            stats_period_days=self.period_spin.value(),
            min_views_baseline=self.baseline_spin.value(),
            drop_percent_threshold=self.drop_spin.value(),
            auto_archive_enabled=self.auto_archive_check.isChecked(),
            scheduler_enabled=self.scheduler_check.isChecked(),
            scheduler_interval_minutes=self.scheduler_interval_spin.value(),
        )
        save_config(self.config)
        self.phone_input.setText(self.config.contact_phone)
        self._apply_scheduler()
        self.status.showMessage("Настройки сохранены", 3000)

    def _validate_api_config(self) -> bool:
        if not self.config.client_id or not self.config.client_secret:
            QMessageBox.warning(
                self,
                "Настройки",
                "Укажите Client ID и Client Secret на вкладке «Настройки».",
            )
            return False
        return True

    # ------------------------------------------------------------------ analyze

    def _start_analyze(self) -> None:
        if not self._validate_api_config():
            return
        self._set_busy(True, "Загрузка объявлений и статистики...")
        worker = AnalyzeWorker(self.config)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_analyze_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

    def _on_analyze_finished(self, rows: list) -> None:
        self._rows = rows
        self._fill_ads_table(rows)
        self._set_busy(False, f"Загружено объявлений: {len(rows)}")

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
            self.ads_table.setItem(row_idx, 1, QTableWidgetItem(listing.title or ""))
            self.ads_table.setItem(row_idx, 2, QTableWidgetItem(str(listing.price or "")))
            self.ads_table.setItem(row_idx, 3, QTableWidgetItem(str(row.current_views)))
            self.ads_table.setItem(row_idx, 4, QTableWidgetItem(str(row.previous_views)))
            delta = "—" if row.views_delta_pct is None else f"{row.views_delta_pct:+.1f}"
            self.ads_table.setItem(row_idx, 5, QTableWidgetItem(delta))

            status = "Просадка" if row.should_archive else "OK"
            status_item = QTableWidgetItem(status)
            if row.should_archive:
                status_item.setBackground(QColor("#ffe0e0"))
            self.ads_table.setItem(row_idx, 6, status_item)

            feed_item = QTableWidgetItem("Да" if row.in_local_feed else "Нет")
            self.ads_table.setItem(row_idx, 7, feed_item)

            btn = QPushButton("Снять")
            btn.setEnabled(row.in_local_feed)
            btn.clicked.connect(lambda _checked, r=row: self._archive_single(r))
            self.ads_table.setCellWidget(row_idx, 8, btn)

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
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_import_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

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
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_export_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

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
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda result: self._on_archive_finished(result, rows[1:]))
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

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
        category_label = self.category_combo.currentText().strip()
        category_slug = self.category_combo.currentData()
        if not title:
            QMessageBox.warning(self, "Публикация", "Заполните заголовок.")
            return
        if not category_label:
            QMessageBox.warning(self, "Публикация", "Загрузите и выберите категорию Avito.")
            return

        extra_fields = {
            slug: widget.text().strip()
            for slug, widget in self._category_field_inputs.items()
            if widget.text().strip()
        }

        images = [part.strip() for part in self.images_input.text().split(",") if part.strip()]
        item = DraftListing(
            ad_id=str(uuid.uuid4())[:8],
            title=title,
            description=self.description_input.toPlainText().strip(),
            price=self.price_input.value(),
            category=category_label,
            category_slug=str(category_slug) if category_slug else "",
            city=self.city_input.text().strip(),
            phone=self.phone_input.text().strip() or self.config.contact_phone,
            images=images,
            extra_fields=extra_fields,
            status="draft",
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

        if category_slug:
            self.config.default_category_slug = str(category_slug)
            save_config(self.config)

        self._set_busy(True, "Публикация объявления...")
        worker = PublishWorker(self.config, item)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_publish_finished)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._threads.append(thread)

    def _on_publish_finished(self, result: PublishResult) -> None:
        msg = f"Фид сохранён ({result.items_count} объявл.): {result.feed_path}"
        if result.report_id:
            msg += f". Загрузка запущена, отчёт #{result.report_id}"
        QMessageBox.information(self, "Публикация", msg)
        self._set_busy(False, msg)
        self.title_input.clear()
        self.description_input.clear()
        self.price_input.setValue(0)
        self.images_input.clear()
        for widget in self._category_field_inputs.values():
            widget.clear()

    def _on_worker_error(self, message: str) -> None:
        self._set_busy(False, "Ошибка")
        QMessageBox.critical(self, "Ошибка", message)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.refresh_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy)
        self.export_btn.setEnabled(not busy)
        self.auto_archive_btn.setEnabled(not busy)
        self.status.showMessage(message)


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
