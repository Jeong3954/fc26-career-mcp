# FC26 Career MCP

**FC26 Career MCP connects your EA SPORTS FC 26 career mode (player career and manager career)
to Claude, ChatGPT, Gemini and any other LLM through the Model Context Protocol (MCP).** It is
a read-only Live Editor bridge: ask your AI assistant about your squad, form, fixtures, league
table, transfers, offers and player progress, and it answers from the real game data.

A small Lua script runs inside xAranaktu's **FC 26 Live Editor** and writes JSON files while
you play. A local server (one Python file, no dependencies) gives any AI client eight `fc26_*`
tools over those files and a parsed copy of your newest save. Works with Claude, ChatGPT/Codex,
Gemini, Cursor, VS Code Copilot, Windsurf, LM Studio and anything else that speaks MCP, REST or
can read pasted text. Nothing is written to the game. Nothing leaves your PC.

By **GLOUNDEV**.

> **Unofficial fan project.** FC26 Career MCP is not affiliated with, endorsed by or sponsored
> by Electronic Arts Inc. or EA SPORTS. "EA SPORTS FC" is a trademark of Electronic Arts Inc.
> FC 26 Live Editor is made by xAranaktu and is a separate third-party tool you install and use
> at your own risk. Offline career only. Back up your saves. [docs/SAFETY.md](docs/SAFETY.md)

## Install: paste one prompt into your AI

There is no setup program. Your AI assistant installs it for you. Requirements: Windows 10/11,
FC 26 (PC), an offline career, **FC 26 Live Editor** already installed, and an AI assistant
that can run commands on your PC (Claude Code, Claude Desktop with computer access, Codex CLI,
Gemini CLI, Cursor, VS Code Copilot agent, ...).

Copy this, paste it to your assistant, and follow what it asks (it will ask you to start the
game and restart the assistant - nothing else):

```
Please install "FC26 Career MCP" on this PC for me.
1. Get the repository https://github.com/Jeong3954/fc26-career-mcp into %USERPROFILE%\fc26-career-mcp (git clone or download the zip).
2. Open INSTALL_FOR_AI.md in that folder (also at
   https://raw.githubusercontent.com/Jeong3954/fc26-career-mcp/main/INSTALL_FOR_AI.md) and follow it exactly, step by step. It explains how to
   detect my Live Editor folder and my AI client, install the read-only bridge and the local
   server, register the server in my client (backing up configs first), and run the health check.
3. Register the server for the client you are running in (ask me if unsure). Never touch the game
   files, anti-cheat, or my saves; never delete anything of mine; ask me before installing Python
   or if another script is already in Live Editor's autorun folder.
4. When done, show me the health check result and tell me the few in-game steps I must do.
```

한국어 안내: [README.ko.md](README.ko.md). Prefer to do it by hand? Section "Manual install".

## What you get

| Tool | What it returns |
|---|---|
| `fc26_status` | bridge alive?, game date, snapshot/heartbeat age, compat, mode (manager/player), how to refresh |
| `fc26_my_career` | manager: club overview (budgets, position, results, fixtures, squad counts, open offers). player: your player's profile, ratings, stats, requests, history |
| `fc26_squad` | squad with contracts, ratings, injuries, season stats (+ save extras: suspensions, loans, pre-signed, youth) |
| `fc26_player` | one player by id or name: full detail (squad), transfer target detail, or top-list rows + save extras |
| `fc26_competitions` | standings / fixtures / top players / index for any competition in the game world |
| `fc26_transfers` | your club's transfers, open offers, offer history, bids, talks, targets - or the whole market + club finance |
| `fc26_save_data` | save-only tables: national teams, managers, board history, suspensions, loans, pre-signed, youth, grudge/love, previous clubs, team roles, ... |
| `fc26_global_events` | the game's event feed (global, never attributed to your club) + decoded payloads with scope |

Every answer carries `_meta`: source (live / save), scope (your club / your player / global),
in-game date, data age and warnings - so the assistant can say what a value is "as of".
Manager **and** player careers; what differs: `skill/references/mode_matrix.md`.

Example questions: "Who is my best performer this season and what is his contract situation?",
"Show the league table and our next three fixtures", "Which offers are open for my players and
at what stage are they?", "Who are the top scorers in the Champions League?", "Which of my
players are suspended or on loan?", player career: "How have my ratings trended lately?"

## Any LLM

| Your assistant | How it connects |
|---|---|
| Claude Desktop, Claude Code, Cursor, Windsurf, VS Code Copilot, Gemini CLI, Codex CLI, LM Studio | MCP **stdio**, registered by the installer |
| Clients that want a URL | MCP **Streamable HTTP** at `http://127.0.0.1:26261/mcp` (`install.py --http-autostart`) |
| ChatGPT and any assistant without MCP | **paste mode**: `python server\fc26cm_cli.py all` prints a compact summary; or the local **REST** API with OpenAPI (`/openapi.json`) |

Instructions for the assistant: [AGENTS.md](AGENTS.md) (any client) or `skill/SKILL.md`
(Claude skills). Config snippets and the tested matrix: [docs/CLIENTS.md](docs/CLIENTS.md).

## Manual install

