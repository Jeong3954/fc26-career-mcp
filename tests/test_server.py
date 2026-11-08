"""Tests for server/fc26cm_core.py and the stdio transport, on the sanitized fixtures
(manager and player careers). No game, no save file needed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import fc26cm_core as core  # noqa: E402

MANAGER = os.path.join(ROOT, "tests", "fixtures", "manager")
PLAYER = os.path.join(ROOT, "tests", "fixtures", "player")
TOOLS = ["fc26_status", "fc26_my_career", "fc26_squad", "fc26_player", "fc26_competitions", "fc26_transfers",
         "fc26_save_data", "fc26_global_events"]


def call(name, args=None, data_dir=MANAGER, **kw):
    kw.setdefault("save_refresh", False)
    return core.call_tool(name, args or {}, data_dir=data_dir, **kw)


class RegistryTests(unittest.TestCase):
    def test_tools_with_schemas(self):
        specs = core.tool_specs()
        self.assertEqual([t["name"] for t in specs], TOOLS + ["fc26_refresh_world"])  # refresh: test_refresh_world
        for t in specs:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertNotIn("run", t)
            self.assertIn("_meta", t["description"])

    def test_unknown_tool_and_argument(self):
        self.assertEqual(call("nope")["error"], "unknown_tool")
        r = call("fc26_squad", {"bogus": 1})
        self.assertEqual(r["error"], "unknown_argument")
        self.assertIn("_meta", r)


class MetaTests(unittest.TestCase):
    def test_every_tool_has_meta(self):
        for name in TOOLS:
            for d in (MANAGER, PLAYER):
                r = call(name, data_dir=d)
                m = r["_meta"]
                for k in ("source", "scope", "mode", "live_game_date", "snapshot_age_sec", "heartbeat_age_sec",
                          "bridge_alive", "compat", "warnings"):
                    self.assertIn(k, m, (name, d, k))
                self.assertIn(m["source"], ("live", "save", "live+save"))
                self.assertIn(m["scope"], ("user_club", "user_player", "global", "mixed"))

    def test_stale_bridge_warning_and_fresh(self):
        r = call("fc26_my_career", now=time.time())
        self.assertFalse(r["_meta"]["bridge_alive"])
        self.assertTrue(any("bridge not beating" in w for w in r["_meta"]["warnings"]))
        hb = json.load(open(os.path.join(MANAGER, "heartbeat.json")))
        r = call("fc26_my_career", now=hb["unix"] + 10)
        self.assertTrue(r["_meta"]["bridge_alive"])
        self.assertFalse(any("bridge not beating" in w for w in r["_meta"]["warnings"]))

    def test_no_snapshot_error(self):
        tmp = tempfile.mkdtemp()
        try:
            r = call("fc26_my_career", data_dir=tmp)
            self.assertEqual(r["error"], "no_snapshot")
            self.assertIn("career hub", r["hint"])
            s = call("fc26_status", data_dir=tmp)
            self.assertNotIn("error", s)
            self.assertFalse(s["files"]["snapshot.json"]["exists"])
            self.assertTrue(any("nothing written yet" in h for h in s["how_to_refresh"]))
        finally:
            shutil.rmtree(tmp)


class ManagerTests(unittest.TestCase):
    def test_status(self):
        r = call("fc26_status")
        self.assertEqual(r["mode"], "manager")
        self.assertEqual(r["compat"], "full")
        self.assertEqual(r["user_team"]["id"], 7)
        self.assertTrue(r["files"]["world.json"]["exists"])

    def test_my_career_home_away(self):
        r = call("fc26_my_career")
        self.assertEqual(r["_meta"]["scope"], "user_club")
        first = r["last_results"][0]  # Everton (7) home 0-2 AFC Bournemouth
        self.assertEqual(first["user_side"], "home")
        self.assertEqual(first["outcome"], "L")
        self.assertEqual(first["user_score"], "0-2")
        self.assertEqual(first["opponent"], "AFC Bournemouth")
        away = next(f for f in r["last_results"] if f["user_side"] == "away")
        hs, as_ = (int(x) for x in away["score"].split("-"))
        self.assertEqual(away["user_score"], "%d-%d" % (as_, hs))
        self.assertEqual(r["league_position"]["user_row"]["teamid"], 7)
        self.assertEqual(r["open_offers"], 3)
        self.assertIn("squad_summary", r)
        self.assertNotIn("user_player", r)

    def test_squad_save_extras_and_sort(self):
        r = call("fc26_squad")
        self.assertEqual(r["_meta"]["source"], "live+save")
        self.assertIn("save_as_of", r["_meta"])
        self.assertEqual(r["count"], 26)
        ovr = [p["overall"] for p in r["players"]]
        self.assertEqual(ovr, sorted(ovr, reverse=True))
        grad = next(p for p in r["players"] if p["playerid"] == 460033)
        self.assertTrue(grad["save_extras"]["youth_graduate"])
        loaned = next(p for p in r["players"] if p["playerid"] == 70448)
        self.assertIn("loan", loaned["save_extras"])
        self.assertTrue(all(p["scope"] == "user_club" for p in r["players"]))
        self.assertNotIn("attributes", r["players"][0])
        r = call("fc26_squad", {"include_attributes": True, "position": "GK"})
        self.assertTrue(all(p["position"] == "GK" for p in r["players"]))
        self.assertIn("ratings", r["players"][0])
        r = call("fc26_squad", {"sort": "age"})
        ages = [p["age"] for p in r["players"]]
        self.assertEqual(ages, sorted(ages))

    def test_player_by_name_id_ambiguous_unknown(self):
        r = call("fc26_player", {"name": "pickford"})
        self.assertEqual(r["playerid"], 204935)
        self.assertEqual(r["scope"], "user_club")
        self.assertIn("squad_player", r)
        self.assertIn("grudge_love", r["save_extras"])
        self.assertIn("national_rosters", r["save_extras"])
        r = call("fc26_player", {"playerid": "204935"})
        self.assertEqual(r["name"], "Jordan Pickford")
        r = call("fc26_player", {"name": "ja"})
        self.assertEqual(r["error"], "ambiguous_name")
        self.assertGreater(len(r["candidates"]), 1)
        r = call("fc26_player", {"name": "zzzz-nobody"})
        self.assertEqual(r["error"], "unknown_name")
        r = call("fc26_player", {})
        self.assertEqual(r["error"], "missing_argument")
        # transfer target: not in squad, detail from targets[]
        r = call("fc26_player", {"playerid": 253054})
        self.assertIn("transfer_target", r)
        self.assertEqual(r["scope"], "global")

    def test_competitions_views(self):
        r = call("fc26_competitions")
        self.assertEqual(r["view"], "standings")
        self.assertEqual(r["_meta"]["scope"], "user_club")
        t = r["tables"][0]
        self.assertEqual(t["compobjid"], 823)
        self.assertTrue(any(row.get("user_club") for row in t["rows"]))
        r = call("fc26_competitions", {"view": "fixtures", "last": 2, "next": 1})
        self.assertEqual(len(r["played"]), 2)
        self.assertEqual(len(r["upcoming"]), 1)
        self.assertIn("outcome", r["played"][0])
        self.assertEqual(r["teamid"], 7)
        r = call("fc26_competitions", {"view": "fixtures", "teamid": 1})
        self.assertEqual(r["_meta"]["scope"], "global")
        self.assertTrue(all(1 in (f["home_id"], f["away_id"]) for f in r["played"] + r["upcoming"]))
        r = call("fc26_competitions", {"view": "top_players", "stat": "assists", "limit": 3})
        self.assertEqual(r["top_players"][0]["rows"][0]["rank"], 1)
        self.assertEqual(len(r["top_players"][0]["rows"]), 3)
        self.assertIn(r["top_players"][0]["rows"][0]["scope"], ("user_club", "global"))
        r = call("fc26_competitions", {"view": "top_players", "stat": "nope"})
        self.assertEqual(r["error"], "bad_stat")
        r = call("fc26_competitions", {"view": "index"})
        pl = next(c for c in r["competitions"] if c["compobjid"] == 821)
        self.assertTrue(pl["user_plays"])
        self.assertEqual(pl["standings_groups"][0]["group_compobjid"], 823)
        r = call("fc26_competitions", {"view": "standings", "competition": "premier"})
        self.assertEqual(r["tables"][0]["competition"], "Premier League")
        r = call("fc26_competitions", {"view": "wrong"})
        self.assertEqual(r["error"], "bad_view")

    def test_transfers_user_club_and_market(self):
        r = call("fc26_transfers")
        self.assertEqual(r["_meta"]["scope"], "user_club")
        self.assertEqual(len(r["offers"]), 3)
        o = r["offers"][0]
        self.assertEqual(o["stage"], "open")
        self.assertIn(o["last_action"], (6, 2, 3))
        self.assertIn("last_action_label", o)
        self.assertNotIn("actions", o)
        self.assertNotIn("raw", o)
        self.assertNotIn("raw_loan_offer", o)
        self.assertNotIn("offers_history", r)
        r = call("fc26_transfers", {"include_history": True, "detail": True})
        self.assertEqual(len(r["offers_history"]), 14)
        self.assertIn("actions", r["offers_history"][0])
        self.assertEqual(r["offers_history"][0]["stage"], "closed")
        r = call("fc26_transfers", {"scope": "market", "limit": 5})
        self.assertEqual(r["_meta"]["scope"], "global")
        self.assertEqual(len(r["transfers"]), 5)
        self.assertTrue(all("scope" in t for t in r["transfers"]))
        self.assertTrue(any(c["scope"] == "user_club" for c in r["club_transfer_finance"]))
        r = call("fc26_transfers", {"scope": "market", "teamid": 7})
        self.assertTrue(all(7 in (t["from_id"], t["to_id"]) for t in r["transfers"]))

    def test_save_data_categories_and_identity(self):
        r = call("fc26_save_data")
        self.assertEqual(r["_meta"]["source"], "save")
        self.assertEqual(r["category"], "summary")
        self.assertIn("national_teams", r["sections"])
        r = call("fc26_save_data", {"category": "national_teams"})
        calls = r["user_players_in_national_rosters"]
        self.assertTrue(any(c["playerid"] == 204935 and c["name"] == "Jordan Pickford" for c in calls))
        r = call("fc26_save_data", {"category": "national_teams", "teamid": 1318})
        self.assertEqual(r["national_teams"][0]["team"], "England")
        self.assertEqual(r["_meta"]["scope"], "global")
        r = call("fc26_save_data", {"category": "loans"})
        self.assertTrue(r["loans"][0]["loan_end"].startswith("20"))
        self.assertEqual(r["loans"][0]["player"], "Adam Aznou")
        r = call("fc26_save_data", {"category": "board_history"})
        self.assertIn("stale_fields", r)
        r = call("fc26_save_data", {"category": "managers"})
        self.assertEqual(r["managers"][0]["scope"], "user_club")
        r = call("fc26_save_data", {"category": "bogus"})
        self.assertEqual(r["error"], "bad_category")
        # identity mismatch -> different_career
        tmp = tempfile.mkdtemp()
        try:
            for n in os.listdir(MANAGER):
                shutil.copyfile(os.path.join(MANAGER, n), os.path.join(tmp, n))
            sd = json.load(open(os.path.join(tmp, "save_extract.json")))
            sd["user"]["clubteamid"] = 999
            json.dump(sd, open(os.path.join(tmp, "save_extract.json"), "w"))
            r = call("fc26_save_data", data_dir=tmp)
            self.assertEqual(r["error"], "different_career")
            sq = call("fc26_squad", data_dir=tmp)
            self.assertEqual(sq["_meta"]["source"], "live", "no save extras from another career")
        finally:
            shutil.rmtree(tmp)

    def test_global_events_scope(self):
        r = call("fc26_global_events")
        self.assertEqual(r["_meta"]["scope"], "global")
        self.assertEqual(r["event_names"]["scope"], "global")
        self.assertTrue(all("scope" in p for p in r["event_payloads"]["rows"]))
        ours = call("fc26_global_events", {"ours_only": True})
        self.assertTrue(all(p["scope"] in ("user_club", "user_player") for p in ours["event_payloads"]["rows"]))
        susp = call("fc26_global_events", {"kind": "suspension"})
        self.assertTrue(all("suspension" in (p["kind"] or "") for p in susp["event_payloads"]["rows"]))
        recent = call("fc26_global_events", {"days": 1})
        self.assertTrue(all(n["date"] >= "2027-01-05" for n in recent["event_names"]["rows"]))


class PlayerTests(unittest.TestCase):
    def test_status_and_career(self):
        r = call("fc26_status", data_dir=PLAYER)
        self.assertEqual(r["mode"], "player")
        self.assertEqual(r["user_player"]["playerid"], 299905)
        r = call("fc26_my_career", data_dir=PLAYER)
        self.assertEqual(r["_meta"]["scope"], "user_player")
        up = r["user_player"]
        self.assertEqual(up["playerid"], 299905)
        self.assertEqual(up["rating_unit"], "rating_int_5_10")
        self.assertIn("history", up)
        self.assertIn("career", up)
        self.assertEqual(r["transfer_requests"]["numloanrequests"], 1)
        self.assertNotIn("squad_summary", r)
        self.assertEqual(r["last_results"][0]["outcome"], "W")

    def test_transfers_offers_null(self):
        r = call("fc26_transfers", data_dir=PLAYER)
        self.assertIsNone(r["offers"])
        self.assertIn("not_available_reason", r)
        self.assertEqual(r["_meta"]["scope"], "user_player")
        self.assertEqual(r["user_requests"]["numloanrequests"], 1)
        self.assertTrue(all(52 in (t["from_id"], t["to_id"]) for t in r["transfers"]))

    def test_squad_player_mode(self):
        r = call("fc26_squad", data_dir=PLAYER)
        self.assertEqual(r["count"], 18)
        self.assertEqual(r["_meta"]["source"], "live")
        self.assertEqual(r["user_player"]["playerid"], 299905)

    def test_player_scope_user_player(self):
        r = call("fc26_player", {"playerid": 299905}, data_dir=PLAYER)
        self.assertEqual(r["scope"], "user_player")
        self.assertIn("user_player", r)
        r = call("fc26_player", {"name": "Soul"}, data_dir=PLAYER)
        self.assertEqual(r["playerid"], 265695)
        self.assertIn("top_list_rows", r)

    def test_save_missing(self):
        r = call("fc26_save_data", data_dir=PLAYER)
        self.assertEqual(r["error"], "no_save_extract")

    def test_competitions_and_events(self):
        r = call("fc26_competitions", {"view": "top_players"}, data_dir=PLAYER)
        row = r["top_players"][0]["rows"][0]
        self.assertEqual(row["scope"], "user_club")
        r = call("fc26_global_events", data_dir=PLAYER)
        self.assertEqual(r["event_payloads"]["rows"][0]["scope"], "user_club")


class StdioTransportTests(unittest.TestCase):
    def run_stdio(self, msgs, data_dir=MANAGER):
        inp = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in msgs)
        cp = subprocess.run([sys.executable, os.path.join(ROOT, "server", "fc26cm_mcp.py"), "--data-dir", data_dir],
                            input=inp, capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        return [json.loads(ln) for ln in cp.stdout.splitlines() if ln.strip()]

    def test_handshake_list_call(self):
        res = self.run_stdio([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fc26_my_career", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "fc26_player", "arguments": {"name": "zz"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
            {"jsonrpc": "2.0", "id": 6, "method": "nope/x"},
            "not json at all",
        ])
        by_id = {r.get("id"): r for r in res}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "fc26-career-mcp")
        self.assertEqual(len(by_id[2]["result"]["tools"]), 9)
        call_res = by_id[3]["result"]
        self.assertFalse(call_res["isError"])
        self.assertEqual(json.loads(call_res["content"][0]["text"])["_meta"]["scope"], "user_club")
        self.assertEqual(call_res["structuredContent"]["user_team"]["id"], 7)
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertEqual(by_id[5]["result"], {})
        self.assertEqual(by_id[6]["error"]["code"], -32601)
        self.assertEqual(by_id[None]["error"]["code"], -32700)
        self.assertEqual(len(res), 7, "notification gets no response")

    def test_unknown_protocol_version_falls_back(self):
        res = self.run_stdio([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
        self.assertEqual(res[0]["result"]["protocolVersion"], core.PROTOCOL_VERSIONS[0])


if __name__ == "__main__":
    unittest.main()
