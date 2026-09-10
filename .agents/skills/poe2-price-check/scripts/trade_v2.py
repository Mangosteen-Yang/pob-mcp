from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


USER_AGENT = "pob-mcp/2.0 (contact: github.com/Mangosteen-Yang/pob-mcp)"
TRADE_API_BASE = "https://www.pathofexile.com/api/trade2"
TRADE_WEB_BASE = "https://www.pathofexile.com/trade2"

STATUS_MAP = {
    "securable": "securable",
    "instant": "securable",
    "instant_buyout": "securable",
    "buyout": "securable",
    "available": "available",
    "both": "available",
    "onlineleague": "onlineleague",
    "online": "online",
    "any": "any",
    "offline": "any",
}

STATUS_DISPLAY = {
    "securable": "Instant Buyout",
    "available": "Instant Buyout and In Person",
    "onlineleague": "In Person (Online in League)",
    "online": "In Person (Online)",
    "any": "Any (Include Offline)",
}

CURRENCY_TAGS = {
    "divine orb": "divine",
    "exalted orb": "exalted",
    "chaos orb": "chaos",
    "greater exalted orb": "greater-exalted-orb",
    "perfect exalted orb": "perfect-exalted-orb",
    "greater chaos orb": "greater-chaos-orb",
    "perfect chaos orb": "perfect-chaos-orb",
    "orb of alchemy": "alch",
    "mirror of kalandra": "mirror",
    "artificer's orb": "artificers-orb",
    "lesser jeweler's orb": "lesser-jewellers-orb",
    "jeweler's orb": "jewellers-orb",
    "greater jeweler's orb": "greater-jewellers-orb",
    "perfect jeweler's orb": "perfect-jewellers-orb",
    "chance orb": "chance",
    "orb of chance": "chance",
    "orb of transmutation": "transmute",
    "orb of augmentation": "aug",
    "regal orb": "regal",
    "vaal orb": "vaal",
}

PROPERTY_PREFIXES = (
    "Armour:",
    "Evasion Rating:",
    "Energy Shield:",
    "Runic Ward:",
    "Physical Damage:",
    "Elemental Damage:",
    "Critical Hit Chance:",
    "Attacks per Second:",
    "Weapon Range:",
    "Requires:",
    "Item Level:",
    "Quality:",
    "Sockets:",
    "Rune Sockets:",
    "LevelReq:",
    "Waystone Tier:",
    "Stack Size:",
    "Rune:",
)

AUTO_LOCK_PATTERNS = (
    "level of all",
    "level of socketed",
    "maximum skill level",
)


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class RateLimiter:
    """Header-driven limiter that waits once for the longest active rule."""

    def __init__(self, *, clock=time.monotonic, sleeper=time.sleep) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._blocked_until = 0.0
        self.reason = ""

    def update_from_headers(self, headers: Any) -> float:
        waits: list[float] = []
        names = (headers.get("X-Rate-Limit-Rules") or "").split(",")
        for raw_name in names:
            name = raw_name.strip()
            if not name:
                continue
            limits_text = headers.get(f"X-Rate-Limit-{name}")
            states_text = headers.get(f"X-Rate-Limit-{name}-State")
            if not limits_text or not states_text:
                continue
            try:
                limits = [tuple(map(int, value.split(":"))) for value in limits_text.split(",")]
                states = [tuple(map(int, value.split(":"))) for value in states_text.split(",")]
            except (TypeError, ValueError):
                continue
            for limit, state in zip(limits, states):
                max_hits, _window, _penalty = limit
                current_hits, _state_window, retry_after = state
                if current_hits >= max_hits:
                    waits.append(max(float(retry_after), 1.0) + 0.5)
        wait = max(waits, default=0.0)
        if wait:
            self.block_for(wait, "GGG rate-limit header")
        return wait

    def block_for(self, seconds: float, reason: str) -> None:
        target = self._clock() + max(seconds, 0.0)
        if target > self._blocked_until:
            self._blocked_until = target
            self.reason = reason

    def wait_if_needed(self) -> float:
        wait = self._blocked_until - self._clock()
        if wait <= 0:
            return 0.0
        _stderr(f"Rate limit: waiting {math.ceil(wait)}s ({self.reason}).")
        self._sleeper(wait)
        return wait


