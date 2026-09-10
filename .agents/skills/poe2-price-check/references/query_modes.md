# Query Modes and Result Interpretation

## Tiered matching

The default `--match tiered` mode runs increasingly broad searches and stops at the
first tier with listings:

1. `exact`: every resolved modifier is present at the supplied value.
2. `relaxed-0.8`: locked modifiers remain exact; other positive integer rolls become
   minimums at `floor(original × tolerance)`.
3. `core-only`: only locked modifiers remain.

Skill-level and rune modifiers are automatically locked. Use repeatable `--lock`
arguments for other non-negotiable properties. `--ignore` removes permitted noise
before tiers are built.

`core-only` answers whether the build-critical combination exists. It does not prove
that the remaining defensive, attribute, or resistance rolls coexist on that item.
Always show the selected tier alongside the price.

## Exact and base modes

- `--match exact` sends one modifier-aware query and never relaxes it.
- `--match base` searches only the unique name or base type, rarity, corruption, and
  supported item metadata. Use this for broad market context, not build validation.

## Unresolved modifiers

Trade queries require official stat IDs from `/api/trade2/data/stats`. The parser
normalizes numeric values and resolves each modifier within its domain: explicit,
implicit, rune, enchant, crafted, or desecrated.

An unresolved or ambiguous modifier is listed in `query_plan.unresolved`. It is never
silently treated as matched. Review its wording or use a deliberate `--ignore` only
when the user has said it is non-critical.

If an unresolved modifier is locked, the checker records `blocked_reason` and does
not send a market search for that item. This prevents a skill level, rune, or other
critical property from being silently omitted.

## Batch schema

```json
[
  {
    "label": "Amulet",
    "text": "Rarity: RARE\n...",
    "lock": ["Level of all"],
    "ignore": ["Resistance"],
    "tolerance": 0.8
  }
]
```

Prefer batch and PoB modes because the process shares league/stat discovery and its
header-driven limiter across all items. `--output` writes a recoverable checkpoint
after every completed item.

## Price summaries

`lowest` is the cheapest fetched priced listing. `top_n_median` is the median of the
fetched cheapest sample only. `sample_size` states that sample size. Different
currencies remain separate; no implicit exchange conversion is performed.
