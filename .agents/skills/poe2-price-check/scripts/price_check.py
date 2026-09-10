#!/usr/bin/env python3
"""
PoE 2 Item Price Checker CLI & Python Tool
Learned from Exiled-Exchange-2 & Awakened PoE Trade architecture.

Features:
- Parses raw Path of Exile 2 clipboard item text (Item Class, Rarity, Names, Base, Ilvl, Quality, Sockets, Modifiers).
- Interacts with official GGG PoE 2 Trade API (trade2/search, trade2/fetch, trade2/exchange).
- Auto-detects current active league (e.g. Forbidden Rites), or user-specified.
- Supports PoE 2 instant buyout & in-person trade filters:
    * instant_buyout (securable) [DEFAULT]
    * instant_and_in_person (available)
    * in_person_league (onlineleague)
    * in_person_online (online)
    * any (any)
- Formats results with price summaries (min, median, max), detailed listings (seller, IGN, age, instant buyout fee), and clickable web trade link.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

USER_AGENT = "pob-mcp/1.0 (contact: github.com/Mangosteen-Yang/pob-mcp)"
TRADE_API_BASE = "https://www.pathofexile.com/api/trade2"
TRADE_WEB_BASE = "https://www.pathofexile.com/trade2"

# Map friendly filter names to GGG query status options
STATUS_MAP: Dict[str, str] = {
    # Instant buyout only (securable)
    "securable": "securable",
    "instant": "securable",
    "instant_buyout": "securable",
    "buyout": "securable",
    # Instant Buyout and In Person (available)
    "available": "available",
    "instant_and_in_person": "available",
    "instant_in_person": "available",
    "both": "available",
    # In Person (Online in League)
    "onlineleague": "onlineleague",
    "in_person_league": "onlineleague",
    "league_online": "onlineleague",
    # In Person (Online)
    "online": "online",
    "in_person_online": "online",
    "in_person": "online",
    # Any / Offline
    "any": "any",
    "offline": "any",
    "all": "any",
}

STATUS_DISPLAY = {
    "securable": "Instant Buyout",
    "available": "Instant Buyout and In Person",
    "onlineleague": "In Person (Online in League)",
    "online": "In Person (Online)",
    "any": "Any (Include Offline)",
}

# Common currency static IDs for bulk exchange
CURRENCY_TAGS: Dict[str, str] = {
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
    "scroll of wisdom": "wisdom",
    "portal scroll": "portal",
}


# ==============================================================================
# Rate Limiter & HTTP Client
# ==============================================================================


class RateLimiter:
    """Sliding window rate limiter adhering to GGG's x-rate-limit headers."""

    def __init__(self):
        self.rules: List[Dict[str, Any]] = []

    def update_from_headers(self, headers: Any):
        rule_names = headers.get("X-Rate-Limit-Rules")
        if not rule_names:
            return

        for name in [r.strip() for r in rule_names.split(",")]:
            rule_str = headers.get(f"X-Rate-Limit-{name}")
            state_str = headers.get(f"X-Rate-Limit-{name}-State")
            if rule_str and state_str:
                # Format: max:window:timeout, e.g. 10:4:60
                limits = [
                    tuple(map(int, part.split(":")))
                    for part in rule_str.split(",")
                ]
                states = [
                    tuple(map(int, part.split(":")))
                    for part in state_str.split(",")
                ]
                for (max_hits, window, timeout), (cur_hits, _, retry_after) in zip(
                    limits, states
                ):
                    if cur_hits >= max_hits:
                        sleep_time = max(retry_after, 1) + 0.5
                        time.sleep(sleep_time)


RATE_LIMITER = RateLimiter()


def http_request(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], Any]:
    """Sends HTTP request with rate limit handling and User-Agent."""
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url, data=body_bytes, headers=req_headers, method=method
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req) as resp:
                RATE_LIMITER.update_from_headers(resp.headers)
                content = resp.read().decode("utf-8")
                return json.loads(content), resp.headers
        except urllib.error.HTTPError as e:
            RATE_LIMITER.update_from_headers(e.headers)
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait_sec = int(retry_after) if retry_after else (attempt + 1) * 3
                time.sleep(wait_sec)
                continue
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} for {url}: {error_body or e.reason}")
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(1)

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} retries")


