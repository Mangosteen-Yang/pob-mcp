# PoE 2 Trade Status Filters Reference

Path of Exile 2 introduces an in-game asynchronous trading mechanism with Instant Buyout fees paid in Gold. This introduces distinct trading modes on the official Trade site (`pathofexile.com/trade2`).

## Filter Options

The trade filter dropdown corresponds directly to the `query.status.option` payload sent to the PoE 2 Trade API:

| UI Option Label | API Value (`query.status.option`) | CLI Alias | Description |
|---|---|---|---|
| **INSTANT BUYOUT** *(Default)* | `securable` | `instant`, `instant_buyout`, `buyout` | **Only Instant Buyout listings**. Buyers can purchase instantly in-game by paying the listed currency + an instant buyout fee in Gold. No player-to-player whisper or manual interaction required. |
| **INSTANT BUYOUT AND IN PERSON** | `available` | `available`, `both`, `instant_and_in_person` | Includes both instant buyout listings and manual whisper trades where the seller is currently online. |
| **IN PERSON (ONLINE IN LEAGUE)** | `onlineleague` | `in_person_league`, `onlineleague` | Only manual whisper trades where the seller is actively logged into the specified league. |
| **IN PERSON (ONLINE)** | `online` | `in_person_online`, `online` | Manual whisper trades where the seller is online in any Path of Exile 2 league or realm. |
| **ANY** | `any` | `any`, `offline`, `all` | Includes all listings, including offline sellers. Useful for rare legacy items or price research on low-volume items. |

## Instant Buyout Fees

In PoE 2, instant buyout listings require an in-game gold fee charged to the buyer upon instant buyout:
- The fee varies based on item level, base type, and rarity (e.g., `5,644 Gold` for typical mid-tier items).
- The official Trade API returns this in the fetch response as `listing.fee`.
- In-person trades have no gold buyout fee (`fee = None`).
