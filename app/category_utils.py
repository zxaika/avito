"""Утилиты для дерева категорий автозагрузки Avito."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HIERARCHY_TAGS = frozenset(
    {
        "Category",
        "ServiceType",
        "ServiceSubtype",
        "GoodsType",
        "ProductType",
        "AdType",
        "Type",
        "SubType",
    }
)


@dataclass(frozen=True)
class CategoryOption:
    slug: str
    path: str
    leaf_name: str


@dataclass
class CategoryMeta:
    slug: str
    path: str
    category: str
    auto_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AutoloadFeedField:
    tag: str
    label: str
    required: bool
    field_type: str
    values: list[str]
    default: str | None = None
    auto_value: str | None = None


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    nested = node.get("nested") or node.get("children") or node.get("items") or []
    return nested if isinstance(nested, list) else []


def _walk_tree_nodes(
    nodes: list[dict[str, Any]],
    *,
    parent_path: str = "",
) -> list[CategoryOption]:
    """Только конечные категории — API /fields работает лишь для leaf-узлов."""
    result: list[CategoryOption] = []
    for node in nodes:
        name = str(node.get("name") or node.get("title") or "").strip()
        slug = str(node.get("slug") or node.get("code") or "").strip()
        path = f"{parent_path} / {name}" if parent_path else name
        children = _node_children(node)
        if children:
            result.extend(_walk_tree_nodes(children, parent_path=path))
        elif slug:
            result.append(
                CategoryOption(
                    slug=slug,
                    path=path,
                    leaf_name=name,
                )
            )
    return result


def flatten_category_tree(nodes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Пары (slug, полный путь) для выпадающего списка."""
    options = _walk_tree_nodes(nodes)
    return [(option.slug, option.path) for option in options]


def find_category_option(
    nodes: list[dict[str, Any]],
    slug: str,
) -> CategoryOption | None:
    for option in _walk_tree_nodes(nodes):
        if option.slug == slug:
            return option
    return None


def _field_values(content: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for block in content:
        for item in block.get("values") or []:
            value = item.get("value")
            if value is not None:
                values.append(str(value))
    return values


def parse_category_fields(raw_fields: list[dict[str, Any]]) -> list[AutoloadFeedField]:
    parsed: list[AutoloadFeedField] = []
    for raw in raw_fields:
        tag = str(raw.get("tag") or raw.get("slug") or "").strip()
        if not tag:
            continue
        label = str(raw.get("label") or raw.get("title") or tag).strip()
        content = raw.get("content") or []
        if not isinstance(content, list):
            content = []

        required = any(bool(block.get("required")) for block in content)
        field_type = ""
        default: str | None = None
        values = _field_values(content)
        for block in content:
            if not field_type:
                field_type = str(block.get("field_type") or block.get("type") or "")
            block_default = block.get("default") or {}
            if isinstance(block_default, dict) and block_default.get("value") is not None:
                default = str(block_default["value"])

        auto_value: str | None = None
        if field_type == "select" and len(values) == 1:
            auto_value = values[0]
        elif tag in HIERARCHY_TAGS and len(values) == 1:
            auto_value = values[0]

        parsed.append(
            AutoloadFeedField(
                tag=tag,
                label=label,
                required=required,
                field_type=field_type,
                values=values,
                default=default,
                auto_value=auto_value,
            )
        )
    return parsed


def build_category_meta(
    option: CategoryOption,
    fields: list[AutoloadFeedField],
) -> CategoryMeta:
    auto_fields: dict[str, str] = {}
    category_value = ""

    for feed_field in fields:
        if feed_field.auto_value:
            auto_fields[feed_field.tag] = feed_field.auto_value
        if feed_field.tag == "Category" and feed_field.auto_value:
            category_value = feed_field.auto_value

    if not category_value:
        for feed_field in fields:
            if feed_field.tag == "Category" and feed_field.values:
                category_value = feed_field.values[0]
                auto_fields.setdefault("Category", category_value)
                break

    return CategoryMeta(
        slug=option.slug,
        path=option.path,
        category=category_value,
        auto_fields=auto_fields,
    )