def get_active_leagues() -> List[Dict[str, str]]:
    """Fetches list of active leagues from PoE 2 Trade API."""
    data, _ = http_request(f"{TRADE_API_BASE}/data/leagues")
    return data.get("result", [])


def get_default_league() -> str:
    """Detects current default temporary league (e.g. Forbidden Rites) or fallback to Standard."""
    try:
        leagues = get_active_leagues()
        # Find first non-Standard, non-Hardcore league
        for lg in leagues:
            lid = lg.get("id", "")
            if lid not in ("Standard", "Hardcore") and not lid.startswith("HC "):
                return lid
        # If none, return first league
        if leagues:
            return leagues[0].get("id", "Standard")
    except Exception:
        pass
    return "Standard"


# ==============================================================================
# Item Parser (PoE 2 Format)
# ==============================================================================


@dataclass
class ParsedItem:
    raw_text: str
    item_class: Optional[str] = None
    rarity: str = "Normal"
    name: str = ""
    base_type: str = ""
    item_level: Optional[int] = None
    quality: Optional[int] = None
    gem_level: Optional[int] = None
    rune_sockets: int = 0
    gem_sockets: int = 0
    waystone_tier: Optional[int] = None
    stack_size: Optional[int] = None
    is_corrupted: bool = False
    is_unidentified: bool = False
    requirements: Dict[str, int] = field(default_factory=dict)
    implicits: List[str] = field(default_factory=list)
    explicits: List[str] = field(default_factory=list)
    runes_embedded: List[str] = field(default_factory=list)