RATE_LIMITER = RateLimiter()


def http_request(
    url: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], Any]:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    for attempt in range(3):
        RATE_LIMITER.wait_if_needed()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read().decode("utf-8")
                RATE_LIMITER.update_from_headers(response.headers)
                return json.loads(content), response.headers
        except urllib.error.HTTPError as exc:
            RATE_LIMITER.update_from_headers(exc.headers)
            if exc.code == 429:
                raw_wait = exc.headers.get("Retry-After")
                try:
                    wait = float(raw_wait) if raw_wait else float((attempt + 1) * 3)
                except ValueError:
                    wait = float((attempt + 1) * 3)
                RATE_LIMITER.block_for(wait + 0.5, "HTTP 429 Retry-After")
                continue
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code} for {url}: {body_text or exc.reason}") from exc
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            RATE_LIMITER.block_for(float(attempt + 1), "transient network error")
    raise RuntimeError(f"Failed to fetch {url} after 3 attempts")


def get_active_leagues() -> list[dict[str, Any]]:
    data, _ = http_request(f"{TRADE_API_BASE}/data/leagues")
    return list(data.get("result", []))


def get_default_league() -> str:
    try:
        leagues = get_active_leagues()
    except Exception as exc:
        _stderr(f"League discovery unavailable ({exc}); falling back to Standard.")
        return "Standard"
    excluded = ("standard", "hardcore", "solo self-found", "ssf", "ruthless")
    for league in leagues:
        league_id = str(league.get("id", ""))
        lowered = league_id.lower()
        if league_id and not any(token in lowered for token in excluded):
            return league_id
    return str(leagues[0].get("id", "Standard")) if leagues else "Standard"


@dataclass
class ParsedModifier:
    text: str
    domain: str
    value: float | None
    marker: str = ""


@dataclass
class ParsedItem:
    raw_text: str
    item_class: str | None = None
    rarity: str = "Normal"
    name: str = ""
    base_type: str = ""
    item_level: int | None = None
    quality: int | None = None
    gem_level: int | None = None
    waystone_tier: int | None = None
    is_corrupted: bool = False
    is_unidentified: bool = False
    modifiers: list[ParsedModifier] = field(default_factory=list)
    implicits: list[str] = field(default_factory=list)
    explicits: list[str] = field(default_factory=list)
    runes_embedded: list[str] = field(default_factory=list)


def strip_trade_markup(text: str) -> str:
    text = re.sub(r"\[([^\]|]+)\|([^\]]+)\]", r"\2", text)
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def modifier_signature(text: str) -> str:
    text = strip_trade_markup(text)
    text = re.sub(r"\([+-]?\d+(?:\.\d+)?(?:\s*[—-]\s*[+-]?\d+(?:\.\d+)?)?\)", "", text)
    text = re.sub(r"\+\s*[+-]?\d+(?:\.\d+)?", "+#", text)
    text = re.sub(r"(?<![#\w])[+-]?\d+(?:\.\d+)?", "#", text)
    # The Trade catalog commonly omits the display-only leading plus that PoB
    # includes on positive flat values ("+# to ..." versus "# to ...").
    text = text.replace("+#", "#")
    return re.sub(r"\s+", " ", text).strip().lower()


def modifier_value(text: str) -> float | None:
    clean = strip_trade_markup(text)
    clean = re.sub(r"\([+-]?\d+(?:\.\d+)?(?:\s*[—-]\s*[+-]?\d+(?:\.\d+)?)?\)", "", clean)
    match = re.search(r"(?<![\w])([+-]?\d+(?:\.\d+)?)", clean)
    return float(match.group(1)) if match else None


def _domain_from_marker(marker: str) -> str:
    lowered = marker.lower()
    if "rune modifier" in lowered or "augment modifier" in lowered:
        return "rune"
    if "desecrated" in lowered:
        return "desecrated"
    if "crafted" in lowered:
        return "crafted"
    if "enchant" in lowered:
        return "enchant"
    if "implicit modifier" in lowered:
        return "implicit"
    if "unique modifier" in lowered:
        return "unique"
    return "explicit"


