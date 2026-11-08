# Changelog

## 1.0.5 - on-demand world read

- New tool `fc26_refresh_world` (MCP, REST, CLI `fc26cm_cli.py refresh`): writes `read_now` in the data folder and
  waits for `read_now_result.json` (`wait_seconds`, `keep_pending`, `cancel`). How: after loading a career, open and
  close a hub menu twice (the first event only settles), stay idle on the hub 15 s, then call it and open/close a
  hub menu once more.
- Bridge: one standings + fixtures read per request, even with memory reads off (still off by default). Runs only
  on a quiet hub event (15 s after any day / match / sim / season / save / load / exit event, no pending day or
  post-match work, not in a match); the request is deleted before the first read and `read_now_inflight` marks
  the read (a leftover marker refuses later requests). No event payloads, no transfer walks; the first failed read
  aborts, writes nothing and blocks requests until the game restarts. Requests expire after 15 min;
  `disable_read_now` refuses all. Writes world.json (transfers not read) and the snapshot's last_results /
  next_fixtures / league_table (`memory.source = "read_now"`). Verified in game (0.44-0.47 s per read).
- Tests: `tests/test_bridge_read_now.py` (Lua mock run, needs lupa), `tests/test_refresh_world.py`.

## 1.0.4 - lifecycle safety and lighter reads

- Block save/exit/new-career flows until a new settled hub event; defer the hub readiness call, reject reentrant callbacks, and remove the manual memory-gate bypass. Memory remains opt-in.
- Reset career-specific names/events/world scheduling; attribute payloads only by decoded identity fields and bound sampling before reading. Native payload failures stop the memory tick.
- Reuse dates and scalar standings within a callback; omit the intermediate bootstrap snapshot write. Heartbeats run at most every 5 seconds on events, including file-only activity reports while waiting.
- Check file write/close/rename failures and preserve world retries. Expose cached memory state in MCP metadata and warnings.
- Reuse file metadata and name indexes within each server request, without a stale cross-request cache.
- Add executable Lua lifecycle regression tests.

## 1.0.3 - safe default (bridge)

- Memory reads are now **off by default**: some games still crashed during multi-day sims while the bridge read
  event payloads from memory. They run only while an empty file `enable_memory` exists in the data folder
  (`disable_memory` still wins). Without it the bridge uses DB-table reads and documented getters only (squad,
  contracts, player attributes/ratings, season stats, injuries, events); league table, fixtures/results,
  transfers, offers/negotiations, world.json fixtures/tables and payload bytes need the opt-in or the save reader.
- The event payload logger is a memory read too and follows the same opt-in and settle gate (no ungated reads).
- No game call at all during a match flow, a multi-day sim or the 10 s after a day / match / sim / season event:
  those events only set flags; the `DAY_PASSED` and post-match work runs on the next quiet hub event.
- `status.json`: `memory_reads` is false and `reason` explains the opt-in while it is off; `compat` still reports
  whether the Live Editor version is supported.
- New tests in `tests/test_bridge_memory_guard.py`.

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