def parse_item_text(text: str) -> ParsedItem:
    """Parses raw PoE 2 clipboard text into a structured ParsedItem."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise ValueError("Item text is empty")

    sections: List[List[str]] = []
    current_sec: List[str] = []
    for line in text.splitlines():
        trimmed = line.strip()
        if re.match(r"^[-—]{4,}$", trimmed):
            if current_sec:
                sections.append(current_sec)
                current_sec = []
        elif trimmed:
            current_sec.append(trimmed)
    if current_sec:
        sections.append(current_sec)

    item = ParsedItem(raw_text=text)

    # 1. Parse Header / Nameplate (Section 0)
    sec0 = sections[0] if sections else []
    name_lines: List[str] = []
    for line in sec0:
        if line.startswith("Item Class:"):
            item.item_class = line.split(":", 1)[1].strip()
        elif line.startswith("Rarity:"):
            item.rarity = line.split(":", 1)[1].strip()
        else:
            name_lines.append(line)

    if len(name_lines) >= 2 and item.rarity in ("Rare", "Unique"):
        item.name = name_lines[0]
        item.base_type = name_lines[1]
    elif len(name_lines) == 1:
        item.name = name_lines[0]
        item.base_type = name_lines[0]
    elif len(name_lines) >= 2:
        item.name = name_lines[0]
        item.base_type = name_lines[-1]

    # Special handling for Uncut Gems: e.g. "Uncut Skill Gem (Level 19)"
    uncut_match = re.search(r"Uncut\s+(Skill|Spirit|Support)\s+Gem\s*\(Level\s*(\d+)\)", text, re.IGNORECASE)
    if uncut_match:
        gem_type = uncut_match.group(1).title()
        item.item_class = f"Uncut {gem_type} Gems"
        item.base_type = f"Uncut {gem_type} Gem"
        item.name = f"Uncut {gem_type} Gem (Level {uncut_match.group(2)})"
        item.gem_level = int(uncut_match.group(2))

    # 2. Iterate remaining sections to extract properties & modifiers
    for sec in sections[1:]:
        sec_text = "\n".join(sec)

        # Check corrupted / unidentified
        if any(line == "Corrupted" for line in sec):
            item.is_corrupted = True
        if any(line == "Unidentified" for line in sec):
            item.is_unidentified = True

        for line in sec:
            # Item Level
            ilvl_match = re.match(r"^Item Level:\s*(\d+)", line)
            if ilvl_match:
                item.item_level = int(ilvl_match.group(1))

            # Quality
            qual_match = re.match(r"^Quality:\s*\+(\d+)%", line)
            if qual_match:
                item.quality = int(qual_match.group(1))

            # Gem Level
            glevel_match = re.match(r"^Level:\s*(\d+)", line)
            if glevel_match and item.gem_level is None:
                item.gem_level = int(glevel_match.group(1))

            # Waystone Tier
            wt_match = re.match(r"^Waystone Tier:\s*(\d+)", line)
            if wt_match:
                item.waystone_tier = int(wt_match.group(1))

            # Sockets
            if line.startswith("Sockets:"):
                # e.g. Sockets: S S S
                gems = len(re.findall(r"[RGBWS]", line))
                if gems > 0:
                    item.gem_sockets = gems

            if line.startswith("Rune Sockets:") or "Rune Socket" in line:
                rs_match = re.search(r"(\d+)", line)
                if rs_match:
                    item.rune_sockets = int(rs_match.group(1))

            # Stack Size
            stack_match = re.match(r"^Stack Size:\s*(\d+)/(\d+)", line)
            if stack_match:
                item.stack_size = int(stack_match.group(1))

            # Requirements
            if line.startswith("Requires:"):
                for req in line[9:].split(","):
                    req = req.strip()
                    m = re.search(r"(Level|Str|Dex|Int)\s*(\d+)", req)
                    if m:
                        item.requirements[m.group(1)] = int(m.group(2))

        # Check for Implicits vs Explicits vs Runes
        if any("{ Implicit Modifier" in l for l in sec):
            for l in sec:
                if not l.startswith("{"):
                    item.implicits.append(l)
        elif any("{ Prefix Modifier" in l or "{ Suffix Modifier" in l or "{ Unique Modifier" in l for l in sec):
            for l in sec:
                if not l.startswith("{") and not re.match(r"^[-—]{4,}$", l):
                    item.explicits.append(l)
        elif any("{ Rune Modifier" in l or "{ Augment Modifier" in l for l in sec):
            for l in sec:
                if not l.startswith("{"):
                    item.runes_embedded.append(l)

    return item


# ==============================================================================
# Trade Query & Results Fetcher
# ==============================================================================


@dataclass
class TradeListing:
    price_amount: float
    price_currency: str
    instant_buyout_fee: Optional[int]
    seller_account: str
    seller_ign: str
    age_str: str
    account_status: str  # online, afk, offline
    item_details: Dict[str, Any]


def format_relative_time(iso_str: str) -> str:
    """Formats ISO timestamp to relative time (e.g. '12m ago', '2h ago')."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
        if diff < 60:
            return f"{int(diff)}s ago"
        elif diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        else:
            return f"{int(diff // 86400)}d ago"
    except Exception:
        return iso_str[:10]


