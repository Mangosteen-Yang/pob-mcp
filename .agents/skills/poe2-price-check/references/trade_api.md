# PoE 2 Trade API Technical Reference

Learned and adapted from Exiled-Exchange-2 and official GGG Trade API standards.

## Endpoints Overview

All Path of Exile 2 trade endpoints use the prefix `https://www.pathofexile.com/api/trade2`:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/trade2/data/leagues` | `GET` | Retrieve list of active PoE 2 leagues and challenge leagues. |
| `/api/trade2/data/static` | `GET` | Static categories and IDs for bulk exchange (currencies, uncut gems, etc.). |
| `/api/trade2/data/items` | `GET` | List of item bases, unique items, and weapon/armour categories. |
| `/api/trade2/data/stats` | `GET` | Official searchable stat IDs grouped by modifier domain. |
| `/api/trade2/search/{league}` | `POST` | Execute equipment/item query. Returns `id` (Query ID), `result` (IDs array), and `total`. |
| `/api/trade2/fetch/{id1},{id2},...` | `GET` | Fetch item details for up to 10 item IDs with `?query={queryId}`. |
| `/api/trade2/exchange/{league}` | `POST` | Bulk currency and item exchange query. Returns live offers directly. |

## Rate Limits

GGG returns rate limiting rules in HTTP headers:
- `X-Rate-Limit-Rules`: Rules applicable (e.g. `account,ip`).
- `X-Rate-Limit-Account`: Format `max:window:timeout` (e.g. `10:4:60,15:12:300`).
- `X-Rate-Limit-Account-State`: Format `current:window:timeout` (e.g. `1:4:0`).
- When encountering HTTP 429, inspect `Retry-After` header and wait before retrying.

Treat every comma-separated rule window independently, but wait only once for the
longest active retry time. Do not sleep once per exceeded window. Reuse one process
for batch work so the limiter retains state between item searches.

The checker logs an upper request budget before searching. One item-search tier uses
one `POST /search` plus up to two `GET /fetch` requests when the listing limit is 20.
Tiered searches stop after the first tier with results.

## Modifier queries

Use `/data/stats` rather than guessing stat IDs. A modifier query is an `and` group:

```json
{
  "stats": [
    {
      "type": "and",
      "filters": [
        {
          "id": "explicit.stat_...",
          "value": {"min": 5, "max": 5},
          "disabled": false
        }
      ]
    }
  ]
}
```

Resolve within the correct domain (`explicit`, `implicit`, `rune`, `enchant`, etc.).
If a normalized item line maps to zero or multiple stat entries, surface it as
unresolved rather than issuing a falsely precise search.

## Web Trade URLs

Generated trade search URLs for players to inspect in browser:
- Item Search: `https://www.pathofexile.com/trade2/search/poe2/{league}/{query_id}`
- Bulk Exchange: `https://www.pathofexile.com/trade2/exchange/poe2/{league}`
