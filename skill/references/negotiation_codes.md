# Negotiation action codes (manager career)

`fc26_transfers` (scope `user_club`) returns the user's open offers, offer history, sent
bids and player talks. Each negotiation carries a sequence of action codes; the **last code**
tells the stage. Raw negotiation text is not in the product files.

| Code | Meaning | Who acted |
|---|---|---|
| 6 | Offer made | buyer |
| 2 | Counter: asking terms | seller |
| 3 | On hold | seller |
| 0 | Accepted | seller |
| 4 | Accepted | buyer |
| 1 | Rejected | either |
| 5 | Expired / ended by the buyer | buyer |

- **Open** = last code 6 or 2 (or 3).
- **Closed** = 0 or 4 (accepted), 1 (rejected), 5 (expired).
- The user is the *buyer* in `bids[]` (sent) and the *seller* in `offers[]` (received).
- `targets[]` holds player detail for ids referenced by offers/bids/talks that are not in
  the squad (name, team, age, position, overall, potential, attributes).
- A code tells the stage, not the money: quote fee/wage fields only when present.
- `offers_read_at` is the game date of the last offers read; `pending_reads[]` lists
  heavy reads the bridge has deferred to the next game-day tick.

Player career: `fc26_transfers` returns `offers: null` with `not_available_reason`; the
user's own transfer/loan/withdraw requests are in `user_player.career`
(`playerrequest`, `numtransferrequests`, `numloanrequests`, `numwithdrawrequests`,
`requestactiondays`, `lastwithdrawdate`).
