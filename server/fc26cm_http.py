#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - local HTTP server (localhost only).

One process, three faces, all read-only:
  * MCP Streamable HTTP  POST http://127.0.0.1:<port>/mcp   (JSON-RPC, stateless, JSON responses)
  * REST JSON API        GET  http://127.0.0.1:<port>/api/<tool>?arg=value   (also POST with a JSON body)
                         GET  /api/tools  /openapi.json  /health  /
  * CLI paste text       GET  /text/<tool>?arg=value   (the same compact summaries fc26cm_cli.py prints)

For LLM clients that need a URL instead of a stdio command, and for any assistant without MCP
(the REST answer or the /text summary can be pasted into a chat).

    python fc26cm_http.py [--port 26261] [--host 127.0.0.1] [--data-dir DIR] [--saves-dir DIR]

Binds to 127.0.0.1 by default: nothing is reachable from other machines. Standard library only.
"""
import argparse
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fc26cm_core as core  # noqa: E402
import fc26cm_mcp as mcp  # noqa: E402
import fc26cm_cli as cli  # noqa: E402

DEFAULT_PORT = 26261


def openapi_spec(base_url):
    paths = {}
    for t in core.tool_specs():
        params = []
        for name, p in (t["inputSchema"].get("properties") or {}).items():
            schema = {k: v for k, v in p.items() if k in ("type", "enum")}
            params.append({"name": name, "in": "query", "required": False, "schema": schema})
        op = {"operationId": t["name"], "summary": t["description"][:120],
              "description": t["description"], "parameters": params,
              "responses": {"200": {"description": "Tool answer; `_meta` carries source, scope, game date, ages and "
                                                   "warnings; an `error` key means the tool could not answer.",
                                    "content": {"application/json": {"schema": {"type": "object"}}}}}}
        paths["/api/" + t["name"]] = {
            "get": op,
            "post": {**op, "operationId": t["name"] + "_post", "parameters": [],
                     "requestBody": {"content": {"application/json": {"schema": t["inputSchema"]}}}}}
        paths["/text/" + t["name"]] = {"get": {"operationId": t["name"] + "_text", "parameters": params,
                                              "summary": "Compact plain-text summary to paste into any chat",
                                              "responses": {"200": {"description": "text/plain"}}}}
    paths["/health"] = {"get": {"operationId": "health", "summary": "server + bridge health",
                                "responses": {"200": {"description": "JSON"}}}}
    paths["/api/tools"] = {"get": {"operationId": "tools", "summary": "tool list with JSON schemas",
                                   "responses": {"200": {"description": "JSON"}}}}
    return {"openapi": "3.1.0",
            "info": {"title": core.PRODUCT, "version": core.VERSION,
                     "description": "Read-only local API over the user's FC 26 offline career data (Live Editor "
                                    "bridge files + a parsed copy of the newest save). " + core.RULES},
            "servers": [{"url": base_url}],
            "paths": paths}


def health(opts):
    st = core.call_tool("fc26_status", {}, data_dir=opts.get("data_dir"), saves_dir=opts.get("saves_dir"),
                        save_refresh=False)
    m = st.get("_meta") or {}
    return {"ok": True, "server": core.SERVER_NAME, "version": core.VERSION, "data_dir": st.get("data_dir"),
            "bridge_alive": m.get("bridge_alive"), "mode": st.get("mode"), "compat": st.get("compat"),
            "game_date": st.get("game_date"), "snapshot": bool((st.get("files") or {}).get("snapshot.json", {}).get("exists")),
            "heartbeat_age_sec": m.get("heartbeat_age_sec"), "endpoints": ["/mcp", "/api/<tool>", "/text/<tool>",
                                                                            "/api/tools", "/openapi.json", "/health"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "fc26-career-mcp/" + core.VERSION
    opts = {}

    def log_message(self, fmt, *args):  # quiet unless --verbose
        if self.opts.get("verbose"):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    # -- helpers --
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = core.to_json(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return raw

    def _base_url(self):
        host = self.headers.get("Host") or "127.0.0.1:%d" % self.server.server_address[1]
        return "http://" + host

    def _route(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query, keep_blank_values=False)
        args = {k: v[-1] for k, v in q.items()}
        return u.path.rstrip("/") or "/", args

    # -- verbs --
    def do_OPTIONS(self):
        self._send(204, "")

    def do_GET(self):
        path, args = self._route()
        if path == "/":
            self._send(200, {"product": core.PRODUCT, "version": core.VERSION, "read_only": True,
                             "mcp": self._base_url() + "/mcp", "openapi": self._base_url() + "/openapi.json",
                             "tools": [t["name"] for t in core.TOOLS], "health": self._base_url() + "/health"})
        elif path == "/health":
            self._send(200, health(self.opts))
        elif path == "/openapi.json":
            self._send(200, openapi_spec(self._base_url()))
        elif path == "/api/tools":
            self._send(200, {"tools": core.tool_specs()})
        elif path.startswith("/api/"):
            name = path[len("/api/"):]
            res = core.call_tool(name, args, data_dir=self.opts.get("data_dir"), saves_dir=self.opts.get("saves_dir"))
            self._send(404 if res.get("error") == "unknown_tool" else 200, res)
        elif path.startswith("/text/"):
            name = path[len("/text/"):]
            res = core.call_tool(name, args, data_dir=self.opts.get("data_dir"), saves_dir=self.opts.get("saves_dir"))
            self._send(404 if res.get("error") == "unknown_tool" else 200, cli.render(name, res),
                       ctype="text/plain; charset=utf-8")
        elif path == "/mcp":
            # No server-initiated stream: stateless server (spec allows 405 for GET).
            self._send(405, {"error": "GET /mcp not supported (no server-to-client stream); POST JSON-RPC here"})
        else:
            self._send(404, {"error": "not_found", "hint": "/, /health, /openapi.json, /api/<tool>, /text/<tool>, /mcp"})

    def do_POST(self):
        path, args = self._route()
        raw = self._body()
        if path == "/mcp":
            return self._mcp(raw)
        if path.startswith("/api/"):
            name = path[len("/api/"):]
            body = {}
            if raw.strip():
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    return self._send(400, {"error": "bad_json"})
            if not isinstance(body, dict):
                return self._send(400, {"error": "bad_json", "hint": "send a JSON object of arguments"})
            body.update(args)
            res = core.call_tool(name, body, data_dir=self.opts.get("data_dir"), saves_dir=self.opts.get("saves_dir"))
            return self._send(404 if res.get("error") == "unknown_tool" else 200, res)
        self._send(404, {"error": "not_found"})

    def _mcp(self, raw):
        try:
            msg = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except ValueError:
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "Parse error"}})
        if msg is None:
            return self._send(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "empty body"}})
        msgs = msg if isinstance(msg, list) else [msg]
        responses = [r for r in (mcp.dispatch(m, self.opts) for m in msgs) if r is not None]
        if not responses:
            return self._send(202, "")  # notifications / responses only
        payload = responses if isinstance(msg, list) else responses[0]
        self._send(200, payload, extra={"MCP-Protocol-Version": core.PROTOCOL_VERSIONS[0]})


def make_server(host, port, opts):
    handler = type("FC26Handler", (Handler,), {"opts": dict(opts)})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(host="127.0.0.1", port=0, opts=None):
    """For tests and embedding: returns (server, thread); server.server_address[1] is the port."""
    srv = make_server(host, port, opts or {})
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


def main(argv=None):
    ap = argparse.ArgumentParser(description="%s - local HTTP server (MCP Streamable HTTP + REST)" % core.PRODUCT)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1; keep it local)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--saves-dir", default=None)
    ap.add_argument("--verbose", action="store_true", help="log requests")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: binding to %s exposes your career data to the network; use 127.0.0.1" % args.host,
              file=sys.stderr)
    srv = make_server(args.host, args.port, {"data_dir": args.data_dir, "saves_dir": args.saves_dir,
                                             "verbose": args.verbose})
    port = srv.server_address[1]
    print("%s HTTP server on http://%s:%d  (MCP: /mcp, REST: /api/<tool>, OpenAPI: /openapi.json, health: /health)"
          % (core.SERVER_NAME, args.host, port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