1. Put the repository at `%USERPROFILE%\fc26-career-mcp` (do not move it later).
2. Real Python 3.9+ (`py -3 --version`; the Microsoft Store stub does not count).
3. `python installer\install.py --detect`, then `python installer\install.py --client auto`
   (options: `--le-dir`, `--data-dir`, `--client <names>`, `--allow-coexist`, `--http-autostart`,
   `--dry-run`). It copies the bridge into `<Live Editor>\lua\autorun\`, creates
   `Documents\FC26CareerMCP\`, and merges the server into each client config (backups first).
4. `python installer\healthcheck.py` - plain PASS / WARN / FAIL lines.
5. Start FC 26 through Live Editor, load your offline career, open the **career hub**, wait
   ~10 s: `snapshot.json` appears in the data folder. Restart your AI client fully and ask
   "How is my career going?".

If autorun does not fire: Live Editor -> Lua Engine -> load
`<Live Editor>\lua\autorun\fc26cm_bridge.lua` -> Execute (F5) while the hub is on screen.
Uninstall: `python installer\uninstall.py` (keeps the data folder unless `--purge`).

## Folder

```
bridge/      fc26cm_bridge.lua (manual) + fc26cm_bridge_autorun.lua   - runs inside Live Editor
server/      fc26cm_mcp.py (stdio)  fc26cm_http.py (HTTP/REST)  fc26cm_cli.py (text)  fc26cm_core.py
save/        save_reader.py + vendored fc26-mcp parser (MIT)           - parses a copy of the save
installer/   install.py  uninstall.py  healthcheck.py                   - run by your AI or by you
skill/       SKILL.md + references (Claude)      AGENTS.md (any client)  INSTALL_FOR_AI.md (your AI)
docs/        DESIGN  CLIENTS  COMPATIBILITY  TROUBLESHOOTING  SAFETY
tests/       unittest + sanitized fixtures (manager + player)            python -m unittest discover -s tests
```

Data folder: `%USERPROFILE%\Documents\FC26CareerMCP\` (`status.json`, `heartbeat.json`,
`snapshot.json`, `world.json`, `event_payloads.jsonl`, `save_extract.json`). Redirect with
`--data-dir`. The save reader parses a copy of the newest save in
`%LOCALAPPDATA%\EA SPORTS FC 26\settings\`.

## Privacy and safety

No network: the server speaks stdio to your client or listens on 127.0.0.1 only. Read-only
(no write handlers, no Lua from the assistant, no table dumps). Offline career only; no
anti-cheat bypass of any kind. Every game call under `pcall`; bounded reads (one heavy read per
game day, none during matches); canary checks disable memory reads on anything suspicious.
Full text: [docs/SAFETY.md](docs/SAFETY.md).

## Compatibility

| Live Editor | FC 26 | Status |
|---|---|---|
| v26.3.5 | TU 1.6.3 (exe 1.0.139.20381) | full |
| other | any | partial (squad, attributes, stats, names, events, save data; no standings/fixtures/transfers/offers) |

After an update: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Problems:
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## FAQ

**How do I connect FC 26 career mode to Claude or ChatGPT?**
Install FC 26 Live Editor, then paste the prompt from "Install" into an AI assistant that can run
commands on your PC (Claude Code, Claude Desktop, Codex CLI, Gemini CLI, Cursor, ...). It installs
the bridge and registers the `fc26-career-mcp` MCP server. Claude, Cursor, Gemini CLI and Codex
use MCP directly. For ChatGPT in the browser, run `python server\fc26cm_cli.py all` and paste the
summary, or point a client at the local REST API (`http://127.0.0.1:26261/openapi.json`).

**Does it work with player career (Player Career / Be a Pro career)?**
Yes. Manager career and player career are both supported. In player career the tools return your
player's profile, ratings, stats, requests and history; what differs per mode is listed in
`skill/references/mode_matrix.md`.

**Which AI models and apps work?**
Any LLM: Claude, ChatGPT, Gemini, local models in LM Studio, and every MCP client. Clients without
MCP use paste mode or REST. See [docs/CLIENTS.md](docs/CLIENTS.md).

**Is it safe? Can it get me banned by anti-cheat?**
It is read-only: it never writes to the game, your saves or memory, and it has no anti-cheat
bypass of any kind. It is meant for offline career only; do not use Live Editor in online modes.
Live Editor itself is a third-party tool you use at your own risk. Details: [docs/SAFETY.md](docs/SAFETY.md).

**Does my data leave my PC?**
No. The server talks to your client over stdio or listens on 127.0.0.1 only. What your AI
provider sees is only what your assistant sends it from the tool answers.

**Does it need FC 26 Live Editor?**
Yes for live data (game date, standings, fixtures, offers). Save-only data (national teams, loans,
suspensions, youth, ...) is parsed from a copy of your newest career save.

**Is this an official EA SPORTS tool?**
No. It is an unofficial fan project by GLOUNDEV, not affiliated with or endorsed by Electronic Arts.

## License

MIT, (c) 2026 GLOUNDEV - see [LICENSE](LICENSE). Author: GLOUNDEV. Third-party notices (fc26-mcp parser, MIT,
(c) 2025 Afjal): [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). EA SPORTS FC is a trademark
of Electronic Arts Inc. This project is not affiliated with or endorsed by Electronic Arts / EA SPORTS.
FC 26 Live Editor is by xAranaktu; this project is not affiliated with xAranaktu.
