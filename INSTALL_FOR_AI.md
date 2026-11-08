# INSTALL_FOR_AI.md - installation guide for an AI agent

You are an AI agent (Claude, ChatGPT/Codex, Gemini, Cursor, Copilot, ...) that a human asked to
install **FC26 Career MCP** on their Windows PC. Follow this file top to bottom. It is
deterministic: every step says what to run, what to expect, and what to do when it differs.
The human is probably not technical. Ask them only for what you cannot do yourself (section 0).

Repository: https://github.com/Jeong3954/fc26-career-mcp (author GLOUNDEV). Latest copy of this file:
https://raw.githubusercontent.com/Jeong3954/fc26-career-mcp/main/INSTALL_FOR_AI.md
Unofficial: not affiliated with or endorsed by Electronic Arts; FC 26 Live Editor is by xAranaktu.

## 0. Rules for you (read first)

1. **Read-only product.** It reads files; it never writes to the game, its saves or its memory.
   You must not change, patch or inject anything into FC 26, EA anti-cheat, or game binaries.
   If a step seems to need that, stop and tell the human the product does not do that.
2. **Offline career only.** Never suggest using the bridge in online modes.
3. **Back up before every change** to a file you did not create (client configs, existing Lua
   scripts). The installer does this automatically (`*.bak-<timestamp>`); keep the backups.
4. **Never delete user files.** Never delete another script in Live Editor's `lua\autorun\`,
   never delete saves. If something is in the way, report it and ask.
5. **Live Editor is xAranaktu's third-party tool.** The human installs it from its official
   source and uses it at their own risk. You only look for it; you do not download it.
6. **Ask the human only for**: starting the game through Live Editor / Mod Manager, opening a
   career, restarting their AI client, approvals (installing Python, keeping another script),
   anything that needs their eyes on the screen. Do everything else yourself.
7. **No wizard, no .exe.** Everything below is plain commands you run from a terminal
   (PowerShell on Windows). If you cannot run commands at all, give the human the exact
   commands to paste, one block at a time, and read back the output they paste.
8. **Report** at the end with the health check output and the few in-game steps.

Placeholders in this file:
`{PRODUCT}` = the product folder (where this file lives), `{PY}` = the Python interpreter path
you validated in step 2. If you were given sandbox flags (`--home`, `--le-dir`, `--data-dir`),
append them to every `install.py`, `uninstall.py` and `healthcheck.py` command.

## 1. Get the product folder

If `{PRODUCT}` already exists (you were started inside it, or the human unzipped it), skip ahead.
Otherwise put it at `%USERPROFILE%\fc26-career-mcp` (do not move it afterwards: clients are
pointed at this path):

```powershell
git clone https://github.com/Jeong3954/fc26-career-mcp "$env:USERPROFILE\fc26-career-mcp"
# no git? download the zip of the repository and expand it:
#   Invoke-WebRequest -Uri https://github.com/Jeong3954/fc26-career-mcp/archive/refs/heads/main.zip -OutFile "$env:TEMP\fc26cm.zip"
#   Expand-Archive "$env:TEMP\fc26cm.zip" "$env:USERPROFILE\fc26-career-mcp-tmp"
#   then move the inner folder to "$env:USERPROFILE\fc26-career-mcp"
```

Expect: `{PRODUCT}\server\fc26cm_mcp.py`, `{PRODUCT}\bridge\fc26cm_bridge_autorun.lua`,
`{PRODUCT}\installer\install.py` exist.

## 2. Python (standard library only, 3.9+)

```powershell
py -3 -c "import sys;print(sys.executable, sys.version)"
python -c "import sys;print(sys.executable, sys.version)"
```

- If one prints a path and a version >= 3.9 **and the path does not contain `WindowsApps`**,
  that path is `{PY}`.
