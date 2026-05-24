"""Утилиты для дерева категорий автозагрузки."""

from __future__ import annotations

from avito.ads.models import AutoloadTreeNode


def flatten_category_tree(
    nodes: list[AutoloadTreeNode],
    *,
    parent_path: str = "",
) -> list[tuple[str, str]]:
    """Возвращает пары (slug, «путь / название») для выпадающего списка."""
    result: list[tuple[str, str]] = []
    for node in nodes:
        title = node.title or node.slug or ""
        path = f"{parent_path} / {title}" if parent_path else title
        if node.slug:
            result.append((node.slug, path))
        result.extend(flatten_category_tree(node.children, parent_path=path))
    return result