def _add_modifier(item: ParsedItem, text: str, domain: str, marker: str = "") -> None:
    clean = re.sub(r"^(?:\{[^}]+\})+", "", text).strip()
    if not clean or clean in {"Corrupted", "Unidentified"}:
        return
    item.modifiers.append(ParsedModifier(clean, domain, modifier_value(clean), marker))
    if domain == "implicit":
        item.implicits.append(clean)
    elif domain == "rune":
        item.runes_embedded.append(clean)
    elif domain not in {"unique", "enchant"}:
        item.explicits.append(clean)


def _parse_header(lines: list[str], item: ParsedItem) -> int:
    name_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("Item Class:"):
            item.item_class = line.split(":", 1)[1].strip()
        elif line.startswith("Rarity:"):
            item.rarity = line.split(":", 1)[1].strip().title()
        elif line == "--------" or line.startswith(PROPERTY_PREFIXES) or line.startswith("Implicits:"):
            break
        elif not line.startswith("{"):
            name_lines.append(line)
        index += 1
    if item.rarity in {"Rare", "Unique"} and len(name_lines) >= 2:
        item.name, item.base_type = name_lines[0], name_lines[1]
    elif name_lines:
        item.name = name_lines[0]
        item.base_type = name_lines[-1]
    return index


def _parse_common_property(item: ParsedItem, line: str) -> bool:
    for prefix, attr in (("Item Level:", "item_level"), ("Waystone Tier:", "waystone_tier")):
        match = re.match(rf"^{re.escape(prefix)}\s*\+?(\d+)", line)
        if match:
            setattr(item, attr, int(match.group(1)))
            return True
    quality = re.match(r"^Quality:\s*\+?(\d+)%", line)
    if quality:
        item.quality = int(quality.group(1))
        return True
    if line == "Corrupted":
        item.is_corrupted = True
        return True
    if line == "Unidentified":
        item.is_unidentified = True
        return True
    return line.startswith(PROPERTY_PREFIXES)


def _parse_advanced_item(text: str, item: ParsedItem) -> None:
    marker = ""
    domain = "explicit"
    header_finished = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"[-—]{4,}", line):
            header_finished = True
            marker = ""
            continue
        if not header_finished or _parse_common_property(item, line):
            continue
        if line.startswith("{") and line.endswith("}"):
            marker = line
            domain = _domain_from_marker(marker)
            continue
        if marker:
            _add_modifier(item, line, domain, marker)


def _parse_pob_item(text: str, item: ParsedItem) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    implicit_count = 0
    implicit_seen = 0
    in_modifiers = False
    for line in lines:
        match = re.match(r"^Implicits:\s*(\d+)", line)
        if match:
            implicit_count = int(match.group(1))
            in_modifiers = True
            continue
        if not in_modifiers:
            _parse_common_property(item, line)
            continue
        if _parse_common_property(item, line):
            continue
        if implicit_seen < implicit_count:
            domain = "rune" if "{rune}" in line else "enchant" if "{enchant}" in line else "implicit"
            implicit_seen += 1
        else:
            domain = "crafted" if "{crafted}" in line else "desecrated" if "{desecrated}" in line else "explicit"
        _add_modifier(item, line, domain)


def parse_item_text(text: str) -> ParsedItem:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("Item text is empty")
    item = ParsedItem(raw_text=text)
    _parse_header(lines, item)
    uncut = re.search(r"Uncut\s+(Skill|Spirit|Support)\s+Gem\s*\(Level\s*(\d+)\)", text, re.I)
    if uncut:
        gem_type, level = uncut.group(1).title(), int(uncut.group(2))
        item.item_class = f"Uncut {gem_type} Gems"
        item.base_type = f"Uncut {gem_type} Gem"
        item.name = f"Uncut {gem_type} Gem (Level {level})"
        item.gem_level = level
    if re.search(r"(?m)^[-—]{4,}\s*$", text):
        _parse_advanced_item(text, item)
    else:
        _parse_pob_item(text, item)
    return item


@dataclass(frozen=True)
class StatEntry:
    stat_id: str
    text: str
    domain: str
    signature: str


