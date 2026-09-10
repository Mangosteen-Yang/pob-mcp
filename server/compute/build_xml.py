"""Read-only parsing of a Path of Building build XML.

The engine round-trips builds through Lua, whose table iteration order is not stable, so the
same build re-serialises with its XML attributes in a different order between runs. Regexes like
``<Slot itemId="(\\d+)"[^>]*name="([^"]+)"`` therefore work on one export and silently return
nothing on the next. Parse the tree instead — that is what this module is for.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

__all__ = ["equipped_items", "item_texts"]


def _root(xml: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def item_texts(xml: str) -> dict[str, str]:
    """{item id -> raw PoB item text} for every item in the build's item pool."""
    root = _root(xml)
    if root is None:
        return {}
    out: dict[str, str] = {}
    for item in root.iter("Item"):
        item_id = (item.get("id") or "").strip()
        if not item_id:
            continue
        # The item's text is the element's own text; <ModRange/> children carry only tails.
        out[item_id] = (item.text or "").strip()
    return out


def equipped_items(xml: str) -> dict[str, str]:
    """{slot name -> raw PoB item text} for the build's ACTIVE item set.

    Empty slots (``itemId="0"``) are omitted. Returns {} when the XML has no item set or cannot
    be parsed — callers treat this as "nothing to check", never as an error.
    """
    root = _root(xml)
    if root is None:
        return {}
    items = root.find("Items")
    if items is None:
        return {}
    texts = item_texts(xml)
    sets = items.findall("ItemSet")
    if not sets:
        return {}
    active = items.get("activeItemSet")
    chosen = next((s for s in sets if s.get("id") == active), sets[0])
    out: dict[str, str] = {}
    for slot in chosen.findall("Slot"):
        name, item_id = slot.get("name"), (slot.get("itemId") or "0").strip()
        if not name or item_id == "0":
            continue
        text = texts.get(item_id)
        if text:
            out[name] = text
    return out
