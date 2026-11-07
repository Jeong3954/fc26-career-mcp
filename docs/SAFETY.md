# Safety

Short version: the product only **reads**, only in **offline career**, and never touches
the network. Still, it runs a third-party tool (Live Editor) inside your game, so use it at
your own risk and back up your saves.

## Read-only by design

- The bridge has **no write handlers**. It never sets a value, never calls a setter, never
  patches memory. The MCP server has no write tools, cannot run Lua, and cannot dump whole
  tables.
- The LLM only sees JSON files. It cannot send anything to the game.
- The save reader parses a **copy** of your newest save; the original is never opened for
  writing.

## Offline career only

- Live Editor itself is only usable with offline modes. This product adds no bypass of any
  kind and must never be used with online modes or anything that touches EA anti-cheat.
- If you launch an online mode with Live Editor active, that is between you and EA; the
  product does nothing there (no career hub, no reads).

## Bounded, defensive reads

- Every call is wrapped in `pcall`; an error disables that read, never the game.
- No game call until the career hub is reached and 5 seconds have passed; LOAD events
  are ignored.
- At most one heavy table read per game-day tick; nothing during match flow.
- Canary checks (pointer validity, list and vector size caps, id ranges) run before each
  memory walk; a failure disables memory reads for the session (`compat: "partial"`).
- Known crash class we avoid on purpose: unfiltered scans of tables the game has never
  read, and per-player stats calls in a loop.
- Files are written atomically (`.tmp` + rename) with size caps (`event_payloads.jsonl`
  <= 8 MB).

## Privacy

- No network. The server listens on stdio only; the bridge writes local files only.
- Data stays in `%USERPROFILE%\Documents\FC26CareerMCP\` (or your configured folder).
- What the files contain: your career's in-game data (names, stats, fixtures, transfers)
  and your in-game user name. Nothing from outside the game.
- What your MCP client (e.g. Claude Desktop) sends to its model is governed by that
  client's own terms; the product does not send anything itself.

## Your responsibilities

- Back up your saves before first use (`%LOCALAPPDATA%\EA SPORTS FC 26\settings\`).
- Use only with offline career modes.
- Keep Live Editor and the product on verified versions (docs/COMPATIBILITY.md).
- The authors are not affiliated with EA SPORTS or xAranaktu, and give no warranty (see
  LICENSE).
