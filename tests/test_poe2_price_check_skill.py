from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_trade_v2() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / ".agents"
        / "skills"
        / "poe2-price-check"
        / "scripts"
        / "trade_v2.py"
    )
    spec = importlib.util.spec_from_file_location("poe2_price_check_trade_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trade = _load_trade_v2()


def test_parse_advanced_clipboard_keeps_modifier_domains_and_values() -> None:
    item = trade.parse_item_text(
        """Item Class: Amulets
Rarity: Rare
Grove Heart
Solar Amulet
--------
Item Level: 81
--------
{ Prefix Modifier \"Virile\" (Tier: 2) }
+95(85-99) to maximum Life
{ Suffix Modifier \"of Skill\" (Tier: 1) }
+5 to Level of all Physical Spell Skills
{ Rune Modifier }
5% increased Movement Speed
--------
Corrupted
"""
    )

    assert item.name == "Grove Heart"
    assert item.base_type == "Solar Amulet"
    assert item.item_level == 81
    assert item.is_corrupted is True
    assert [(mod.domain, mod.value) for mod in item.modifiers] == [
        ("explicit", 95.0),
        ("explicit", 5.0),
        ("rune", 5.0),
    ]


def test_parse_plain_pob_item_understands_implicit_and_rune_markup() -> None:
    item = trade.parse_item_text(
        """Rarity: RARE
Grove Heart
Solar Amulet
Implicits: 2
+12 to all Attributes
{enchant}{rune}5% increased Movement Speed
+5 to Level of all Physical Spell Skills
+95 to maximum Life
"""
    )

    assert [(mod.domain, mod.text) for mod in item.modifiers] == [
        ("implicit", "+12 to all Attributes"),
        ("rune", "5% increased Movement Speed"),
        ("explicit", "+5 to Level of all Physical Spell Skills"),
        ("explicit", "+95 to maximum Life"),
    ]


def _catalog() -> object:
    return trade.StatCatalog.from_api(
        {
            "result": [
                {
                    "entries": [
                        {"id": "explicit.stat_life", "text": "+# to maximum Life"},
                        {
                            "id": "explicit.stat_skill",
                            "text": "+# to Level of all Physical Spell Skills",
                        },
                        {"id": "rune.stat_speed", "text": "#% increased Movement Speed"},
                    ]
                }
            ]
        }
    )


def test_tiered_plan_locks_skill_level_and_runes_but_relaxes_other_mods() -> None:
    item = trade.ParsedItem(
        raw_text="",
        modifiers=[
            trade.ParsedModifier("+95 to maximum Life", "explicit", 95.0),
            trade.ParsedModifier(
                "+5 to Level of all Physical Spell Skills", "explicit", 5.0
            ),
            trade.ParsedModifier("5% increased Movement Speed", "rune", 5.0),
        ],
    )
    plan = trade.build_query_plan(item, _catalog())
    tiers = trade.build_filter_tiers(plan, 0.8)

    assert [entry.locked for entry in plan.resolved] == [False, True, True]
    assert [name for name, _filters in tiers] == ["exact", "relaxed-0.8", "core-only"]
    relaxed = tiers[1][1]
    assert relaxed[0]["value"] == {"min": 76}
    assert relaxed[1]["value"] == {"min": 5.0, "max": 5.0}
    assert relaxed[2]["value"] == {"min": 5.0, "max": 5.0}
    assert len(tiers[2][1]) == 2


def test_search_query_contains_stats_rarity_and_corruption() -> None:
    item = trade.ParsedItem(
        raw_text="",
        rarity="Rare",
        base_type="Solar Amulet",
        is_corrupted=True,
    )
    stats = [{"id": "explicit.stat_life", "value": {"min": 76}}]
    payload = trade.build_search_query(item, stats, "securable")

    assert payload["type"] == "Solar Amulet"
    assert payload["stats"][0]["filters"] == stats
    assert payload["filters"]["type_filters"]["filters"]["rarity"] == {
        "option": "rare"
    }
    assert payload["filters"]["misc_filters"]["filters"]["corrupted"] == {
        "option": "true"
    }


def test_rate_limiter_waits_once_for_longest_active_rule() -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = trade.RateLimiter(clock=lambda: now[0], sleeper=sleep)
    headers = {
        "X-Rate-Limit-Rules": "account,ip",
        "X-Rate-Limit-account": "2:10:60,5:60:120",
        "X-Rate-Limit-account-State": "2:10:4,5:60:30",
        "X-Rate-Limit-ip": "3:10:60",
        "X-Rate-Limit-ip-State": "3:10:10",
    }

    assert limiter.update_from_headers(headers) == 30.5
    assert limiter.wait_if_needed() == 30.5
    assert sleeps == [30.5]
    assert limiter.wait_if_needed() == 0.0


def test_extract_active_pob_items_uses_only_active_item_set() -> None:
    xml = """<PathOfBuilding><Items activeItemSet="2">
<Item id="1">one</Item><Item id="2">two</Item><Item id="3">three</Item>
<ItemSet id="1"><Slot name="Helmet" itemId="1" /></ItemSet>
<ItemSet id="2"><Slot name="Helmet" itemId="2" /><Slot name="Body Armour" itemId="3" /></ItemSet>
</Items></PathOfBuilding>"""

    assert trade.extract_active_pob_items(xml) == [
        ("Helmet", "two"),
        ("Body Armour", "three"),
    ]


def test_price_summary_names_sample_median_accurately() -> None:
    summary = trade.summarize_prices(
        [
            {"price_amount": 1, "price_currency": "divine"},
            {"price_amount": 3, "price_currency": "divine"},
        ]
    )

    assert summary["divine"] == {"lowest": 1.0, "top_n_median": 2.0, "sample_size": 2}


def test_stat_catalog_cache_avoids_repeated_api_requests(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    payload = {
        "result": [
            {
                "entries": [
                    {"id": "explicit.stat_life", "text": "+# to maximum Life"}
                ]
            }
        ]
    }

    def fake_request(url: str) -> tuple[dict[str, object], object]:
        calls.append(url)
        return payload, {}

    monkeypatch.setattr(trade, "http_request", fake_request)
    cache = tmp_path / "stats.json"

    first = trade.StatCatalog.fetch(cache_path=cache)
    second = trade.StatCatalog.fetch(cache_path=cache)

    assert len(first.entries) == len(second.entries) == 1
    assert calls == [f"{trade.TRADE_API_BASE}/data/stats"]


def test_unresolved_locked_modifier_blocks_market_search(monkeypatch) -> None:
    item = trade.ParsedItem(
        raw_text="",
        rarity="Rare",
        base_type="Iron Crown",
        modifiers=[trade.ParsedModifier("+50 to all Attributes", "rune", 50.0)],
    )

    def unexpected_search(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("a locked unresolved modifier must block the search")

    monkeypatch.setattr(trade, "search_trade", unexpected_search)
    result = trade.check_item(
        item,
        trade.StatCatalog([]),
        league="Test",
        status="securable",
        limit=10,
        match_mode="tiered",
        tolerance=0.8,
        lock_patterns=[],
        ignore_patterns=[],
        plan_only=False,
    )

    assert result["blocked_reason"] == "one or more locked modifiers could not be resolved"
    assert result["attempts"] == []
    assert result["query_plan"]["unresolved"][0]["locked"] is True
