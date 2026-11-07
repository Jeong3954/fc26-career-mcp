"""Tests for save/save_reader.py using fake tables (no save file needed)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "save"))

import save_reader  # noqa: E402

CLUB = 1
OTHER = 2


def manager_tables():
    return {
        "career_users": [{"userid": 7, "clubteamid": CLUB, "firstname": "Ann", "surname": "Boss", "playertype": 0,
                          "usertype": 0, "nationalteamid": 0, "primarycompobjid": 13, "leagueid": 13}],
        "career_managerinfo": [{"clubteamid": CLUB, "artificialkey": 1, "budget": 100}],
        "career_playasplayer": [],
        "teamplayerlinks": [
            {"teamid": CLUB, "playerid": 101}, {"teamid": CLUB, "playerid": 102},
            {"teamid": OTHER, "playerid": 201},
            {"teamid": 1318, "playerid": 101}, {"teamid": 1318, "playerid": 301},  # England (men)
            {"teamid": 9999, "playerid": 401},  # a women's national team
        ],
        "career_playercontract": [
            {"teamid": CLUB, "playerid": 101}, {"teamid": CLUB, "playerid": 103},  # 103 loaned out
            {"teamid": CLUB, "playerid": 104},  # sold: stale contract row
        ],
        "playerloans": [{"playerid": 103, "teamidloanedfrom": CLUB, "teamidloanedto": OTHER}],
        "leagueteamlinks": [
            {"leagueid": 13, "teamid": CLUB}, {"leagueid": 78, "teamid": 1318},
            {"leagueid": 2136, "teamid": 9999}, {"leagueid": 78, "teamid": 1318},  # duplicate link
        ],
        "teams": [{"teamid": CLUB, "teamname": "Our Club", "captainid": 101, "penaltytakerid": 0},
                  {"teamid": OTHER, "teamname": "Them"}, {"teamid": 1318, "teamname": "England"},
                  {"teamid": 9999, "teamname": "Ladies"}],
        "playersuspensions": [{"teamid": CLUB, "playerid": 102, "numbersuspendedmatches": 2},
                              {"teamid": OTHER, "playerid": 201}],
        "manager": [{"managerid": 5, "commonname": "", "firstname": "Pep", "surname": "G", "teamid": OTHER,
                     "nationality": 45, "birthdate": 145000, "starrating": 5},
                    {"managerid": 6, "commonname": "Mou", "firstname": "J", "surname": "M", "teamid": CLUB,
                     "nationality": 38, "birthdate": 144000, "starrating": 4}],
        "players": [{"playerid": 103, "firstnameid": 1, "lastnameid": 2, "commonnameid": 0}],
        "dcplayernames": [{"nameid": 1, "name": "Loan"}, {"nameid": 2, "name": "Guy"}],
    }


class ExtractTests(unittest.TestCase):
    def test_squad_rule_and_schema_keys(self):
        res = save_reader.extract(save_reader.DictTables(manager_tables()), save_file="CmMgrC1", save_mtime=0.0)
        self.assertEqual(res["schema"], 2)
        self.assertEqual(res["source"], "save")
        self.assertEqual(res["product"], "fc26-career-mcp")
        self.assertEqual(res["team_id"], CLUB)
        self.assertEqual(res["squad_ids"], [101, 102, 103])  # links + loaned-out; 104 (sold) excluded
        self.assertEqual(res["loaned_out_ids"], [103])
        self.assertEqual(res["excluded_contract_rows"], [104])
        self.assertEqual([r["playerid"] for r in res["suspensions"]], [102])
        self.assertEqual(res["team_roles"], {"captainid": 101})
        self.assertEqual(res["player_names"], {"103": "Loan Guy"})
        self.assertEqual(res["team_names"]["2"], "Them")
        self.assertIn("names_note", res)
        for k in ("suspensions", "grudgelove", "previous_teams", "season_start", "squad_ranking", "youth_graduates",
                  "team_roles", "presigned", "loans", "manager_info", "manager_history", "news_events",
                  "team_names", "player_names", "squad_ids", "loaned_out_ids", "excluded_contract_rows",
                  "squad_rule", "game_date_note", "as_of_save", "save_mtime", "save_file", "written"):
            self.assertIn(k, res)
        self.assertNotIn("artificialkey", res["manager_info"])

    def test_manager_career_type_and_identity(self):
        res = save_reader.extract(save_reader.DictTables(manager_tables()))
        self.assertEqual(res["career_type"], "manager")
        self.assertEqual(res["user"]["userid"], 7)
        self.assertEqual(res["user"]["clubteamid"], CLUB)
        self.assertEqual(set(res["user"]), set(save_reader.USER_FIELDS))
        self.assertIsNone(res["player"])
        self.assertEqual(res["player_history"], [])

    def test_national_teams(self):
        res = save_reader.extract(save_reader.DictTables(manager_tables()))
        nat = {t["teamid"]: t for t in res["national_teams"]}
        self.assertEqual(set(nat), {1318, 9999})
        self.assertEqual(nat[1318]["gender"], "men")
        self.assertEqual(nat[1318]["name"], "England")
        self.assertEqual(nat[1318]["roster"], [101, 301])
        self.assertEqual(nat[1318]["roster_count"], 2)
        self.assertEqual(nat[9999]["gender"], "women")
        self.assertEqual(nat[9999]["roster"], [401])
        self.assertEqual(len(res["national_teams"]), 2)  # duplicate link collapsed

    def test_roster_cap(self):
        t = manager_tables()
        t["teamplayerlinks"] = [{"teamid": 1318, "playerid": i} for i in range(60)]
        res = save_reader.extract(save_reader.DictTables(t))
        eng = next(x for x in res["national_teams"] if x["teamid"] == 1318)
        self.assertEqual(len(eng["roster"]), 40)
        self.assertEqual(eng["roster_count"], 60)

    def test_managers_mapping(self):
        res = save_reader.extract(save_reader.DictTables(manager_tables()))
        self.assertEqual(res["managers"], [
            {"managerid": 5, "name": "Pep G", "teamid": OTHER, "nationality": 45, "birthdate": 145000, "starrating": 5},
            {"managerid": 6, "name": "Mou", "teamid": CLUB, "nationality": 38, "birthdate": 144000, "starrating": 4},
        ])

    def test_player_career(self):
        t = manager_tables()
        t["career_managerinfo"] = []
        t["career_playasplayer"] = [{"userid": 7, "playerid": 102, "position": 14, "playerrequest": 1,
                                     "numtransferrequests": 2, "numloanrequests": 0, "numwithdrawrequests": 0,
                                     "requestactiondays": 3, "lastwithdrawdate": 0, "playedlastmatch": 1}]
        t["career_playasplayerhistory"] = [{"season": 1, "teamid": CLUB, "appearances": 30, "goals": 9, "assists": 4,
                                            "motm": 2, "matchratings": 700, "cleansheets": 0, "wins": 15, "draws": 5,
                                            "loses": 10, "wage": 50000, "overall": 78, "value": 1000000,
                                            "tableposition": 4, "leaguetrophies": 0, "domesticcuptrophies": 1,
                                            "continentalcuptrophies": 0, "clublevel": 3, "fouls": 12}]
        res = save_reader.extract(save_reader.DictTables(t))
        self.assertEqual(res["career_type"], "player")
        self.assertEqual(res["team_id"], CLUB)  # club squad rules still apply
        self.assertEqual(res["squad_ids"], [101, 102, 103])
        self.assertEqual(res["player"]["playerid"], 102)
        self.assertEqual(res["player"]["numtransferrequests"], 2)
        self.assertEqual(set(res["player"]), set(save_reader.PLAYER_FIELDS))
        self.assertEqual(res["user_player"], res["player"])
        self.assertEqual(len(res["player_history"]), 1)
        self.assertEqual(set(res["player_history"][0]), set(save_reader.PLAYER_HISTORY_FIELDS))
        self.assertEqual(res["player_history"][0]["goals"], 9)
        self.assertNotIn("fouls", res["player_history"][0])

    def test_player_row_with_zero_id_is_manager(self):
        t = manager_tables()
        t["career_playasplayer"] = [{"userid": 7, "playerid": 0}]
        self.assertEqual(save_reader.detect_career_type(save_reader.DictTables(t)), "manager")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fc26cm_test_")
        self.data_dir = os.path.join(self.tmp, "data")
        self.saves_dir = os.path.join(self.tmp, "saves")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_no_save_subprocess(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "save", "save_reader.py"), "--json",
                            "--saves-dir", os.path.join(self.tmp, "nonexistent"), "--data-dir", self.data_dir],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        out = json.loads(lines[0])
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_save")
        self.assertFalse(os.path.exists(os.path.join(self.data_dir, "save_extract.json")))

    def test_quiet_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = save_reader.main(["--quiet", "--saves-dir", self.saves_dir, "--data-dir", self.data_dir])
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(status["error"], "no_save")

    def test_unchanged_short_circuit(self):
        os.makedirs(self.saves_dir)
        os.makedirs(self.data_dir)
        save = os.path.join(self.saves_dir, "CmMgrC0001")
        with open(save, "wb") as f:
            f.write(b"not a real save")
        with open(os.path.join(self.saves_dir, "Squads0001"), "wb") as f:  # ignored: not a career save
            f.write(b"x")
        mtime = os.path.getmtime(save)
        with open(os.path.join(self.data_dir, "save_extract.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": 2, "save_file": "CmMgrC0001", "save_mtime": mtime}, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = save_reader.main(["--saves-dir", self.saves_dir, "--data-dir", self.data_dir])
        self.assertEqual(status, {"ok": True, "unchanged": True})
        self.assertEqual(json.loads(buf.getvalue()), {"ok": True, "unchanged": True})
        # --force bypasses the short-circuit and tries to parse the junk file -> parse_failed, exit 0 style
        with contextlib.redirect_stdout(io.StringIO()):
            status = save_reader.main(["--force", "--saves-dir", self.saves_dir, "--data-dir", self.data_dir])
        self.assertFalse(status["ok"])
        self.assertEqual(status["error"], "parse_failed")

    def test_newest_save_picks_latest_career_file(self):
        os.makedirs(self.saves_dir)
        old = os.path.join(self.saves_dir, "CmMgrC0001")
        new = os.path.join(self.saves_dir, "CmPlyC0002")
        for p in (old, new):
            with open(p, "wb") as f:
                f.write(b"x")
        now = time.time()
        os.utime(old, (now - 100, now - 100))
        os.utime(new, (now, now))
        self.assertEqual(save_reader.newest_save(self.saves_dir), new)


if __name__ == "__main__":
    unittest.main()