class StatCatalog:
    def __init__(self, entries: Iterable[StatEntry]) -> None:
        self.entries = list(entries)
        self._by_key: dict[tuple[str, str], list[StatEntry]] = {}
        for entry in self.entries:
            self._by_key.setdefault((entry.domain, entry.signature), []).append(entry)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "StatCatalog":
        entries: list[StatEntry] = []
        for group in payload.get("result", []):
            for raw in group.get("entries", []):
                stat_id = str(raw.get("id", ""))
                text = str(raw.get("text", ""))
                if not stat_id or not text or "." not in stat_id:
                    continue
                domain = stat_id.split(".", 1)[0]
                entries.append(StatEntry(stat_id, text, domain, modifier_signature(text)))
        return cls(entries)

    @classmethod
    def fetch(
        cls,
        *,
        cache_path: Path | None = None,
        max_age: float = 24 * 60 * 60,
        refresh: bool = False,
    ) -> "StatCatalog":
        stale_payload: dict[str, Any] | None = None
        if cache_path and cache_path.exists():
            try:
                stale_payload = json.loads(cache_path.read_text(encoding="utf-8"))
                age = time.time() - cache_path.stat().st_mtime
                if not refresh and age <= max_age:
                    return cls.from_api(stale_payload)
            except (OSError, json.JSONDecodeError):
                stale_payload = None
        try:
            payload, _ = http_request(f"{TRADE_API_BASE}/data/stats")
        except Exception:
            if stale_payload is not None:
                _stderr("Stat catalog refresh failed; using stale cached catalog.")
                return cls.from_api(stale_payload)
            raise
        if cache_path:
            _write_json_atomic(cache_path, payload)
        return cls.from_api(payload)

    def resolve(
        self, modifier: ParsedModifier, *, prefer_local: bool = False
    ) -> tuple[StatEntry | None, str | None]:
        domain = "explicit" if modifier.domain == "unique" else modifier.domain
        signature = modifier_signature(modifier.text)
        # DEFENCE mods on gear that carries its own Armour/Evasion/Energy Shield are LOCAL, and
        # the Trade catalog lists them as a separate "... (Local)" stat. PoB item text writes them
        # exactly like the global variant ("+65 to maximum Energy Shield"), so a plain signature
        # match silently picks the global id — which matches nothing on armour and returns zero
        # results. Prefer the local entry when the item carries local defences.
        #
        # Scope this to defences only. Accuracy Rating and Attack Speed also have "(Local)" twins,
        # but those are WEAPON-local: on gloves or a helmet the affix is global, so preferring the
        # local id there would query a stat the piece can never roll — the same zero-results bug,
        # just moved to another slot.
        if prefer_local and _LOCAL_DEFENCE_STAT.search(signature):
            local = self._by_key.get((domain, f"{signature} (local)"), [])
            if len(local) == 1:
                return local[0], None
        matches = self._by_key.get((domain, signature), [])
        if not matches and domain in {"crafted", "desecrated"}:
            matches = self._by_key.get(("explicit", signature), [])
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, f"ambiguous stat signature ({len(matches)} candidates)"
        return None, "no matching Trade stat"


@dataclass
class ResolvedFilter:
    text: str
    stat_id: str
    domain: str
    value: float | None
    locked: bool


@dataclass
class QueryPlan:
    resolved: list[ResolvedFilter]
    ignored: list[str]
    unresolved: list[dict[str, Any]]


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    lowered = strip_trade_markup(text).lower()
    return any(pattern.lower() in lowered for pattern in patterns)


# Item classes whose defence affixes roll as LOCAL stats on Trade.
LOCAL_DEFENCE_CLASSES = frozenset(
    {"body armour", "helmet", "gloves", "boots", "shield", "buckler", "focus"}
)
# The stat families that are actually local on armour. Deliberately narrow: other stats carry
# "(Local)" twins too (Accuracy Rating, Attack Speed), but those are weapon-local and must keep
# resolving to their global id on armour.
_LOCAL_DEFENCE_STAT = re.compile(r"\b(?:armour|evasion|energy shield|block chance)\b")
_DEFENCE_HEADER = re.compile(
    r"^(?:Armour|Evasion(?: Rating)?|Energy Shield)\s*:\s*\d+", re.IGNORECASE | re.MULTILINE
)


def carries_local_defences(item: ParsedItem) -> bool:
    """True when this item's Armour/Evasion/Energy Shield affixes are local rather than global.

    Decided by the item's own defence header ("Energy Shield: 476"), which only appears on gear
    that has base defences, with the item class as a fallback for text that omits the header.
    """
    if item.item_class and item.item_class.strip().lower() in LOCAL_DEFENCE_CLASSES:
        return True
    return bool(_DEFENCE_HEADER.search(item.raw_text or ""))


