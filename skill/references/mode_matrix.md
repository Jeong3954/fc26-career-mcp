# Tool availability: manager vs player career

`fc26_status.mode` tells which career is running ("manager", "player", or "unknown" before
the first snapshot). Every response carries `_meta.scope`.

| Tool | Args | Manager career | Player career |
|---|---|---|---|
| `fc26_status` | - | freshness, versions, compat, mode, how to refresh, which files exist | same |
| `fc26_my_career` | - | club overview: user, team, budgets (`wage_budget` may be null), league position + table slice, last results, next fixtures, squad counts, open offers count, pending reads | the user's player profile (attributes, recent ratings, season stats, career requests, history) + club context |
| `fc26_squad` | `include_attributes?` | squad with contracts, ratings, injuries, flags, season stats + save extras per player (suspensions, loans, pre-signed, youth graduate) | club squad; contract fields may be absent |
| `fc26_player` | `playerid` or `name` (exact/partial within known names; ambiguous => list) | squad player full detail; transfer target detail (`targets`); other players: world top-list rows + save extras (previous clubs, grudge/love) | same; the user's own player comes from `fc26_my_career` |
| `fc26_competitions` | `view: standings / fixtures / top_players`, `compobjid?`, `teamid?`, `last?`, `next?`, `stat?` | all competitions in world.json; default `compobjid` = the user's league | same |
| `fc26_transfers` | `teamid?`, `scope: user_club / market`, `include_history?` | user club transfers, open offers (decoded stages), offer history, sent bids, player talks, targets; or market-wide transfers + club finance | club transfers + market; `offers: null` with `not_available_reason`; the user's requests are in `user_player.career` |
| `fc26_save_data` | `category: national_teams / managers / board_history / suspensions / loans / presigned / youth / grudge_love / previous_clubs / summary`, `teamid?` | from save_extract.json (refreshed when the save changed), identity-checked, names hydrated from live maps | same |
| `fc26_global_events` | `days?`, `kind?` | filtered event names (GLOBAL, never attributed) + decoded payloads with scope | same |

## `compat: "partial"` (any mode)

Memory reads are off (unsupported Live Editor / game version, or a failed safety check).
Missing: standings, fixtures, league table, transfers, offers, world fixtures and tables.
Still available: squad, player attributes, season stats, names, events, save data.
Tell the user which half is missing and point them to docs/COMPATIBILITY.md.

## Refresh rules the bridge follows (why data can lag)

- Nothing is read until the career hub is reached and 5 s have passed; LOAD events are
  ignored.
- At most one heavy table read per game day tick; nothing during match flow.
- `snapshot.json` is rewritten on the first read, every game day, and after each user
  match; `world.json` once per game day.
- So: "advance a day" or "go back to the career hub" is the refresh advice.
