"""Генерация XML-фида автозагрузки Avito."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from app.database import DraftListing


def _add_text(parent: ET.Element, tag: str, value: str) -> None:
    child = ET.SubElement(parent, tag)
    child.text = value


def build_autoload_xml(items: list[DraftListing]) -> str:
    root = ET.Element("Ads", {"formatVersion": "3", "target": "Avito.ru"})
    for item in items:
        ad = ET.SubElement(root, "Ad")
        _add_text(ad, "Id", item.ad_id)
        _add_text(ad, "AllowEmail", "Да")
        _add_text(ad, "Category", item.category)

        for tag, value in item.extra_fields.items():
            if value.strip():
                _add_text(ad, tag, value.strip())

        _add_text(ad, "Title", item.title)

        desc = ET.SubElement(ad, "Description")
        desc.text = item.description

        _add_text(ad, "Price", str(item.price))
        _add_text(ad, "Address", item.city)
        if item.phone:
            _add_text(ad, "ContactPhone", item.phone)

        if item.images:
            images_el = ET.SubElement(ad, "Images")
            for url in item.images:
                ET.SubElement(images_el, "Image", {"url": url})

        if item.avito_item_id:
            _add_text(ad, "AvitoId", str(item.avito_item_id))

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_body}\n'


def write_feed_file(items: list[DraftListing], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_autoload_xml(items), encoding="utf-8")
    return path