def build_query_plan(
    item: ParsedItem,
    catalog: StatCatalog,
    *,
    lock_patterns: Iterable[str] = (),
    ignore_patterns: Iterable[str] = (),
) -> QueryPlan:
    resolved: list[ResolvedFilter] = []
    ignored: list[str] = []
    unresolved: list[dict[str, Any]] = []
    explicit_locks = tuple(lock_patterns)
    prefer_local = carries_local_defences(item)
    for modifier in item.modifiers:
        if _matches_any(modifier.text, ignore_patterns):
            ignored.append(modifier.text)
            continue
        auto_locked = modifier.domain == "rune" or _matches_any(modifier.text, AUTO_LOCK_PATTERNS)
        locked = auto_locked or _matches_any(modifier.text, explicit_locks)
        if item.rarity == "Unique" and modifier.domain == "unique" and not locked:
            ignored.append(modifier.text)
            continue
        entry, reason = catalog.resolve(modifier, prefer_local=prefer_local)
        if entry is None:
            unresolved.append(
                {
                    "text": modifier.text,
                    "domain": modifier.domain,
                    "reason": reason or "unknown",
                    "locked": locked,
                }
            )
            continue
        resolved.append(ResolvedFilter(modifier.text, entry.stat_id, entry.domain, modifier.value, locked))
    return QueryPlan(resolved, ignored, unresolved)


def _trade_value(value: float | None, *, exact: bool, tolerance: float) -> dict[str, float]:
    if value is None:
        return {}
    if exact:
        return {"min": value, "max": value}
    adjusted = value * tolerance
    if value >= 0:
        adjusted = math.floor(adjusted) if value.is_integer() else adjusted
    else:
        adjusted = math.ceil(adjusted) if value.is_integer() else adjusted
    return {"min": adjusted}


