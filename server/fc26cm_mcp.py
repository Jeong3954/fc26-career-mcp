#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - local MCP server, stdio transport.

Started by the MCP client (Claude Desktop, Claude Code, Cursor, Windsurf, VS Code, Gemini CLI,
Codex CLI, LM Studio, ...):  python fc26cm_mcp.py [--data-dir DIR] [--saves-dir DIR]

For clients that need a URL (Streamable HTTP) or a REST API, run fc26cm_http.py instead.
Standard library only, Python 3.9+. Read-only: eight fc26_* tools that read local JSON files.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fc26cm_core as core  # noqa: E402

INSTRUCTIONS = (
    "Read-only data of the user's EA SPORTS FC 26 offline career (manager or player). Call fc26_status first. "
    "Every answer carries _meta (source live/save, scope, live_game_date, ages, warnings): say what date a value "
    "is as of, never attribute global events to the user's club, read home/away ids before saying 'you won', "
    "ratings are integers 5-10, and nothing here changes the game."
)


def handle(req, opts):
    """One JSON-RPC request -> result dict or raises JsonRpcError."""
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        want = params.get("protocolVersion")
        version = want if want in core.PROTOCOL_VERSIONS else core.PROTOCOL_VERSIONS[0]
        return {"protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": core.SERVER_NAME, "version": core.VERSION},
                "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": core.tool_specs()}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        res = core.call_tool(name, args, data_dir=opts.get("data_dir"), saves_dir=opts.get("saves_dir"))
        return {"content": [{"type": "text", "text": core.to_json(res, compact=True)}],
                "structuredContent": res if isinstance(res, dict) else None,
                "isError": bool(isinstance(res, dict) and res.get("error"))}
    if method in ("resources/list", "resources/templates/list"):
        return {"resources": [], "resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    raise JsonRpcError(-32601, "Method not found: %s" % method)


class JsonRpcError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def dispatch(msg, opts):
    """Full JSON-RPC handling for one message (dict). Returns a response dict or None (notification)."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    id_ = msg.get("id")
    method = msg.get("method")
    if method is None:
        return None  # a response from the client (not expected): ignore
    if id_ is None:
        return None  # notification (notifications/initialized, notifications/cancelled ...)
    try:
        result = handle(msg, opts)
        return {"jsonrpc": "2.0", "id": id_, "result": result}
    except JsonRpcError as e:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": e.code, "message": e.message}}
    except Exception as e:  # keep serving
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32603, "message": "%s: %s" % (type(e).__name__, e)}}


def serve_stdio(opts):
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            stdout.write((json.dumps(out) + "\n").encode("utf-8"))
            stdout.flush()
            continue
        msgs = msg if isinstance(msg, list) else [msg]
        responses = [r for r in (dispatch(m, opts) for m in msgs) if r is not None]
        if not responses:
            continue
        payload = responses if isinstance(msg, list) else responses[0]
        stdout.write((json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description="%s - MCP stdio server" % core.PRODUCT)
    ap.add_argument("--data-dir", default=None, help="bridge data folder (default Documents\\FC26CareerMCP)")
    ap.add_argument("--saves-dir", default=None, help="FC 26 settings folder with the Cm* saves")
    ap.add_argument("--version", action="store_true", help="print the server version and exit")
    args = ap.parse_args(argv)
    if args.version:
        print("%s server %s" % (core.SERVER_NAME, core.VERSION))
        return 0
    serve_stdio({"data_dir": args.data_dir, "saves_dir": args.saves_dir})
    return 0


if __name__ == "__main__":
    sys.exit(main())
