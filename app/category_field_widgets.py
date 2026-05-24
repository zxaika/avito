"""Виджеты для полей категории автозагрузки Avito."""

from __future__ import annotations

from PyQt5.QtWidgets import QCheckBox, QVBoxLayout, QWidget


class CheckboxFieldWidget(QWidget):
    """Множественный выбор — field_type=checkbox в API Avito (WorkDays и др.)."""

    def __init__(
        self,
        values: list[str],
        *,
        labels: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._checkboxes: list[tuple[str, QCheckBox]] = []
        label_map = labels or {}
        for value in values:
            checkbox = QCheckBox(label_map.get(value, value))
            layout.addWidget(checkbox)
            self._checkboxes.append((value, checkbox))

    def selected_values(self) -> list[str]:
        return [value for value, checkbox in self._checkboxes if checkbox.isChecked()]

    def clear_selection(self) -> None:
        for _, checkbox in self._checkboxes:
            checkbox.setChecked(False)