- If the path contains `WindowsApps`, it is the Microsoft Store stub, not Python. Treat as missing.
- If missing: ask the human for approval to install Python, then run
  `winget install -e --id Python.Python.3.13 --scope user` and re-run the commands above
  (new terminal). Fallback without winget / without admin rights: download the official
  *Windows embeddable package (64-bit)* zip from python.org/downloads/windows, expand it to
  `{PRODUCT}\python\`, and `{PY}` = `{PRODUCT}\python\python.exe` (the server needs no pip).

## 3. Detect (writes nothing)

```sh fc26cm-step
{PY} {PRODUCT}/installer/install.py --detect
```

Read the output:

| Line | Meaning | What to do |
|---|---|---|
| `Live Editor : NOT FOUND` | Live Editor is not at `C:\FC 26 Live Editor` and no `*Live*Editor*` folder was found on C:, D:, E:, Desktop, Downloads, LocalAppData, Program Files | Ask the human where they installed FC 26 Live Editor; then add `--le-dir "<folder>"` to every installer command. If they have not installed it: stop, tell them it is required (official source, their decision), and resume afterwards. |
| `bridge conflicts : <names>` | Another bridge-like script is in `<LE>\lua\autorun\` (for example a personal `le_bridge_ro_auto.lua`) | Ask the human: keep it (then add `--allow-coexist`; both run, which doubles the per-day reads) or move it away themselves. Never delete or rename it yourself. |
| `other autorun scripts: <names>` (no conflicts) | Unrelated Live Editor scripts | Leave them alone; nothing to do. |
| `Python ... STORE STUB` | See step 2 | Pass `--python {PY}` with a real interpreter. |
| `Client <name> present=True` | That AI client is installed for this user | These are registered by default (`--client auto`). |
| `Saves ... saves present: False` | No career save yet | Fine; `fc26_save_data` works after the first save. |

Decide the client list: the client **you** are running in (if you are not sure, ask the human
which app they are typing in), plus any other installed client the human wants. `auto` = all
present clients. `--client none` registers nothing (use the generic snippets in section 6).

## 4. Install

```sh fc26cm-step
{PY} {PRODUCT}/installer/install.py --python {PY} --client auto --yes
```

Add `--le-dir "<folder>"`, `--allow-coexist`, `--data-dir "<folder>"` (only if the human wants
the data elsewhere), `--http-autostart` (only for URL-based clients, section 6) as decided in
step 3. For a specific client list replace `auto` with names from:
`claude-desktop claude-code cursor windsurf vscode gemini-cli codex lm-studio`.

What it does (all logged): copies `fc26cm_bridge_autorun.lua` + `fc26cm_bridge.lua` into
`<LE>\lua\autorun\` (previous copies backed up), creates `Documents\FC26CareerMCP\`, merges the
server entry into each client config (backup first; Codex gets a TOML block; Claude Code / Codex
use their own CLI when available), writes `installer\install_state.json`.

Expect the last lines `Done. Next steps ...`. On `rc != 0` read the message: it always says what
to fix (missing Live Editor, conflict, bad Python, invalid JSON in a client config - in that
case show the human the file name and ask before touching it).

## 5. Health check (writes nothing)

```sh fc26cm-step
{PY} {PRODUCT}/installer/healthcheck.py --python {PY}
```

One line per check: `PASS`, `WARN`, `FAIL`, `SKIP`, then `RESULT PASS|FAIL`.

- `FAIL` must be fixed before you report success (python, product files, live editor folder,
  bridge installed, stdio server). Re-run after fixing.
- Right after installation these `WARN`s are normal: `bridge status.json`, `bridge heartbeat`,
  `snapshot.json`, `world.json` (the game has not run yet), `save folder` (no save yet),
  `client <x> present but not registered` (a client the human did not want).
- `SKIP http server` is normal unless a URL-based client is used.

## 6. Client specifics

The server entry everywhere is: command `{PY}`, args `["{PRODUCT}\server\fc26cm_mcp.py"]`
(stdio). Full snippets and the tested matrix: `docs/CLIENTS.md`.

| Client | Registered by install.py in | After install |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` (`mcpServers`) | human quits it from the tray icon and starts it again |
| Claude Code | `claude mcp add --scope user ...` (fallback `%USERPROFILE%\.claude.json`) | `claude mcp list` shows `fc26-career-mcp`; new session |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` | Settings -> MCP shows it; restart Cursor |
| Windsurf | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` | restart |
| VS Code (Copilot) | `%APPDATA%\Code\User\mcp.json` (`servers`, type stdio) | `MCP: List Servers` -> Start |
| Gemini CLI | `%USERPROFILE%\.gemini\settings.json` | `/mcp` in a new session |
| OpenAI Codex CLI | `codex mcp add ...` (fallback `%USERPROFILE%\.codex\config.toml`) | new session |
| LM Studio | `%USERPROFILE%\.lmstudio\mcp.json` | restart; enable the server in the chat's tools |
| ChatGPT (web/desktop) | not registrable locally (ChatGPT only connects to remote HTTPS MCP servers) | use paste mode: `{PY} {PRODUCT}\server\fc26cm_cli.py all` and paste the text; or the REST API below |
| Any other MCP client | you: paste the generic JSON from `install.py` output, or the URL | - |