def check_bulk_price(
    item: ParsedItem, league: str, status_option: str, min_stock: int = 1
) -> Optional[Dict[str, Any]]:
    """Checks price using Bulk Exchange API for currency and uncut gems."""
    # Check if item has a known currency tag or is Uncut Gem
    want_tag = None
    clean_name = item.name.lower().strip()
    clean_base = item.base_type.lower().strip()

    if clean_name in CURRENCY_TAGS:
        want_tag = CURRENCY_TAGS[clean_name]
    elif clean_base in CURRENCY_TAGS:
        want_tag = CURRENCY_TAGS[clean_base]
    else:
        # Check uncut gem: e.g. uncut-skill-gem-19
        uncut_match = re.search(r"uncut\s+(skill|spirit|support)\s+gem\s*\(level\s*(\d+)\)", clean_name)
        if uncut_match:
            gem_type = uncut_match.group(1)
            level = uncut_match.group(2)
            want_tag = f"uncut-{gem_type}-gem-{level}"

    if not want_tag:
        return None

    # Bulk API status: online, onlineleague, any
    bulk_status = "online" if status_option in ("securable", "available", "online") else status_option
    payload = {
        "engine": "new",
        "query": {
            "status": {"option": bulk_status},
            "have": ["divine", "exalted", "chaos"],
            "want": [want_tag],
            "minimum": min_stock,
        },
        "sort": {"have": "asc"},
    }

    try:
        url = f"{TRADE_API_BASE}/exchange/{urllib.parse.quote(league)}"
        data, _ = http_request(url, method="POST", data=payload)
    except Exception:
        return None

    result_dict = data.get("result", {})
    if not result_dict:
        return {
            "is_bulk": True,
            "league": league,
            "item_name": item.name,
            "total_listings": 0,
            "listings": [],
            "trade_url": f"{TRADE_WEB_BASE}/exchange/poe2/{urllib.parse.quote(league)}",
        }

    listings: List[TradeListing] = []
    for item_id, entry in result_dict.items():
        listing_info = entry.get("listing", {})
        account_info = listing_info.get("account", {})
        offers = listing_info.get("offers", [])
        if not offers:
            continue
        offer = offers[0]
        exchange = offer.get("exchange", {})
        item_offer = offer.get("item", {})

        price_amount = exchange.get("amount", 0) / max(item_offer.get("amount", 1), 1)
        price_currency = exchange.get("currency", "unknown")
        stock = item_offer.get("stock", 1)

        age = format_relative_time(listing_info.get("indexed", ""))
        status = "online"
        if account_info.get("online"):
            if account_info["online"].get("status") == "afk":
                status = "afk"
        else:
            status = "offline"

        listings.append(
            TradeListing(
                price_amount=round(price_amount, 2),
                price_currency=price_currency,
                instant_buyout_fee=None,
                seller_account=account_info.get("name", "Unknown"),
                seller_ign=account_info.get("lastCharacterName", ""),
                age_str=age,
                account_status=status,
                item_details={"stock": stock},
            )
        )

    listings.sort(key=lambda x: (x.price_currency != "exalted", x.price_amount))
    return {
        "is_bulk": True,
        "league": league,
        "item_name": item.name,
        "total_listings": len(listings),
        "listings": listings,
        "trade_url": f"{TRADE_WEB_BASE}/exchange/poe2/{urllib.parse.quote(league)}",
    }