def build_filter_tiers(plan: QueryPlan, tolerance: float) -> list[tuple[str, list[dict[str, Any]]]]:
    def compile_filters(filters: Iterable[ResolvedFilter], relax: bool) -> list[dict[str, Any]]:
        output = []
        for entry in filters:
            exact = entry.locked or not relax
            output.append({"id": entry.stat_id, "value": _trade_value(entry.value, exact=exact, tolerance=tolerance), "disabled": False})
        return output

    locked = [entry for entry in plan.resolved if entry.locked]
    tiers = [("exact", compile_filters(plan.resolved, False))]
    if any(not entry.locked for entry in plan.resolved):
        tiers.append((f"relaxed-{tolerance:g}", compile_filters(plan.resolved, True)))
    if locked and len(locked) != len(plan.resolved):
        tiers.append(("core-only", compile_filters(locked, False)))
    unique: list[tuple[str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for name, filters in tiers:
        fingerprint = json.dumps(filters, sort_keys=True)
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append((name, filters))
    return unique


def build_search_query(item: ParsedItem, stats: list[dict[str, Any]], status: str) -> dict[str, Any]:
    query: dict[str, Any] = {"status": {"option": status}, "filters": {}}
    if item.rarity == "Unique" and item.name:
        query["name"] = item.name
        if item.base_type and item.base_type != item.name:
            query["type"] = item.base_type
    elif item.base_type:
        query["type"] = item.base_type
    if stats:
        query["stats"] = [{"type": "and", "filters": stats}]
    if item.rarity in {"Rare", "Magic", "Normal"}:
        query.setdefault("filters", {}).setdefault("type_filters", {}).setdefault("filters", {})["rarity"] = {"option": item.rarity.lower()}
    if item.waystone_tier is not None:
        query.setdefault("filters", {}).setdefault("map_filters", {}).setdefault("filters", {})["map_tier"] = {"min": item.waystone_tier, "max": item.waystone_tier}
    if item.gem_level is not None and "Uncut" not in item.base_type:
        query.setdefault("filters", {}).setdefault("misc_filters", {}).setdefault("filters", {})["gem_level"] = {"min": item.gem_level}
    if item.is_corrupted:
        query.setdefault("filters", {}).setdefault("misc_filters", {}).setdefault("filters", {})["corrupted"] = {"option": "true"}
    return query


def _listing(entry: dict[str, Any]) -> dict[str, Any]:
    listing = entry.get("listing", {})
    price = listing.get("price", {})
    account = listing.get("account", {})
    item = entry.get("item", {})
    return {
        "price_amount": price.get("amount"),
        "price_currency": price.get("currency"),
        "instant_buyout_fee": listing.get("fee"),
        "indexed": listing.get("indexed"),
        "seller_account": account.get("name"),
        "seller_ign": account.get("lastCharacterName"),
        "name": item.get("name"),
        "type_line": item.get("typeLine"),
        "base_type": item.get("baseType"),
        "corrupted": item.get("corrupted", False),
        "implicit_mods": item.get("implicitMods", []),
        "explicit_mods": item.get("explicitMods", []),
        "rune_mods": item.get("runeMods", []),
        "enchant_mods": item.get("enchantMods", []),
    }


def summarize_prices(listings: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for listing in listings:
        amount, currency = listing.get("price_amount"), listing.get("price_currency")
        if isinstance(amount, (int, float)) and currency:
            groups.setdefault(str(currency), []).append(float(amount))
    summary: dict[str, Any] = {}
    for currency, prices in groups.items():
        ordered = sorted(prices)
        summary[currency] = {
            "lowest": ordered[0],
            "top_n_median": statistics.median(ordered),
            "sample_size": len(ordered),
        }
    return summary


def search_trade(
    item: ParsedItem,
    stats: list[dict[str, Any]],
    *,
    league: str,
    status: str,
    limit: int,
) -> dict[str, Any]:
    query = build_search_query(item, stats, status)
    payload = {"query": query, "sort": {"price": "asc"}}
    endpoint = f"{TRADE_API_BASE}/search/{urllib.parse.quote(league)}"
    data, _ = http_request(endpoint, method="POST", data=payload)
    query_id = str(data.get("id", ""))
    ids = list(data.get("result", []))[:limit]
    listings: list[dict[str, Any]] = []
    for start in range(0, len(ids), 10):
        chunk = ids[start : start + 10]
        fetched, _ = http_request(f"{TRADE_API_BASE}/fetch/{','.join(chunk)}?query={query_id}")
        listings.extend(_listing(entry) for entry in fetched.get("result", []) if entry)
    return {
        "total_listings": int(data.get("total", 0)),
        "listings": listings,
        "price_summary": summarize_prices(listings),
        "trade_url": f"{TRADE_WEB_BASE}/search/poe2/{urllib.parse.quote(league)}/{query_id}" if query_id else "",
        "query": payload,
    }


def _bulk_tag(item: ParsedItem) -> str | None:
    for candidate in (item.name, item.base_type):
        tag = CURRENCY_TAGS.get(candidate.lower().strip())
        if tag:
            return tag
    match = re.search(r"uncut\s+(skill|spirit|support)\s+gem\s*\(level\s*(\d+)\)", item.name.lower())
    return f"uncut-{match.group(1)}-gem-{match.group(2)}" if match else None


def search_bulk(item: ParsedItem, *, league: str, status: str) -> dict[str, Any] | None:
    tag = _bulk_tag(item)
    if not tag:
        return None
    bulk_status = "online" if status in {"securable", "available", "online"} else status
    payload = {"engine": "new", "query": {"status": {"option": bulk_status}, "have": ["divine", "exalted", "chaos"], "want": [tag], "minimum": 1}, "sort": {"have": "asc"}}
    data, _ = http_request(f"{TRADE_API_BASE}/exchange/{urllib.parse.quote(league)}", method="POST", data=payload)
    listings = []
    for entry in data.get("result", {}).values():
        offers = entry.get("listing", {}).get("offers", [])
        if not offers:
            continue
        offer = offers[0]
        exchange, wanted = offer.get("exchange", {}), offer.get("item", {})
        amount = float(exchange.get("amount", 0)) / max(float(wanted.get("amount", 1)), 1.0)
        listings.append({"price_amount": amount, "price_currency": exchange.get("currency"), "stock": wanted.get("stock")})
    return {"tier": "bulk", "total_listings": len(listings), "listings": listings, "price_summary": summarize_prices(listings), "trade_url": f"{TRADE_WEB_BASE}/exchange/poe2/{urllib.parse.quote(league)}", "query": payload}


def check_item(
    item: ParsedItem,
    catalog: StatCatalog,
    *,
    league: str,
    status: str,
    limit: int,
    match_mode: str,
    tolerance: float,
    lock_patterns: Iterable[str],
    ignore_patterns: Iterable[str],
    plan_only: bool,
) -> dict[str, Any]:
    bulk = None if plan_only else search_bulk(item, league=league, status=status)
    if bulk is not None:
        return {"item": {"name": item.name, "base_type": item.base_type}, "league": league, "status": status, "attempts": [bulk], "selected_tier": "bulk"}
    plan = build_query_plan(item, catalog, lock_patterns=lock_patterns, ignore_patterns=ignore_patterns)
    tiers = build_filter_tiers(plan, tolerance)
    if match_mode == "base":
        tiers = [("base-only", [])]
    elif match_mode == "exact":
        tiers = tiers[:1]
    result = {
        "item": {"name": item.name, "base_type": item.base_type, "rarity": item.rarity, "corrupted": item.is_corrupted},
        "league": league,
        "status": status,
        "query_plan": {
            "resolved": [asdict(entry) for entry in plan.resolved],
            "ignored": plan.ignored,
            "unresolved": plan.unresolved,
        },
        "attempts": [],
        "selected_tier": None,
    }
    unresolved_locked = [entry for entry in plan.unresolved if entry["locked"]]
    if unresolved_locked:
        result["blocked_reason"] = "one or more locked modifiers could not be resolved"
    if plan_only:
        result["planned_tiers"] = [{"name": name, "filters": filters} for name, filters in tiers]
        return result
    if unresolved_locked:
        return result
    for name, filters in tiers:
        attempt = search_trade(item, filters, league=league, status=status, limit=limit)
        attempt["tier"] = name
        result["attempts"].append(attempt)
        if attempt["total_listings"]:
            result["selected_tier"] = name
            break
    return result


def decode_pob(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return text
    padded = text.strip() + "=" * (-len(text.strip()) % 4)
    try:
        return zlib.decompress(base64.urlsafe_b64decode(padded)).decode("utf-8")
    except Exception as exc:
        raise ValueError("Input is neither PoB XML nor a valid PoB code") from exc


def extract_active_pob_items(text: str) -> list[tuple[str, str]]:
    root = ET.fromstring(decode_pob(text))
    items = root.find("Items")
    if items is None:
        raise ValueError("PoB XML has no Items section")
    active_id = items.get("activeItemSet")
    item_set = next((node for node in items.findall("ItemSet") if node.get("id") == active_id), None)
    if item_set is None:
        item_set = next(iter(items.findall("ItemSet")), None)
    if item_set is None:
        raise ValueError("PoB XML has no item set")
    item_text = {node.get("id"): node.text or "" for node in items.findall("Item")}
    active: list[tuple[str, str]] = []
    for slot in item_set.findall("Slot"):
        item_id = slot.get("itemId")
        if item_id and item_id != "0" and item_id in item_text:
            active.append((slot.get("name", "Unknown"), item_text[item_id]))
    return active


def _load_inputs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.pob:
        return [{"label": slot, "text": text} for slot, text in extract_active_pob_items(Path(args.pob).read_text(encoding="utf-8"))]
    if args.batch:
        payload = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Batch file must contain a JSON list")
        output = []
        for index, entry in enumerate(payload, 1):
            if isinstance(entry, str):
                output.append({"label": f"item-{index}", "text": entry})
            elif isinstance(entry, dict) and isinstance(entry.get("text"), str):
                output.append({"label": entry.get("label", f"item-{index}"), **entry})
            else:
                raise ValueError(f"Invalid batch entry #{index}")
        return output
    if args.input_file:
        return [{"label": Path(args.input_file).stem, "text": Path(args.input_file).read_text(encoding="utf-8")}]
    text = args.item_text
    if text is None and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        raise ValueError("Provide item text, --input-file, --batch, or --pob")
    return [{"label": "item", "text": text}]


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(path, payload)


def format_pretty(report: dict[str, Any]) -> str:
    lines = [f"# PoE 2 price check — {report['league']}", f"Status: {STATUS_DISPLAY.get(report['status'], report['status'])}"]
    for result in report["results"]:
        item = result["item"]
        lines.extend(["", f"## {result['label']}: {item.get('name') or item.get('base_type')}"])
        plan = result.get("query_plan", {})
        if plan.get("resolved"):
            locked = [entry["text"] for entry in plan["resolved"] if entry["locked"]]
            if locked:
                lines.append("Locked: " + "; ".join(locked))
        if plan.get("unresolved"):
            lines.append("Unresolved: " + "; ".join(entry["text"] for entry in plan["unresolved"]))
        if result.get("blocked_reason") and "planned_tiers" not in result:
            lines.append(f"Blocked: {result['blocked_reason']}; no market query was sent.")
            continue
        if "planned_tiers" in result:
            lines.append("Plan only: " + ", ".join(tier["name"] for tier in result["planned_tiers"]))
            continue
        attempts = result.get("attempts", [])
        selected = attempts[-1] if attempts else {}
        lines.append(f"Selected tier: {result.get('selected_tier') or 'no matches'}")
        lines.append(f"Listed: {selected.get('total_listings', 0)}")
        for currency, stats in selected.get("price_summary", {}).items():
            lines.append(f"{currency}: lowest {stats['lowest']:g}; top-{stats['sample_size']} median {stats['top_n_median']:g}")
        if selected.get("trade_url"):
            lines.append(selected["trade_url"])
    return "\n".join(lines)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PoE 2 modifier-aware official Trade price checker")
    parser.add_argument("item_text", nargs="?")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--input-file")
    sources.add_argument("--batch", help="JSON list of item texts or {label,text} objects")
    sources.add_argument("--pob", help="PoB XML or .pobcode; queries the active equipment set")
    parser.add_argument("-l", "--league")
    parser.add_argument("-s", "--status", default="securable", choices=sorted(STATUS_MAP))
    parser.add_argument("-n", "--limit", type=int, default=10)
    parser.add_argument("-f", "--format", choices=("pretty", "json"), default="pretty")
    parser.add_argument("--match", choices=("tiered", "exact", "base"), default="tiered")
    parser.add_argument("--tolerance", type=float, default=0.8, help="Preferred-mod minimum multiplier in relaxed tier")
    parser.add_argument("--lock", action="append", default=[], help="Substring of a modifier that must stay exact; repeatable")
    parser.add_argument("--ignore", action="append", default=[], help="Substring of a modifier to omit; repeatable")
    parser.add_argument("--plan-only", action="store_true", help="Resolve modifiers and show queries without searching")
    parser.add_argument("--output", help="Atomic JSON checkpoint path, updated after each item")
    parser.add_argument(
        "--stats-cache",
        default=str(Path(tempfile.gettempdir()) / "poe2-price-check-stats.json"),
        help="Cached official stat catalog path",
    )
    parser.add_argument("--refresh-stats", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    if not 0 < args.tolerance <= 1:
        parser.error("--tolerance must be greater than 0 and at most 1")
    try:
        inputs = _load_inputs(args)
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    league = args.league or ("plan-only" if args.plan_only else get_default_league())
    status = STATUS_MAP[args.status]
    if args.match == "base" or args.plan_only and not any(parse_item_text(entry["text"]).modifiers for entry in inputs):
        catalog = StatCatalog([])
    else:
        catalog = StatCatalog.fetch(
            cache_path=Path(args.stats_cache),
            refresh=args.refresh_stats,
        )
    max_tiers = 3 if args.match == "tiered" else 1
    if not args.plan_only:
        _stderr(f"League: {league}; items: {len(inputs)}; request budget: up to {len(inputs) * max_tiers * 3 + 2}.")
    report: dict[str, Any] = {"version": 2, "league": league, "status": status, "results": []}
    output_path = Path(args.output) if args.output else None
    for entry in inputs:
        item = parse_item_text(entry["text"])
        result = check_item(
            item,
            catalog,
            league=league,
            status=status,
            limit=args.limit,
            match_mode=args.match,
            tolerance=float(entry.get("tolerance", args.tolerance)),
            lock_patterns=[*args.lock, *entry.get("lock", [])],
            ignore_patterns=[*args.ignore, *entry.get("ignore", [])],
            plan_only=args.plan_only,
        )
        result["label"] = entry.get("label", "item")
        report["results"].append(result)
        if output_path:
            _write_checkpoint(output_path, report)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_pretty(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
