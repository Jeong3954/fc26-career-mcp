# Troubleshooting

Start with `fc26_status` (ask your assistant "check fc26 status") or open
`%USERPROFILE%\Documents\FC26CareerMCP\status.json` and `heartbeat.json` in a text editor.

## No `snapshot.json` in the data folder

1. Is the game running through Live Editor, with an **offline career** loaded, and the
   **career hub** on screen? Nothing is read before the hub + 5 s settle.
2. Is `fc26cm_bridge_autorun.lua` in `<Live Editor>\lua\autorun\`? Run
   `python installer\install.py` again, or copy it by hand from `bridge\`.
3. Autorun did not fire (see below): load the manual script instead.
4. Wait ~10 s after the hub appears; then check `heartbeat.json` (updated on every career
   event) and `last_step.txt` (the last thing the bridge tried).
5. If `status.json` exists but `snapshot.json` does not, read `status.json` `reason`.

## Autorun not firing

Some Live Editor builds only run autorun scripts after the Lua Engine has been opened once.
Manual fallback: Live Editor -> **Lua Engine** -> load `<Live Editor>\lua\autorun\fc26cm_bridge.lua`
(the manual copy) -> **Execute** (or F5) while the career hub is on screen. The manual
version does the same thing for the current session only.

## `compat: "partial"`

Your Live Editor or game version is not in the bridge's verified list, or a safety check
failed mid-session (`status.json` `reason` says which). Squad, attributes, stats, names,
events and save data still work; standings, fixtures, league table, transfers and offers
do not. See docs/COMPATIBILITY.md for what to do after an update.

## The game was updated (title update)

Live Editor usually stops loading until xAranaktu releases a matching version. Once it
loads again, the bridge reports `partial` until a product release adds the new Live Editor
version (or you edit the one-line `SUPPORTED` table yourself, at your own risk).

## `error: different_career` from `fc26_save_data`

The newest save file on disk belongs to a different career than the one the bridge is
watching (another manager/player, another club). Save the career you are playing so it
becomes the newest file, or ignore save-only tools for this career.

## Claude Desktop does not show the server

1. Quit Claude Desktop completely (tray icon -> Quit), then start it again; a window close
   is not enough.
2. Open `%APPDATA%\Claude\claude_desktop_config.json` and check the `fc26-career-mcp`
   entry: `command` must be a real `python.exe` and `args` the absolute path to
   `server\fc26cm_mcp.py`. The installer printed both.
3. Run in a terminal: `"<python.exe>" "<path>\server\fc26cm_mcp.py"` - it must wait
   silently for input (Ctrl+C to stop). If it prints an error, Python is too old (< 3.9) or
   the path is wrong.
4. Claude Desktop -> Settings -> Developer shows the server log on failure.

## "Python was not found" / Microsoft Store window opens

Windows ships a stub `python.exe` that opens the Store. `install.py --detect` reports it as
`STORE STUB` and refuses to register it. Install Python from python.org or
`winget install -e --id Python.Python.3.13` (tick "Add python.exe to PATH"), or expand the
official embeddable zip into `<product>\python\`, then run `install.py --python <path>` again.

## Heartbeat is old

`heartbeat.json` updates on every career event. If `fc26_status` says the heartbeat is
minutes old while the game is running, you are probably in a match, a menu outside the
career, or the game is paused. Return to the career hub or advance a day. If the game was
closed, the data is simply "as of" the last heartbeat; the tools keep working on it.

## File lock / "file in use"

The bridge writes `.tmp` files and renames them, so readers never see half-written JSON.
If a tool reports it cannot read a file, close any editor that has the file open and try
again; antivirus "real-time scan" on the Documents folder can also hold files briefly.

## Uninstall

`python installer\uninstall.py` (add `--purge` to delete the data folder too). It removes the
two Lua files, every client entry it added (with a backup) and the HTTP Startup entry, using
`installer\install_state.json`.

## Health check

`python installer\healthcheck.py` prints one PASS / WARN / FAIL / SKIP line per check (Python,
product files, bridge in Live Editor, data folder and heartbeat, saves, stdio server handshake,
HTTP server, client registrations) and `RESULT PASS|FAIL`. Right after installation the
bridge/heartbeat/snapshot WARNs are normal until the game has run once.

## Client does not show the server

- Restart the client completely (Claude Desktop: tray icon -> Quit). Claude Code: `claude mcp list`.
- Open the config `install.py --detect` names for your client and check the `fc26-career-mcp`
  entry: the `command` must be a real python.exe (not WindowsApps) and the path must exist.
- Clients that need a URL: start `server\fc26cm_http.py` (or install with `--http-autostart`)
  and use `http://127.0.0.1:26261/mcp`; `/health` must answer in a browser.
- ChatGPT cannot connect to a local server: use `server\fc26cm_cli.py all` and paste the text.
