# Client compatibility and config snippets

The server is one Python file per transport, standard library only:

| Transport | Start | Endpoint |
|---|---|---|
| MCP **stdio** | the client starts `<PY> <PRODUCT>\server\fc26cm_mcp.py` | - |
| MCP **Streamable HTTP** | `<PY> <PRODUCT>\server\fc26cm_http.py` (or `install.py --http-autostart`) | `http://127.0.0.1:26261/mcp` (POST JSON-RPC, stateless) |
| **REST** JSON | same HTTP server | `GET /api/<tool>?arg=value`, `POST /api/<tool>` (JSON body), `GET /api/tools`, `GET /openapi.json`, `GET /health` |
| **Text** to paste | same HTTP server, or `fc26cm_cli.py` | `GET /text/<tool>`; `fc26cm_cli.py all \| status \| career \| squad \| player <name> \| table \| fixtures \| top \| transfers \| market \| save [category] \| events` |

`<PY>` = a real CPython 3.9+ (not the Microsoft Store stub), `<PRODUCT>` = the product folder.
Backslashes in JSON must be doubled (`"D:\\fc26-career-mcp\\server\\fc26cm_mcp.py"`).

## Matrix

"Tested" = exercised by this repo's test-suite on the exact file format / protocol messages
(configs are written by `installer/install.py`, the stdio and HTTP transports by
`tests/test_server.py` and `tests/test_http.py`). "Manual" = verified by hand against the real
application. Clients change fast: if a snippet stops matching the app, the generic entry below
is what every client ultimately needs.

| Client | Transport | Config written by install.py | Tested |
|---|---|---|---|
| Claude Desktop | stdio | `%APPDATA%\Claude\claude_desktop_config.json` -> `mcpServers` | config format: yes; app: manual (Windows) |
| Claude Code | stdio | `claude mcp add --scope user` (fallback `%USERPROFILE%\.claude.json` -> `mcpServers`) | config format: yes; CLI: manual |
| Cursor | stdio | `%USERPROFILE%\.cursor\mcp.json` -> `mcpServers` | config format: yes; app: not yet |
| Windsurf | stdio | `%USERPROFILE%\.codeium\windsurf\mcp_config.json` -> `mcpServers` | config format: yes; app: not yet |
| VS Code (GitHub Copilot agent mode) | stdio | `%APPDATA%\Code\User\mcp.json` -> `servers` (`type: stdio`) | config format: yes; app: not yet |
| Gemini CLI | stdio | `%USERPROFILE%\.gemini\settings.json` -> `mcpServers` | config format: yes; app: not yet |
| OpenAI Codex CLI | stdio | `codex mcp add` (fallback `%USERPROFILE%\.codex\config.toml` -> `[mcp_servers.fc26-career-mcp]`) | config format: yes; app: not yet |
| LM Studio | stdio | `%USERPROFILE%\.lmstudio\mcp.json` -> `mcpServers` | config format: yes; app: not yet |
| Any Streamable-HTTP client | HTTP | none (give it the URL) | protocol: yes (initialize / tools/list / tools/call / notifications -> 202) |
| ChatGPT (web / desktop) | none locally | ChatGPT connects only to remote HTTPS MCP servers; no stdio, no localhost | paste mode (CLI / `/text`) tested; a tunnel to the HTTP server is the user's own decision and is not recommended |
| Any LLM without MCP | REST / text | none | REST + OpenAPI: yes; text: yes |

## Snippets

### Generic stdio (Claude Desktop, Cursor, Windsurf, Gemini CLI, LM Studio, most others)

```json
{
  "mcpServers": {
    "fc26-career-mcp": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["C:\\Users\\you\\fc26-career-mcp\\server\\fc26cm_mcp.py"]
    }
  }
}
```

### VS Code (`%APPDATA%\Code\User\mcp.json`)

```json
{
  "servers": {
    "fc26-career-mcp": {
      "type": "stdio",
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["C:\\Users\\you\\fc26-career-mcp\\server\\fc26cm_mcp.py"]
    }
  }
}
```

### Claude Code

```
claude mcp add --scope user fc26-career-mcp -- "C:\...\python.exe" "C:\Users\you\fc26-career-mcp\server\fc26cm_mcp.py"
```

Skill: copy `skill\` to `%USERPROFILE%\.claude\skills\fc26-career-mcp\`.

### OpenAI Codex CLI (`%USERPROFILE%\.codex\config.toml`)

```toml
[mcp_servers.fc26-career-mcp]
command = "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"
args = ["C:\\Users\\you\\fc26-career-mcp\\server\\fc26cm_mcp.py"]
```

or `codex mcp add fc26-career-mcp -- "C:\...\python.exe" "C:\...\server\fc26cm_mcp.py"`.
Instructions: point Codex at `AGENTS.md` (it reads `AGENTS.md` in the working folder).

### Gemini CLI (`%USERPROFILE%\.gemini\settings.json`)

```json
{ "mcpServers": { "fc26-career-mcp": { "command": "C:\\...\\python.exe", "args": ["C:\\...\\server\\fc26cm_mcp.py"] } } }
```

### Streamable HTTP (clients that take a URL)

Start the HTTP server (`install.py --http-autostart` adds a Startup entry), then:

```json
{ "mcpServers": { "fc26-career-mcp": { "url": "http://127.0.0.1:26261/mcp" } } }
```

(VS Code: `"type": "http"`, Cursor/Windsurf/Gemini: `"url"` or `"httpUrl"` per their docs.)

### ChatGPT and other assistants without local MCP

```
<PY> <PRODUCT>\server\fc26cm_cli.py all
```

prints a compact text summary (status, career, squad, table, fixtures, transfers, events) to
paste into the chat, together with the rules in `AGENTS.md`. Narrower: `career`, `squad`,
`player "Name"`, `table`, `top --stat assists`, `transfers --history`, `save loans`,
`events --days 7 --ours`. `--json` prints the raw tool answer.
Custom GPTs / tool-calling agents on the same PC can use the REST API with `/openapi.json`.
