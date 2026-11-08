"""Tests for server/fc26cm_http.py (MCP Streamable HTTP + REST + OpenAPI + text) and the CLI."""
import json
import os
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import fc26cm_http as http_mod  # noqa: E402
import fc26cm_cli as cli  # noqa: E402

MANAGER = os.path.join(ROOT, "tests", "fixtures", "manager")
PLAYER = os.path.join(ROOT, "tests", "fixtures", "player")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.thread = http_mod.serve_in_thread(opts={"data_dir": MANAGER})
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, raw=False):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            body = r.read().decode("utf-8")
            return (r.status, r.headers, body) if raw else json.loads(body)

    def post(self, path, obj, raw=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8")
            return (r.status, r.headers, body) if raw else json.loads(body)

    def test_index_health_openapi_tools(self):
        idx = self.get("/")
        self.assertTrue(idx["read_only"])
        self.assertEqual(len(idx["tools"]), 9)
        h = self.get("/health")
        self.assertTrue(h["ok"])
        self.assertEqual(h["mode"], "manager")
        self.assertIn("/mcp", h["endpoints"])
        spec = self.get("/openapi.json")
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/api/fc26_squad", spec["paths"])
        self.assertIn("/text/fc26_squad", spec["paths"])
        self.assertEqual(spec["servers"][0]["url"], self.base)
        params = {p["name"] for p in spec["paths"]["/api/fc26_competitions"]["get"]["parameters"]}
        self.assertIn("compobjid", params)
        self.assertEqual(len(self.get("/api/tools")["tools"]), 9)

    def test_rest_get_post_and_errors(self):
        r = self.get("/api/fc26_competitions?view=top_players&stat=goals&limit=2")
        self.assertEqual(len(r["top_players"][0]["rows"]), 2)
        r = self.get("/api/fc26_squad?include_attributes=true&position=GK")
        self.assertIn("attributes", r["players"][0]) if "attributes" in r["players"][0] else self.assertIn("ratings", r["players"][0])
        r = self.post("/api/fc26_player", {"playerid": 204935})
        self.assertEqual(r["name"], "Jordan Pickford")
        r = self.get("/api/fc26_competitions?compobjid=notanint")
        self.assertEqual(r["error"], "bad_argument")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/nope")
        self.assertEqual(cm.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/whatever")
        self.assertEqual(cm.exception.code, 404)

    def test_text_endpoint(self):
        status, headers, body = self.get("/text/fc26_my_career", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn("[fc26-career-mcp] source=live scope=user_club", body)
        self.assertIn("CAREER mode=manager", body)
        self.assertIn("-> L (home 0-2)", body)

    def test_mcp_streamable_http(self):
        status, headers, body = self.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                   "params": {"protocolVersion": "2025-03-26"}}, raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
        init = json.loads(body)
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        status, _, body = self.post("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, raw=True)
        self.assertEqual(status, 202)
        self.assertEqual(body, "")
        r = self.post("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(r["result"]["tools"]), 9)
        r = self.post("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "fc26_transfers", "arguments": {"scope": "market", "limit": 1}}})
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(len(r["result"]["structuredContent"]["transfers"]), 1)
        batch = self.post("/mcp", [{"jsonrpc": "2.0", "id": 4, "method": "ping"},
                                   {"jsonrpc": "2.0", "id": 5, "method": "tools/list"}])
        self.assertEqual([b["id"] for b in batch], [4, 5])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/mcp")
        self.assertEqual(cm.exception.code, 405)
        req = urllib.request.Request(self.base + "/mcp", data=b"{bad", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(json.loads(cm.exception.read().decode())["error"]["code"], -32700)

    def test_options_cors(self):
        req = urllib.request.Request(self.base + "/mcp", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")


class CliTests(unittest.TestCase):
    def run_cli(self, *args, data_dir=MANAGER):
        cp = subprocess.run([sys.executable, os.path.join(ROOT, "server", "fc26cm_cli.py")] + list(args) +
                            ["--data-dir", data_dir], capture_output=True, text=True, timeout=60)
        return cp

    def test_all_and_commands(self):
        cp = self.run_cli("all")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        for mark in ("STATUS product=", "CAREER mode=manager", "SQUAD Everton", "table Premier League",
                     "upcoming (", "TRANSFERS Everton", "EVENTS game_date="):
            self.assertIn(mark, cp.stdout)
        cp = self.run_cli("player", "Pickford")
        self.assertIn("PLAYER Jordan Pickford (id 204935)", cp.stdout)
        cp = self.run_cli("player", "204935", "--json")
        self.assertEqual(json.loads(cp.stdout)["name"], "Jordan Pickford")
        cp = self.run_cli("top", "--stat", "assists", "--limit", "2")
        self.assertIn("assists (compobjid", cp.stdout)
        cp = self.run_cli("save", "loans")
        self.assertIn("SAVE DATA category=loans", cp.stdout)
        cp = self.run_cli("transfers", "--history")
        self.assertIn("offers history:", cp.stdout)
        cp = self.run_cli("events", "--days", "2", "--ours")
        self.assertIn("decoded payloads", cp.stdout)
        cp = self.run_cli("player", "zzz")
        self.assertEqual(cp.returncode, 1)
        self.assertIn("ERROR unknown_name", cp.stdout)
        cp = self.run_cli("tools")
        self.assertEqual(cp.stdout.count("fc26_"), 8 + cp.stdout.count("fc26_") - 8)
        cp = self.run_cli("openapi")
        self.assertEqual(json.loads(cp.stdout)["openapi"], "3.1.0")

    def test_player_mode_text(self):
        cp = self.run_cli("career", data_dir=PLAYER)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("my player: Test Player (id 299905)", cp.stdout)
        self.assertIn("recent ratings (int 5-10): [7, 6]", cp.stdout)
        cp = self.run_cli("transfers", data_dir=PLAYER)
        self.assertIn("offers: not available", cp.stdout)

    def test_render_error_candidates(self):
        txt = cli.render("fc26_player", {"_meta": {"source": "live", "scope": "mixed", "warnings": []},
                                         "error": "ambiguous_name", "hint": "h",
                                         "candidates": [{"playerid": 1, "name": "A B"}]})
        self.assertIn("ERROR ambiguous_name", txt)
        self.assertIn("candidate: A B (1)", txt)


if __name__ == "__main__":
    unittest.main()
