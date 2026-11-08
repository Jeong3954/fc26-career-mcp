---
name: fc26-career-mcp
description: Read-only access to the user's EA SPORTS FC 26 offline career (manager or player) through the fc26_* MCP tools - club overview, squad, player detail, competitions, transfers, save-file extras and global events. Use when the user asks about their FC 26 career, squad, fixtures, league table, transfers, offers, player progress or save data. Never claims to change anything in the game.
---

# FC26 Career MCP

Eight read-only MCP tools (`fc26_*`) expose JSON files that a Live Editor Lua bridge
writes while the user's FC 26 offline career is running, plus a parsed copy of the newest
save file. Nothing here writes to the game. Nothing leaves the user's PC.

## Workflow

1. **Always call `fc26_status` first** in a conversation (and again if the user says they
   advanced the game). It tells you: whether the bridge is alive, the live game date, how
   old the snapshot/heartbeat are, `compat` (full / partial / unsupported), the career
   `mode` (manager / player), which files exist, and how to refresh.
2. Pick the tool from the table in `references/mode_matrix.md`. Prefer the narrowest tool
   (`fc26_my_career` for an overview, `fc26_squad` for the squad, `fc26_player` for one
   player, `fc26_competitions` for tables/fixtures/top lists, `fc26_transfers` for
   dealings, `fc26_save_data` for save-only tables, `fc26_global_events` for the news feed).
3. Standings or fixtures older than the user wants (`fc26_status` how_to_refresh): after loading a career the
   user opens and closes a hub menu twice (the first event only settles) and stays idle on the hub 15 s; then
   call `fc26_refresh_world` and ask them to open/close a hub menu once more. One read per request; a `failed`
   result blocks it until the game restarts - do not retry.
4. Read `_meta` on every response: `source` (live / save / live+save), `scope`
   (user_club / user_player / global / mixed), `live_game_date`, `snapshot_age_sec`,
   `heartbeat_age_sec`, `save_as_of`, `compat`, `warnings[]`. Repeat relevant warnings to
   the user in plain words.

## How to talk about the data

- **Freshness in words.** Say the in-game date and what the value is "as of":
  "As of 14 March (your last game day) you are 4th." Live values change every game day;
  save values are "as of your last save". Never mix the two in one sentence without saying
  which is which (A11). If the heartbeat is old or the bridge is not running, say so and
  tell the user how to refresh (`fc26_status.how_to_refresh`: advance a day or open the
  career hub).
- **Never attribute global events to the user's club.** Items from `fc26_global_events`
  and `events[]` without a decoded payload only say that something happened *somewhere in
  the game world*. Only payloads with `scope: "ours"` concern the user's club or player
  (A1, A5, A5b). Event *names* are hints: Live Editor's id-to-name table is wrong for
  FC 26 past id ~35, so trust the id + decoded payload label, not the name.
- **Home/away.** Scores are in the fixture's own home/away order. Read `home_id/away_id`
  (or the explicit `home_*`/`away_*` keys) before saying "you won" (A6).
- **Ratings.** `rating_int_5_10` values are integers 5-10 from the game database; the game
  screen shows one decimal (e.g. 7.4). Say "about 7" or "7 (integer rating)", never invent
  the decimal (A7). Averages computed from integers are approximate.
- **Save vs live.** Use the save (`fc26_save_data`) only for things the live files do not
  have (national teams, managers, board history, suspensions, loans, pre-signed players,
  youth, grudge/love, previous clubs). Known stale-in-save values: board confidence,
  objectives, budgets, previous-season league table, previous-season league goals,
  manager W/D/L history. Label them "previous season" or do not use them (A11, A17).
  If the save tool returns `error: different_career`, the newest save on disk is not the
  career the bridge is watching; say so instead of mixing careers.
- **Names.** Real player names exist only in the live files. The save has ids; the server
  hydrates them from live name maps. A name-less id stays an id, never a guess (A13).
- **League table.** Use the standings group the user's fixtures point to (the
  `compobjid` in `league_table` / `next_fixtures`). A competition can have several
  groups; name the group by its `compobjid`, never merge groups by name (A12, A14).
- **Budgets.** `wage_budget` may be `null` (no live getter). Say "not readable" rather
  than zero.

## What is not readable (say so, do not guess)

- Board confidence / objectives live (save copies are stale zeros).
- Player of the Month / Team of the Week (news only, no payload).
- Playing-time status (promise/role) in player career.
- In `compat: "partial"` (unsupported Live Editor or game version, or a failed safety
  check): standings, fixtures, league table, transfers, offers, world fixtures/tables.
  Squad, attributes, season stats, names and events still work. Say which half is missing.
- Anything in online modes. The product is offline-career only.

## Never

- Never say you changed, signed, sold, trained or set anything. There are no write tools.
- Never run or suggest Lua / memory edits; the bridge is read-only by design.
- Never present a global count ("9 players unhappy with wages") as the user's players.
- Never claim a call-up, award or transfer from an event name alone (A16, A18).

## Other clients

The same tools exist over Streamable HTTP (`http://127.0.0.1:26261/mcp`), REST
(`/api/<tool>`, OpenAPI at `/openapi.json`) and as pasted text (`server/fc26cm_cli.py`). The
client-neutral version of this file is `AGENTS.md` at the product root.

## References

- `references/accuracy_rules.md` - rules A1-A18 and A5b in product terms.
- `references/negotiation_codes.md` - transfer negotiation action codes (manager mode).
- `references/data_model.md` - files and snapshot/world/save keys.
- `references/mode_matrix.md` - tool availability in manager vs player career.
