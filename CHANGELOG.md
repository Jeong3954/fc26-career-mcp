# Changelog

## 1.0.2 - bridge follow-up

- Event payload reads: Live Editor sometimes hands the payload address as a float; such addresses are now skipped
  before the native read, and a payload read miss no longer marks the tick's memory reads as failed (it used to
  stop the deferred memory refresh that runs later in the same tick).
- `world.json` (standings for all competitions, leaders, all-club matches, market) is written in player career
  too, with the same deferral / settle rules as manager career.
- New tests for both in `tests/test_bridge_memory_guard.py`.

## 1.0.1 - crash fix (bridge)

- Fixed a game crash: a snapshot memory read (league standings / fixtures) could run in the same tick as
  `DAY_PASSED`, while the game was rebuilding its lists; Live Editor's `ReadShort` then got a non-integer address
  and the next native read hit `0xFFFFFFFFFFFFFFFF`.
- Every memory read now goes through a guard: integer address inside user space checked before the native call,
  each read in `pcall`, the first failure stops memory reads for the rest of that tick, non-pointer results read
  as 0, multilevel pointer walks check every level.
- Memory reads (standings, fixtures, transfer negotiations, world.json) never run in the tick of a day / match /
  sim / season event: they wait until 10 s after the last such event and are skipped during multi-day sims; they
  run on the next harmless career event (`snapshot.reason = "memory_refresh"`). `snapshot.memory` reports
  `fresh` / `deferred` / `failed` and when the cached values were read. DB-table reads are unchanged.
- Kill switch: create an empty file `disable_memory` in the data folder to turn every memory read off.
- New tests: `tests/test_bridge_memory_guard.py`.

## 1.0.0 - initial release

- Read-only Live Editor bridge (`bridge/`): status, heartbeat, snapshot (schema 4),
  world (fc26-world-1), event payloads. Autorun and manual variants.
- Save reader (`save/`): parses a copy of the newest career save; schema 2 extract with
  national teams, managers, player-career data; identity check against the live snapshot.
- Local stdio MCP server (`server/`), 8 tools: `fc26_status`, `fc26_my_career`,
  `fc26_squad`, `fc26_player`, `fc26_competitions`, `fc26_transfers`, `fc26_save_data`,
  `fc26_global_events`. Manager and player careers.
- Skill (`skill/`) with accuracy rules, negotiation codes, data model and mode matrix.
- Transports: MCP stdio, MCP Streamable HTTP + REST/OpenAPI + text (`server/fc26cm_http.py`,
  127.0.0.1:26261), CLI paste mode (`server/fc26cm_cli.py`).
- AI-driven install: `INSTALL_FOR_AI.md` (deterministic agent steps), `installer/install.py`
  (`--detect`, multi-client JSON/TOML merge with backups, bridge-conflict detection,
  `--http-autostart`), `healthcheck.py` (PASS/WARN/FAIL lines), `uninstall.py`. No wizard/.exe.
- `AGENTS.md` (client-neutral rules), `docs/CLIENTS.md` (compatibility matrix + snippets).
- Verified: Live Editor v26.3.5 with FC 26 TU 1.6.3 (exe 1.0.139.20381). Other versions
  run in `partial` mode (documented-API data only).