def query_item_price(
    item: ParsedItem,
    league: str,
    status_option: str = "securable",
    limit: int = 10,
) -> Dict[str, Any]:
    """Queries PoE 2 Trade Search API for an item."""
    # 1. Try bulk exchange first for currency / uncut gems
    bulk_result = check_bulk_price(item, league, status_option)
    if bulk_result and bulk_result.get("total_listings", 0) > 0:
        return bulk_result

    # 2. Build Trade Search query
    query_obj: Dict[str, Any] = {
        "status": {"option": status_option},
        "filters": {},
    }

    if item.rarity == "Unique" and item.name:
        query_obj["name"] = item.name
        if item.base_type and item.base_type != item.name:
            query_obj["type"] = item.base_type
    elif item.base_type:
        query_obj["type"] = item.base_type

    # Specific filters
    if item.waystone_tier is not None:
        query_obj.setdefault("filters", {}).setdefault("map_filters", {}).setdefault("filters", {})["map_tier"] = {
            "min": item.waystone_tier,
            "max": item.waystone_tier,
        }

    if item.gem_level is not None and "Uncut" not in item.base_type:
        query_obj.setdefault("filters", {}).setdefault("misc_filters", {}).setdefault("filters", {})["gem_level"] = {
            "min": item.gem_level
        }

    if item.rarity in ("Rare", "Magic", "Normal"):
        rarity_val = item.rarity.lower()
        query_obj.setdefault("filters", {}).setdefault("type_filters", {}).setdefault("filters", {})["rarity"] = {
            "option": rarity_val
        }

    payload = {
        "query": query_obj,
        "sort": {"price": "asc"},
    }

    search_url = f"{TRADE_API_BASE}/search/{urllib.parse.quote(league)}"
    data, _ = http_request(search_url, method="POST", data=payload)

    query_id = data.get("id", "")
    result_ids: List[str] = data.get("result", [])
    total: int = data.get("total", 0)

    web_url = f"{TRADE_WEB_BASE}/search/poe2/{urllib.parse.quote(league)}/{query_id}" if query_id else ""

    if not result_ids:
        return {
            "is_bulk": False,
            "league": league,
            "status_filter": status_option,
            "status_filter_label": STATUS_DISPLAY.get(status_option, status_option),
            "item_name": item.name or item.base_type,
            "item_class": item.item_class,
            "total_listings": 0,
            "listings": [],
            "trade_url": web_url,
        }

    # Fetch top listings (up to limit, max 10 per fetch chunk)
    fetch_ids = result_ids[: min(limit, 20)]
    chunks = [fetch_ids[i : i + 10] for i in range(0, len(fetch_ids), 10)]

    listings: List[TradeListing] = []
    for chunk in chunks:
        fetch_url = f"{TRADE_API_BASE}/fetch/{','.join(chunk)}?query={query_id}"
        fetch_data, _ = http_request(fetch_url)
        for entry in fetch_data.get("result", []):
            if not entry:
                continue
            listing = entry.get("listing", {})
            price_info = listing.get("price", {})
            account = listing.get("account", {})
            item_data = entry.get("item", {})

            price_amt = price_info.get("amount", 0)
            price_curr = price_info.get("currency", "unknown")
            fee = listing.get("fee")  # Instant buyout fee in gold

            age = format_relative_time(listing.get("indexed", ""))
            status = "online"
            if account.get("online"):
                if account["online"].get("status") == "afk":
                    status = "afk"
            else:
                status = "offline"

            props = {}
            if item_data.get("ilvl"):
                props["ilvl"] = item_data["ilvl"]
            if item_data.get("corrupted"):
                props["corrupted"] = True

            listings.append(
                TradeListing(
                    price_amount=float(price_amt),
                    price_currency=price_curr,
                    instant_buyout_fee=fee,
                    seller_account=account.get("name", "Unknown"),
                    seller_ign=account.get("lastCharacterName", ""),
                    age_str=age,
                    account_status=status,
                    item_details=props,
                )
            )

    return {
        "is_bulk": False,
        "league": league,
        "status_filter": status_option,
        "status_filter_label": STATUS_DISPLAY.get(status_option, status_option),
        "item_name": item.name or item.base_type,
        "item_class": item.item_class,
        "total_listings": total,
        "listings": listings,
        "trade_url": web_url,
    }


# ==============================================================================
# CLI & Formatting
# ==============================================================================


