# AGENTS.md - how an AI assistant should use FC26 Career MCP

Unofficial fan project by GLOUNDEV (https://github.com/Jeong3954/fc26-career-mcp); not affiliated with or
endorsed by Electronic Arts. FC 26 Live Editor is by xAranaktu.

Client-neutral instructions (Claude, ChatGPT/Codex, Gemini, Cursor, Copilot, LM Studio, any
other). The Claude skill version is `skill/SKILL.md`; the content is the same.

## What you have

Eight read-only tools, prefix `fc26_`, over the user's **EA SPORTS FC 26 offline career**
(manager or player). They read JSON files written by a Live Editor bridge while the game runs,
plus a parsed copy of the newest save. Nothing writes to the game. Nothing leaves the PC.

Reach them through MCP (stdio or `http://127.0.0.1:26261/mcp`), REST
(`http://127.0.0.1:26261/api/<tool>?arg=value`, OpenAPI at `/openapi.json`), or the text the user
pastes from `server\fc26cm_cli.py`. The data and rules are identical in every transport.

| Tool | Use it for |
|---|---|
| `fc26_status` | **call first**: bridge alive?, mode (manager/player), live game date, data age, compat, how to refresh |
| `fc26_my_career` | one overview: club/budgets/position/results/fixtures (manager) or the user's player profile, ratings, stats, requests, history (player) |
| `fc26_squad` | the club squad with contracts, stats, injuries, flags, save extras (`include_attributes`, `position`, `sort`) |
| `fc26_player` | one player by `playerid` or `name` (ambiguous -> candidates) |
| `fc26_competitions` | `view` standings / fixtures / top_players / index for any competition (`compobjid`, `competition`, `teamid`) |
| `fc26_transfers` | `scope` user_club (offers, bids, talks, targets, `include_history`) or market (all clubs, finance) |
| `fc26_save_data` | `category` national_teams, managers, board_history, suspensions, loans, presigned, youth, grudge_love, previous_clubs, team_roles, season_start, squad_ranking, news_events, summary |
| `fc26_global_events` | the event feed: names (global counts) + decoded payloads with a scope per row |
| `fc26_refresh_world` | asks the bridge (1.0.5+) for ONE live standings + fixtures read now, waits for it (`wait_seconds`, `keep_pending`, `cancel`) |

## Every answer carries `_meta`

`source` (live / save / live+save), `scope` (user_club / user_player / global / mixed), `mode`,
`live_game_date`, `snapshot_age_sec`, `heartbeat_age_sec`, `bridge_alive`, `save_as_of`,
`compat`, `warnings[]`. Read it before answering; repeat relevant warnings in plain words.
An `error` key (`no_snapshot`, `no_world`, `different_career`, `ambiguous_name`, ...) comes with
a `hint`; tell the user what to do, do not guess the data.

## Accuracy rules (the short list)

1. **Say what date a value is as of.** Live values: "as of <live_game_date> (your last game
   day)". Save values: "as of your last save (<save_as_of>)". Never mix the two in one
   sentence without saying which is which. If `bridge_alive` is false, say the game may be
   further on and how to refresh (advance a day / open the career hub).
2. **Only `scope: user_club` / `user_player` rows are about the user.** Event *names*
   (`fc26_global_events.event_names`) are global counts: "9 players unhappy with wages" is
   somewhere in the game world, never the user's players. Payload rows are attributed only by
   their own `teamid` / `playerid`. Live Editor's event names are wrong for many ids past ~35:
   trust `id` + `kind`.
3. **Home/away.** Scores are in the fixture's own home-away order. Use `user_score`, `outcome`
   (W/D/L) and `opponent`, which the server derives from `home_id` / `away_id`, before saying
   "you won".
4. **Ratings are integers 5-10** (`rating_int_5_10`); the game screen shows one decimal. Say
   "about 7", never invent decimals. Averages of integers are approximate.
5. **League table = one standings group = one `compobjid`.** A competition can have several
   groups; name the group's `compobjid`; never merge groups by name. Top lists exist only for
   competitions the game tracks (the user's plus European/international).
6. **Names come from the live files only.** The save holds ids; the server hydrates names it
   knows; an id without a name stays an id, never a guess.
7. **Save-only tables are "as of the last save".** Board confidence, objectives, reputation,
   budgets and the manager W/D/L history in the save are stale placeholders: do not quote them
   as current. `different_career` means the newest save is another career: say so.
8. **Negotiation codes** (manager): last action 6 offer made, 2 counter, 3 on hold (open);
   0/4 accepted, 1 rejected, 5 expired (closed). The user is the seller in `offers[]`, the buyer
   in `bids[]`. Quote money only when the field is present.
9. **Not readable (say so):** board confidence/objectives live, Player of the Month / Team of
   the Week, playing-time status in player career, injury types, wage budget when `null`,
   anything in `compat: partial` (standings, fixtures, league table, transfers, offers, world
   tables), anything in online modes.
10. **Never claim to have changed anything.** There are no write tools; never suggest Lua,
    memory edits or anti-cheat workarounds. Offline career only.

## Typical flow

1. `fc26_status` -> read `mode`, `live_game_date`, `bridge_alive`, `compat`.
2. The narrowest tool for the question (`fc26_my_career` for "how is it going", `fc26_squad` for
   the squad, `fc26_player` for one player, `fc26_competitions` for tables/fixtures/top lists,
   `fc26_transfers` for dealings, `fc26_save_data` for save-only tables, `fc26_global_events`
   for the news feed).
3. Answer with the date the data is as of, the scope, and the warnings that matter.

Fresh table/fixtures before the next game day: ask the user, after loading a career, to open and close a hub
menu twice (the first event only settles) and stay idle on the hub 15 s; then call `fc26_refresh_world` and have
them open/close a hub menu once more while it waits. On `failed` do not retry (the bridge blocks it until the game
restarts); on `withdrawn` explain the steps again.

More: `skill/references/accuracy_rules.md` (A1-A18), `negotiation_codes.md`, `data_model.md`,
`mode_matrix.md` (manager vs player). Installing: `INSTALL_FOR_AI.md`.
