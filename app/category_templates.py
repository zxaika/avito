"""Шаблоны полей категорий из data/category_templates.json (файл Категории.xlsx)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.category_utils import AutoloadFeedField, CategoryOption, HIERARCHY_TAGS

TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "data" / "category_templates.json"

# Поля основной формы или подставляются автоматически — не показываем в блоке категории.
UI_HIDDEN_FIELD_TAGS = frozenset(
    {
        "title",
        "description",
        "price",
        "address",
        "contactphone",
        "images",
        "imageurls",
        "imagenames",
        "category",
        "servicetype",
        "servicesubtype",
        "id",
        "avitoid",
        "allowemail",
    }
)

TAG_LABELS_RU: dict[str, str] = {
    "ListingFee": "Способ размещения",
    "ManagerName": "Контактное лицо",
    "ContactMethod": "Способ связи",
    "AvitoDateEnd": "Дата окончания размещения",
    "EMail": "E-mail",
    "AvitoStatus": "Статус объявления",
    "CompanyName": "Название компании",
    "PriceList": "Прайс-лист",
}

LISTING_FEE_LABELS: dict[str, str] = {
    "Package": "Пакет размещения",
    "PackageSingle": "Пакет или разовое",
    "Single": "Только разовое",
}


@dataclass
class CategoryFieldsBundle:
    fields: list[AutoloadFeedField]
    auto_fields: dict[str, str]


@lru_cache(maxsize=1)
def _load_templates_data() -> dict[str, object]:
    if not TEMPLATES_PATH.exists():
        return {"categories": []}
    return json.loads(TEMPLATES_PATH.read_text(encoding="utf-8"))


def get_template_categories() -> list[tuple[str, str]]:
    """Список (slug, path) только для категорий из шаблона."""
    data = _load_templates_data()
    categories = data.get("categories") or []
    result: list[tuple[str, str]] = []
    for item in categories:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        path = str(item.get("path") or "").strip()
        if slug and path:
            result.append((slug, path))
    return result


def get_template_by_slug(slug: str) -> dict[str, object] | None:
    for item in _load_templates_data().get("categories") or []:
        if isinstance(item, dict) and item.get("slug") == slug:
            return item
    return None


def get_category_option(slug: str) -> CategoryOption | None:
    template = get_template_by_slug(slug)
    if template is None:
        return None
    path = str(template.get("path") or "")
    leaf = path.rsplit(" / ", maxsplit=1)[-1] if path else slug
    return CategoryOption(slug=slug, path=path, leaf_name=leaf)


def extract_auto_fields(api_fields: list[AutoloadFeedField]) -> dict[str, str]:
    auto: dict[str, str] = {}
    for field in api_fields:
        if field.auto_value:
            auto[field.tag] = field.auto_value
        elif field.tag in HIERARCHY_TAGS and field.values:
            auto[field.tag] = field.values[0]
    return auto


def _label_for_field(field: AutoloadFeedField) -> str:
    label = (field.label or "").strip()
    if label and label not in {field.tag, "AvitoDateEnd", "AvitoStatus", "EMail", "CompanyName"}:
        return label
    return TAG_LABELS_RU.get(field.tag, label or field.tag)


def apply_template_fields(
    slug: str,
    api_fields: list[AutoloadFeedField],
) -> CategoryFieldsBundle:
    """Оставляет только поля из шаблона Excel, подписи — на русском из API."""
    auto_fields = extract_auto_fields(api_fields)
    api_by_tag = {field.tag: field for field in api_fields if field.tag}

    template = get_template_by_slug(slug)
    if template is None:
        visible = [
            field
            for field in api_fields
            if field.tag and field.tag.lower() not in UI_HIDDEN_FIELD_TAGS and not field.auto_value
        ]
        return CategoryFieldsBundle(fields=visible, auto_fields=auto_fields)

    field_tags = template.get("field_tags") or []
    visible: list[AutoloadFeedField] = []
    for raw_tag in field_tags:
        tag = str(raw_tag).strip()
        if not tag or tag.lower() in UI_HIDDEN_FIELD_TAGS:
            continue
        source = api_by_tag.get(tag)
        if source is None:
            visible.append(
                AutoloadFeedField(
                    tag=tag,
                    label=TAG_LABELS_RU.get(tag, tag),
                    required=False,
                    field_type="input",
                    values=[],
                )
            )
            continue
        if source.auto_value:
            auto_fields[tag] = source.auto_value
            continue
        visible.append(
            AutoloadFeedField(
                tag=source.tag,
                label=_label_for_field(source),
                required=bool(source.required),
                field_type=source.field_type,
                values=list(source.values),
                default=source.default,
                auto_value=None,
            )
        )
    return CategoryFieldsBundle(fields=visible, auto_fields=auto_fields)