def format_output_pretty(data: Dict[str, Any], item: ParsedItem) -> str:
    """Renders structured price check result in a readable markdown format."""
    lines: List[str] = []
    name = data["item_name"]
    base = item.base_type if item.base_type and item.base_type != name else ""
    header = f"### 💰 PoE 2 Price Check: **{name}**" + (f" ({base})" if base else "")
    lines.append(header)
    lines.append(f"- **League**: `{data['league']}`")

    if not data.get("is_bulk"):
        status_label = data.get("status_filter_label", "Instant Buyout")
        lines.append(f"- **Trade Filter**: `{status_label}` (`{data.get('status_filter')}`)")

    total = data["total_listings"]
    lines.append(f"- **Total Listed**: `{total}`")

    listings: List[TradeListing] = data.get("listings", [])
    if not listings:
        lines.append("\n> ⚠️ **No active listings found matching this criteria.**")
        if data.get("trade_url"):
            lines.append(f"> [View Search on Trade Website]({data['trade_url']})")
        return "\n".join(lines)

    # Calculate summary stats (group by currency)
    currencies = {}
    for l in listings:
        currencies.setdefault(l.price_currency, []).append(l.price_amount)

    summary_parts = []
    for curr, prices in currencies.items():
        sorted_p = sorted(prices)
        p_min = sorted_p[0]
        p_med = sorted_p[len(sorted_p) // 2]
        if len(sorted_p) == 1:
            summary_parts.append(f"**{p_min:g} {curr}**")
        else:
            summary_parts.append(f"**{p_min:g} ~ {p_med:g} {curr}** (Lowest: `{p_min:g}`, Median: `{p_med:g}`)")

    lines.append(f"- **Price Estimate**: " + " | ".join(summary_parts))
    if data.get("trade_url"):
        lines.append(f"- **Official Trade Link**: [Open in Browser]({data['trade_url']})")

    lines.append("\n#### 📋 Top Current Listings")
    lines.append("| # | Price | Instant Buyout Fee | Age | Seller (Account / IGN) | Status |")
    lines.append("|---|---|---|---|---|---|")

    for idx, l in enumerate(listings[:10], 1):
        fee_str = f"{l.instant_buyout_fee:,} Gold" if l.instant_buyout_fee is not None else "-"
        seller = f"`{l.seller_account}`" + (f" ({l.seller_ign})" if l.seller_ign else "")
        status_icon = "🟢" if l.account_status == "online" else ("🟡 AFK" if l.account_status == "afk" else "⚪")
        price_str = f"**{l.price_amount:g} {l.price_currency}**"
        lines.append(f"| {idx} | {price_str} | {fee_str} | {l.age_str} | {seller} | {status_icon} |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="PoE 2 Item Price Checker (Learned from Exiled-Exchange-2)"
    )
    parser.add_argument(
        "item_text",
        nargs="?",
        default=None,
        help="Raw clipboard text of the item. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "-l",
        "--league",
        default=None,
        help="League name (default: auto-detected current temporary league, e.g. Forbidden Rites)",
    )
    parser.add_argument(
        "-s",
        "--status",
        "--filter",
        dest="status",
        default="securable",
        choices=list(STATUS_MAP.keys()),
        help="Trade filter: instant_buyout (securable) [DEFAULT], available, onlineleague, online, any",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max number of listings to return (default: 10)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["pretty", "json"],
        default="pretty",
        help="Output format (pretty or json)",
    )

    args = parser.parse_args()

    # Read item text
    raw_text = args.item_text
    if not raw_text:
        if not sys.stdin.isatty():
            raw_text = sys.stdin.read().strip()
        else:
            parser.error("No item text provided via argument or stdin.")

    # Resolve status
    status_option = STATUS_MAP.get(args.status.lower(), "securable")

    # Resolve league
    league = args.league
    if not league:
        league = get_default_league()

    # Parse item
    try:
        parsed_item = parse_item_text(raw_text)
    except Exception as e:
        sys.stderr.write(f"Error parsing item text: {e}\n")
        sys.exit(1)

    # Query trade
    try:
        result = query_item_price(
            parsed_item,
            league=league,
            status_option=status_option,
            limit=args.limit,
        )
    except Exception as e:
        sys.stderr.write(f"Error querying PoE 2 Trade API: {e}\n")
        sys.exit(1)

    # Format output
    if args.format == "json":
        # Make listings serializable
        if "listings" in result:
            result["listings"] = [
                {
                    "price_amount": l.price_amount,
                    "price_currency": l.price_currency,
                    "instant_buyout_fee": l.instant_buyout_fee,
                    "seller_account": l.seller_account,
                    "seller_ign": l.seller_ign,
                    "age": l.age_str,
                    "status": l.account_status,
                    "details": l.item_details,
                }
                for l in result["listings"]
            ]
        print(json.dumps(result, indent=2))
    else:
        print(format_output_pretty(result, parsed_item))


if __name__ == "__main__":
    # Keep the original filename stable for existing callers while routing the
    # CLI to the modifier-aware, rate-limit-safe implementation.
    from trade_v2 import main as main_v2

    raise SystemExit(main_v2())