**URL-based clients (Streamable HTTP)**: run `install.py` with `--http-autostart` (Startup entry;
or start `{PY} {PRODUCT}\server\fc26cm_http.py` by hand). URL: `http://127.0.0.1:26261/mcp`.
It binds to localhost only.

**Assistants without MCP**: REST `http://127.0.0.1:26261/api/<tool>` with OpenAPI at
`/openapi.json`, plain text at `/text/<tool>`, or the CLI (`fc26cm_cli.py all|career|squad|...`).

Give the assistant the instructions too: `AGENTS.md` (client-neutral) or `skill/SKILL.md`
(Claude skills folder). For Claude Code: copy `skill/` to `~/.claude/skills/fc26-career-mcp/`
or `<project>/.claude/skills/fc26-career-mcp/`. For Cursor/Codex/Gemini: point them at
`AGENTS.md` (rules file / project instructions).

## 7. In-game steps (the human)

Tell the human, in their language, exactly this:

1. Start FC 26 the usual way through Live Editor (or FC 26 Mod Manager with Live Editor).
2. Load an **offline** career (manager or player) and stay on the **career hub** for ~10 s.
3. `Documents\FC26CareerMCP\snapshot.json` appears. If it does not: Live Editor -> Lua Engine
   -> load `<LE>\lua\autorun\fc26cm_bridge.lua` -> Execute (F5) while the hub is on screen.
4. Restart the AI client completely (Claude Desktop: tray icon -> Quit).
5. Ask the assistant: "How is my career going?" (it should call `fc26_status` first).
6. For a fresh league table / fixtures right now (`fc26_refresh_world`): after loading the career, open and
   close a hub menu twice (the first event only settles), stay idle on the hub 15 s, then ask the assistant to
   refresh and open/close a hub menu once more.

Then run the health check again (step 5): `bridge heartbeat` and `snapshot.json` should be PASS.

## 8. Rollback / uninstall

```sh fc26cm-rollback
{PY} {PRODUCT}/installer/uninstall.py --dry-run
{PY} {PRODUCT}/installer/uninstall.py
```

Removes the two Lua files, the server entries (backups first), the Startup entry; keeps
`Documents\FC26CareerMCP\` (add `--purge` only if the human asks). Client configs can also be
restored from the `*.bak-<timestamp>` files next to them. Without `install_state.json` it still
finds and removes what it can (`--home`, `--le-dir` help it).

## 9. Updating

Pull/unzip the new version over the same folder, then run step 4 again (it re-copies the Lua
files, backs up the old ones, and leaves client entries as they are) and step 5.

## 10. What to tell the human at the end

- The health check lines (PASS/WARN/FAIL) and what the WARNs mean in one sentence each.
- The in-game steps (section 7).
- That the product is read-only, offline-career only, and that Live Editor is a third-party tool.
- Where things are: product folder, data folder, which client config was changed (and its backup).
