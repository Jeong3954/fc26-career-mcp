# Data model

All data is plain JSON in the data folder (default
`%USERPROFILE%\Documents\FC26CareerMCP\`, or the folder named in that folder's
`config.json` `{"data_dir": ...}`). The bridge writes atomically (`.tmp` + rename);
the MCP server only reads files and never talks to the game.

## Files

| File | Content | Written |
|---|---|---|
| `status.json` | `schema, product, bridge, le_version, compat ("full" / "partial" / "unsupported"), memory_reads, reason, supported_le_versions[], mode, written` | bridge start, every career load |
| `heartbeat.json` | `bridge, wall, unix, reason, career_ready, why, mode, game_date, date_error` | every career event |
| `snapshot.json` | schema 4, the user's club / player (below) | first read, every game day, after each user match |
| `world.json` | `fc26-world-1`: `tables[]` (every standings group), `top[]` (top 10 per competition: goals, assists, clean_sheets, avg_rating), `fixtures[]` (all clubs, played + upcoming, capped), `transfers[]` (market-wide, cap 400), `club_transfer_finance[]` | first read, once per game day, both modes |
| `event_payloads.jsonl` | raw event payload dumps (cap 8 MB) | every event |
| `save_extract.json` | parsed copy of the newest save (schema 2) | by the server when the save changed |
| `last_step.txt` | bridge breadcrumb (for troubleshooting) | before risky steps |

## snapshot.json (schema 4)

Common keys:
`schema=4, product, bridge, le_version, compat, memory_reads, reason, written, game_date,
date_error, game_date_source, mode ("manager" / "player" / "unknown"),
user{name, firstname, seasons, club_teamid, usertype, playertype}, user_team{id, name},
transfer_budget, wage_budget (null = no getter), last_results[], next_fixtures[],
next_fixture, league_table{competition, compobjid, rows[], team_competitions[]},
squad{count, total_wages, players[], excluded[], notes, ...}, events[] (filtered names,
GLOBAL), event_payloads[] (decoded, scope "ours" / "global"), raw_events[]`.

Manager only:
`transfers[]` (user club, this season), `offers[]` (open, received), `offers_history[]`,
`bids[]` (sent), `player_talks[]`, `targets[]` (player detail for ids in offers/bids/talks
not in the squad), `offers_read_at, offers_error, pending_reads[], last_job`.

Player only:
`user_player{playerid, name, team_id, team, position, age, overall, potential, wage,
season_count, attributes{38 attributes + skillmoves, weakfoot, foot, height, weight},
recent_ratings[] (rating_int_5_10), avg_rating_recent,
season{app, goals, assists, yellow, red, by_competition[]},
career{playerrequest, numtransferrequests, numloanrequests, numwithdrawrequests,
requestactiondays, lastwithdrawdate, playedlastmatch, position},
history[] (per season: season, teamid, appearances, goals, assists, motm, matchratings,
cleansheets, wins, draws, loses, wage, overall, value, tableposition, leaguetrophies,
domesticcuptrophies, continentalcuptrophies, clublevel),
last_match{mins, pos, ovr}, squad_role: "not_available"}`.

## world.json

- `tables[]`: one entry per standings group (`compobjid`, competition name, rows with
  played/won/drawn/lost/goals/points/position). Several groups can share a competition name.
- `top[]`: per `compobjid`, top 10 for goals, assists, clean_sheets, avg_rating (only the
  competitions the user plays in plus European/international cups, A15).
- `fixtures[]`: all clubs, played and upcoming, explicit `home_id`/`away_id`, scores in
  home-away order (A6).
- `transfers[]`: market-wide completed transfers (cap 400).
- `club_transfer_finance[]`: per club spend/income this season.

## save_extract.json (schema 2)

Parsed from a COPY of the newest career save (`CmMgrC*` manager / `CmPlyC*` player)
under `%LOCALAPPDATA%\EA SPORTS FC 26\settings\`. Identity-checked against
`snapshot.user` (userid / club teamid / player id); mismatch => `error: different_career`.
Contains: `career_type`, `user_player` (player careers), `national_teams[]` (men league
id 78 / women 2136, with rosters), `managers[]` (managerid, name, teamid, nationality,
birthdate), board history, suspensions, loans, pre-signed players, youth, grudge/love,
previous clubs. The save has **no real player names**; the server hydrates ids from the
live name maps and leaves unknown ids as ids (A13). Values are "as of the last save"
(`_meta.save_as_of`); see A11 for the stale tables.

## `_meta` on every tool response

`source: "live" | "save" | "live+save"`, `scope: "user_club" | "user_player" | "global" |
"mixed"`, `mode`, `live_game_date`, `snapshot_written`, `snapshot_age_sec`,
`heartbeat_age_sec`, `save_as_of` (when the save was used), `compat`, `warnings[]`.
