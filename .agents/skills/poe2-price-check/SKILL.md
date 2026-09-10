---
name: poe2-price-check
description: >-
  Check Path of Exile 2 (PoE 2) item market prices, live listings, instant buyout fees, and seller details by inputting raw item clipboard text.
  Supports automatic league detection (defaults to current challenge league) and trade status filters (defaults to Instant Buyout / securable).
  Use this skill whenever the user asks to price check a PoE 2 item, evaluate gear value, or search official PoE 2 trade listings.
---

# Path of Exile 2 Price Check

Use `scripts/price_check.py` for official PoE 2 Trade searches. It accepts advanced
clipboard text, plain PoB item text, a JSON batch, or the active equipment set in a
PoB file.

## Required workflow

1. Identify attributes that may not change. Skill-level modifiers and rune modifiers
   are locked automatically. Pass every other build-critical phrase with `--lock`.
2. Put flavour or non-critical modifiers behind `--ignore` when the user permits it.
3. Use the default `--match tiered` search. It tries exact rolls, then 80% minima for
   preferred modifiers, then locked core modifiers only. Never describe a core-only
   result as an exact match.
4. Prefer one `--batch` or `--pob` run over many CLI invocations. Preserve the JSON
   checkpoint and report unresolved modifiers instead of silently dropping them.
   The checker blocks an item search when an unresolved modifier is locked.
5. Label `top_n_median` as the median of the fetched cheapest sample, not the median
   of the entire market. Keep currencies separate unless a sourced conversion rate is
   available.

The CLI waits once for the longest active GGG rate-limit rule, honors HTTP 429
`Retry-After`, retries conservatively, logs its request budget, and writes `--output`
atomically after each completed item.

## Commands

```bash
uv run python .agents/skills/poe2-price-check/scripts/price_check.py --input-file item.txt --lock "Level of all" --ignore "Resistance"
```

```bash
uv run python .agents/skills/poe2-price-check/scripts/price_check.py --pob build.pobcode --league "Forbidden Rites" --status available --format json --output outputs/build_prices.json
```

```bash
uv run python .agents/skills/poe2-price-check/scripts/price_check.py --batch items.json --match tiered --tolerance 0.8 --limit 10
```

Important options:

- `--match tiered|exact|base`: modifier-aware fallback, exact-only, or base-only.
- `--tolerance 0.8`: minimum multiplier for non-locked rolls in the relaxed tier.
- `--lock TEXT`, `--ignore TEXT`: repeatable substring rules.
- `--plan-only`: resolve stats and show tiers without running item searches.
- `--stats-cache`: reuse the official stat catalog for 24 hours; stale cache is a
  fallback when refresh fails. Use `--refresh-stats` only when necessary.
- `--status securable|available|onlineleague|online|any`: defaults to instant buyout.
- `--pob`: reads only the active PoB item set.

Batch entries may be strings or objects containing `text`, `label`, and optional
`lock`, `ignore`, and `tolerance` arrays/values. See [Query Modes](./references/query_modes.md)
before interpreting fallback results.

## References

- [Query Modes](./references/query_modes.md)
- [Status Filters](./references/status_filters.md)
- [Trade API and Rate Limits](./references/trade_api.md)
