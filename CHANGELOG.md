# Changelog

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
