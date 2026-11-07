#!/usr/bin/env python3
"""Build sanitized test fixtures for the fc26-career-mcp MCP server.

Usage:
    python tools/make_fixtures.py [--src <dir with real bridge files>] [--out tests/fixtures]

Manager fixture: real bridge files (snapshot/world/heartbeat/save_extract) sanitized:
  * the manager's name becomes "Test Manager" (club/player names are game data, kept)
  * world.json is truncated (~60 fixtures, 6 tables, 3 top entries, 40 transfers)
  * raw/hex/w fields are dropped
  * keys are upgraded to schema 4 (product, compat, memory_reads, targets, events_note)
  * status.json is synthesized from the snapshot header
Player fixture: generated from DESIGN.md's player-only section (AS Roma, user player "Test Player" 299905).
Stdlib only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SRC = os.environ.get("FC26CM_FIXTURE_SRC", "bridge-dump")  # dir with real bridge output files
PRODUCT = "fc26-career-mcp"
DROP_KEYS = ("raw", "raw_loan_offer", "hex", "w")  # "w" only as a top-level negotiation/raw field, not table wins


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def strip_raw(obj, in_table_row=False):
    """Drop raw/hex/w fields recursively. Standings rows keep their 'w' (wins)."""
    if isinstance(obj, dict):
        is_table_row = "pts" in obj and "teamid" in obj
        out = {}
        for k, v in obj.items():
            if k in ("raw", "raw_loan_offer", "hex"):
                continue
            if k == "w" and not is_table_row:
                continue
            out[k] = strip_raw(v)
        return out
    if isinstance(obj, list):
        return [strip_raw(x) for x in obj]
    return obj


def manager_fixture(src, out):
    snap = strip_raw(load(os.path.join(src, "snapshot.json")))
    world = load(os.path.join(src, "world.json"))
    hb = load(os.path.join(src, "heartbeat.json"))
    save = load(os.path.join(src, "save_extract.json"))

    # --- snapshot: sanitize + schema 4 keys
    snap["schema"] = 4
    snap["product"] = PRODUCT
    snap["compat"] = "full"
    snap["memory_reads"] = True
    snap["compat_reason"] = None
    snap.setdefault("targets", [])
    snap["events_note"] = ("events[] are GLOBAL counts of event names (never attributed); "
                           "event_payloads[] carry their own teamid/playerid and a scope")
    user = snap.get("user") or {}
    user["name"] = "Manager"
    user["firstname"] = "Test"
    snap["user"] = user
    # The negotiation lists reference players outside the squad: give the fixture one target row
    # so fc26_player can be exercised on a non-squad player with full detail.
    squad_ids = {p.get("playerid") for p in (snap.get("squad") or {}).get("players", [])}
    if not snap["targets"]:
        for row in ((snap.get("bids") or []) + (snap.get("offers") or []) + (snap.get("player_talks") or [])
                    + (snap.get("offers_history") or [])):
            pid = row.get("playerid")
            if pid and pid not in squad_ids:
                snap["targets"].append({
                    "playerid": pid, "name": row.get("player"), "team": row.get("club"),
                    "teamid": row.get("club_id") or row.get("sell_id"), "age": 26, "position": "RB", "overall": 74, "potential": 78,
                    "attributes": {"pace": 80, "shooting": 55, "passing": 68, "dribbling": 74,
                                   "defending": 70, "physical": 66},
                })
                break
    # keep the payload list small
    snap["event_payloads"] = (snap.get("event_payloads") or [])[:40]
    snap["raw_events"] = (snap.get("raw_events") or [])[:20]

    # --- world: truncate
    user_team = world.get("user_team") or (snap.get("user_team") or {}).get("id")
    league_stage = (snap.get("league_table") or {}).get("compobjid")
    tables = world.get("tables") or []
    keep_tables = [t for t in tables if t.get("stage_compobjid") == league_stage or t.get("compobjid") == league_stage]
    for t in tables:
        if len(keep_tables) >= 6:
            break
        if t not in keep_tables and not str(t.get("competition", "")).startswith("COBJ"):
            keep_tables.append(t)
    fixtures = world.get("fixtures") or []
    ours = [f for f in fixtures if user_team in (f.get("home_id"), f.get("away_id"))]
    others = [f for f in fixtures if f not in ours]
    keep_fx = ours[:30] + others[:max(0, 60 - min(30, len(ours)))]
    top = world.get("top") or []
    league_comp = None
    for t in keep_tables:
        if t.get("stage_compobjid") == league_stage:
            league_comp = t.get("competition_compobjid")
    keep_top = [t for t in top if t.get("compobjid") == league_comp][:1]
    for t in top:
        if len(keep_top) >= 3:
            break
        if t not in keep_top:
            keep_top.append(t)
    world2 = {k: v for k, v in world.items() if k not in ("tables", "fixtures", "top", "transfers",
                                                          "club_transfer_finance")}
    world2["tables"] = keep_tables
    world2["fixtures"] = keep_fx
    world2["top"] = keep_top
    world2["transfers"] = (world.get("transfers") or [])[:40]
    fin = world.get("club_transfer_finance") or []
    world2["club_transfer_finance"] = [c for c in fin if c.get("teamid") == user_team] + \
        [c for c in fin if c.get("teamid") != user_team][:20]
    world2 = strip_raw(world2)

    # --- status.json (synthesized from the snapshot header)
    status = {
        "schema": 1, "product": PRODUCT, "bridge": snap.get("bridge"), "le_version": snap.get("le_version"),
        "compat": "full", "memory_reads": True, "reason": "career_loaded",
        "supported_le_versions": [snap.get("le_version")], "mode": "manager",
        "data_dir": "C:\\Users\\test\\Documents\\FC26CareerMCP", "written": snap.get("written"),
    }

    # --- save_extract: schema 2 keys added, personal names scrubbed
    save = strip_raw(save)
    save["schema"] = 2
    save["product"] = PRODUCT
    save["career_type"] = "manager"
    save["user"] = {"userid": 0, "clubteamid": save.get("team_id"), "firstname": "Test", "surname": "Manager"}
    save["player"] = None
    save["player_history"] = []
    save["user_player"] = None
    save.setdefault("national_teams", [
        {"teamid": 1318, "team": "England", "gender": "men", "leagueid": 78,
         "roster": [204935, 243657, 202695]},
    ])
    save.setdefault("managers", [
        {"managerid": 1, "name": "Test Manager", "teamid": save.get("team_id"), "nationality": 42,
         "birthdate": 150000},
    ])
    for row in save.get("manager_history") or []:
        for k in ("firstname", "surname", "name"):
            if k in row:
                row[k] = "Test Manager" if k != "firstname" else "Test"

    hb = dict(hb)
    hb["career_ready"] = True
    hb.setdefault("why", None)
    hb.setdefault("date_error", None)

    d = os.path.join(out, "manager")
    dump(os.path.join(d, "status.json"), status)
    dump(os.path.join(d, "heartbeat.json"), hb)
    dump(os.path.join(d, "snapshot.json"), snap)
    dump(os.path.join(d, "world.json"), world2)
    dump(os.path.join(d, "save_extract.json"), save)
    return snap, world2


def player_fixture(out, manager_snap, manager_world):
    """Player career fixture: AS Roma (52), user player Test Player (299905), Serie A + Europa League."""
    written = "2026-11-06T20:10:00Z"
    game_date = "2027-11-28"
    roma = {"id": 52, "name": "AS Roma"}
    season_rows = [
        ("Matías Soulé", 265695, 20, 12, 12, 1, 0), ("Wesley", 80170, 20, 5, 2, 1, 0),
        ("Gianluca Mancini", 229582, 17, 4, 1, 2, 0), ("Paulo Dybala", 211110, 18, 4, 7, 1, 0),
        ("Kouadio Manu Koné", 250723, 16, 3, 0, 1, 0), ("Bryan Cristante", 208268, 17, 3, 1, 2, 0),
        ("Angeliño", 220651, 16, 2, 0, 0, 0), ("Robinio Vaz", 76410, 7, 1, 1, 0, 0),
        ("Rodrigo Mora", 72997, 9, 1, 2, 0, 0), ("Donyell Malen", 231447, 17, 1, 2, 0, 0),
        ("Test Player", 299905, 2, 1, 0, 0, 0), ("Mario Hermoso", 229668, 13, 1, 0, 1, 0),
        ("Evan Ndicka", 236403, 18, 0, 0, 1, 0), ("Mile Svilar", 230872, 17, 0, 0, 0, 0),
        ("Lorenzo Pellegrini", 228251, 0, 0, 0, 0, 0), ("Artem Dovbyk", 242458, 8, 0, 0, 0, 0),
        ("Konstantinos Koulierakis", 270077, 14, 0, 0, 1, 0), ("Devyne Rensch", 258826, 4, 0, 0, 1, 0),
    ]
    players = []
    for name, pid, app, goals, assists, yellow, red in season_rows:
        players.append({"playerid": pid, "name": name, "app": app, "goals": goals, "assists": assists,
                        "yellow": yellow, "red": red})
    table_rows = [
        (131682, "Inter", 1, 12, 11, 0, 1, 22, 9, 33), (1842, "Cagliari", 2, 13, 10, 1, 2, 24, 12, 31),
        (1745, "Como 1907", 3, 12, 8, 2, 2, 21, 10, 26), (52, "AS Roma", 4, 12, 7, 4, 1, 26, 13, 25),
        (115841, "Lazio", 5, 12, 6, 5, 1, 20, 12, 23), (45, "Juventus", 6, 12, 6, 3, 3, 21, 13, 21),
        (131681, "AC Milan", 7, 12, 5, 5, 2, 22, 15, 20), (189, "Bologna", 8, 12, 6, 2, 4, 18, 13, 20),
        (48, "SSC Napoli", 9, 12, 4, 6, 2, 18, 15, 18), (115845, "Atalanta", 10, 12, 5, 3, 4, 20, 18, 18),
        (110374, "Fiorentina", 11, 12, 5, 2, 5, 18, 14, 17), (111974, "Sassuolo", 12, 12, 4, 4, 4, 15, 16, 16),
        (55, "Udinese", 13, 12, 3, 4, 5, 17, 18, 13), (110556, "Genoa", 14, 12, 2, 4, 6, 10, 16, 10),
        (347, "Lecce", 15, 13, 1, 6, 6, 10, 18, 9), (54, "Torino", 16, 12, 2, 3, 7, 11, 21, 9),
        (1843, "Palermo", 17, 12, 2, 1, 9, 8, 26, 7), (111811, "Monza", 18, 12, 1, 3, 8, 10, 24, 6),
        (1837, "Sampdoria", 19, 12, 0, 4, 8, 9, 23, 4), (1746, "Pisa", 20, 12, 0, 3, 9, 6, 25, 3),
    ]
    rows = [{"teamid": tid, "team": t, "pos": pos, "p": p, "w": w, "d": d, "l": l, "gf": gf, "ga": ga,
             "gd": gf - ga, "pts": pts} for tid, t, pos, p, w, d, l, gf, ga, pts in table_rows]
    last_results = [
        {"date": "2027-11-25", "compobjid": 234, "competition": "UEFA Europa League", "home_id": 52,
         "home": "AS Roma", "away_id": 483, "away": "Villarreal CF", "score": "2-1"},
        {"date": "2027-11-20", "compobjid": 1121, "competition": "Serie A Enilive", "home_id": 347,
         "home": "Lecce", "away_id": 52, "away": "AS Roma", "score": "1-3"},
        {"date": "2027-11-07", "compobjid": 1121, "competition": "Serie A Enilive", "home_id": 52,
         "home": "AS Roma", "away_id": 1837, "away": "Sampdoria", "score": "2-1"},
        {"date": "2027-11-04", "compobjid": 234, "competition": "UEFA Europa League", "home_id": 234,
         "home": "SL Benfica", "away_id": 52, "away": "AS Roma", "score": "0-1"},
        {"date": "2027-10-31", "compobjid": 1121, "competition": "Serie A Enilive", "home_id": 111811,
         "home": "Monza", "away_id": 52, "away": "AS Roma", "score": "1-3"},
    ]
    next_fixtures = [
        {"date": "2027-11-29", "compobjid": 1121, "competition": "Serie A Enilive", "home_id": 52,
         "home": "AS Roma", "away_id": 45, "away": "Juventus"},
        {"date": "2027-12-03", "compobjid": 234, "competition": "UEFA Europa League", "home_id": 246,
         "home": "Feyenoord", "away_id": 52, "away": "AS Roma"},
        {"date": "2027-12-06", "compobjid": 1121, "competition": "Serie A Enilive", "home_id": 131681,
         "home": "AC Milan", "away_id": 52, "away": "AS Roma"},
    ]
    attrs = {"acceleration": 84, "sprintspeed": 86, "agility": 78, "balance": 72, "jumping": 70, "stamina": 74,
             "strength": 61, "reactions": 68, "aggression": 55, "composure": 66, "interceptions": 30,
             "positioning": 70, "vision": 62, "ballcontrol": 72, "crossing": 60, "dribbling": 74,
             "finishing": 71, "freekickaccuracy": 45, "headingaccuracy": 52, "longpassing": 55,
             "shortpassing": 66, "marking": 25, "shotpower": 70, "longshots": 62, "standingtackle": 28,
             "slidingtackle": 24, "volleys": 58, "curve": 60, "penalties": 60, "gkdiving": 8,
             "gkhandling": 10, "gkkicking": 12, "gkpositioning": 9, "gkreflexes": 11, "defensiveawareness": 26,
             "skillmoves": 3, "weakfoot": 3, "foot": "right", "height": 178, "weight": 71}
    user_player = {
        "playerid": 299905, "name": "Test Player", "team_id": 52, "team": "AS Roma", "position": "ST", "age": 19,
        "overall": 68, "potential": 84, "wage": 9000, "season_count": 1, "attributes": attrs,
        "recent_ratings": [{"date": "2027-11-20", "mins": 23, "rating_int_5_10": 7},
                           {"date": "2027-11-07", "mins": 12, "rating_int_5_10": 6}],
        "avg_rating_recent": 6.5, "rating_unit": "int_5_10",
        "season": {"app": 2, "goals": 1, "assists": 0, "yellow": 0, "red": 0,
                   "by_competition": [{"compobjid": 1121, "competition": "Serie A Enilive", "app": 2,
                                       "goals": 1, "assists": 0}]},
        "career": {"playerrequest": 0, "numtransferrequests": 0, "numloanrequests": 1,
                   "numwithdrawrequests": 0, "requestactiondays": 0, "lastwithdrawdate": 20080101,
                   "playedlastmatch": 1, "position": 25},
        "history": [{"season": 0, "teamid": 52, "appearances": 2, "goals": 1, "assists": 0, "motm": 0,
                     "matchratings": 13, "cleansheets": 0, "wins": 1, "draws": 0, "loses": 1, "wage": 9000,
                     "overall": 68, "value": 2400000, "tableposition": 4, "leaguetrophies": 0,
                     "domesticcuptrophies": 0, "continentalcuptrophies": 0, "clublevel": 4}],
        "last_match": {"mins": 23, "pos": "ST", "ovr": 68},
        "squad_role": "not_available",
    }
    snap = {
        "schema": 4, "product": PRODUCT, "bridge": "ro14", "le_version": "v26.3.5", "compat": "full",
        "memory_reads": True, "compat_reason": None, "reason": "day_passed", "written": written,
        "game_date": game_date, "date_error": None, "game_date_source": "live GetCurrentDate",
        "mode": "player",
        "user": {"name": "Test Player", "firstname": "Test", "seasons": 1, "club_teamid": 52, "usertype": 1,
                 "playertype": 0},
        "user_team": roma, "transfer_budget": None, "wage_budget": None,
        "wage_budget_note": "player career: club budgets are not shown to the player",
        "last_results": last_results, "next_fixtures": next_fixtures, "next_fixture": next_fixtures[0],
        "league_table": {"competition": "Serie A Enilive", "compobjid": 1123, "rows": rows,
                         "team_competitions": [{"compobjid": 1121, "competition": "Serie A Enilive", "teams": 20,
                                                "games": 38, "team_played": 12},
                                               {"compobjid": 234, "competition": "UEFA Europa League",
                                                "teams": 36, "games": 8, "team_played": 5}]},
        "squad": {"count": len(players), "total_wages": None, "players": players, "excluded": [],
                  "notes": {"stats": "player career: squad = GetPlayersStats rows of the club (season stats "
                                     "only; contracts/ratings not available)"},
                  "contracts_read": False, "ratings_read": False},
        "events": [{"date": "2027-11-27", "name": "WEEK_PASSED", "count": 1},
                   {"date": "2027-11-26", "name": "PLAYER_UNHAPPY_WITH_WAGE", "count": 4}],
        "events_note": "events[] are GLOBAL counts of event names (never attributed)",
        "event_payloads": [{"date": 20271126, "id": 20, "name": "SUSPENSION", "scope": "global",
                            "fields": {"kind": "suspension", "playerid": 229582, "teamid": 52, "matches": 1},
                            "matches": []}],
        "raw_events": [],
        "user_player": user_player,
        "last_job": {"name": "user_player", "ok": True, "game_date": game_date, "wall": written},
        "pending_reads": [],
    }
    hb = {"bridge": "ro14", "wall": written, "unix": 1794082200, "reason": "event", "career_ready": True,
          "why": None, "mode": "player", "game_date": game_date, "date_error": None}
    status = {"schema": 1, "product": PRODUCT, "bridge": "ro14", "le_version": "v26.3.5", "compat": "full",
              "memory_reads": True, "reason": "career_loaded", "supported_le_versions": ["v26.3.5"],
              "mode": "player", "data_dir": "C:\\Users\\test\\Documents\\FC26CareerMCP", "written": written}
    # world: Serie A table (stage 1123 -> competition 1121), Europa league table, our fixtures, transfers
    fixtures = []
    for r in last_results:
        h, a = r["score"].split("-")
        fx = dict(r)
        fx.update({"played": True, "home_score": int(h), "away_score": int(a), "season": "2027/28",
                   "stage_compobjid": 1123 if r["compobjid"] == 1121 else 258, "group": 12})
        fixtures.append(fx)
    for r in next_fixtures:
        fx = dict(r)
        fx.update({"played": False, "season": "2027/28",
                   "stage_compobjid": 1123 if r["compobjid"] == 1121 else 258, "group": 13})
        fixtures.append(fx)
    fixtures.append({"date": "2027-11-28", "compobjid": 1121, "stage_compobjid": 1123, "competition": "Serie A Enilive",
                     "home_id": 131682, "home": "Inter", "away_id": 48, "away": "SSC Napoli", "score": "2-2",
                     "home_score": 2, "away_score": 2, "played": True, "season": "2027/28", "group": 13})
    el_rows = [{"teamid": 246, "team": "Feyenoord", "pos": 1, "p": 5, "w": 4, "d": 1, "l": 0, "gf": 11, "ga": 3,
                "gd": 8, "pts": 13},
               {"teamid": 52, "team": "AS Roma", "pos": 2, "p": 5, "w": 4, "d": 0, "l": 1, "gf": 9, "ga": 4,
                "gd": 5, "pts": 12},
               {"teamid": 483, "team": "Villarreal CF", "pos": 3, "p": 5, "w": 3, "d": 1, "l": 1, "gf": 8,
                "ga": 5, "gd": 3, "pts": 10}]
    world = {
        "schema": "fc26-world-1", "bridge": "ro14", "game_date": game_date, "season": "2027/28", "user_team": 52,
        "reason": "day_passed",
        "tables": [{"compobjid": 1123, "stage_compobjid": 1123, "competition_compobjid": 1121,
                    "competition": "Serie A Enilive", "stage_name": "Serie A Enilive", "kind": "league",
                    "season": "2027/28", "teams": 20, "games_played": 240, "rows": rows},
                   {"compobjid": 258, "stage_compobjid": 258, "competition_compobjid": 234,
                    "competition": "UEFA Europa League", "stage_name": "League phase", "kind": "league",
                    "season": "2027/28", "teams": 36, "games_played": 90, "rows": el_rows}],
        "top": [{"compobjid": 1121, "competition": "Serie A Enilive", "season": "2027/28", "players": 540,
                 "goals": [{"rank": 1, "playerid": 265695, "player": "Matías Soulé", "teamid": 52, "team": "AS Roma",
                            "value": 12, "goals": 12, "assists": 12, "app": 20, "clean_sheets": 0},
                           {"rank": 2, "playerid": 231747, "player": "Lautaro Martínez", "teamid": 131682,
                            "team": "Inter", "value": 11, "goals": 11, "assists": 2, "app": 12, "clean_sheets": 0}],
                 "assists": [{"rank": 1, "playerid": 265695, "player": "Matías Soulé", "teamid": 52,
                              "team": "AS Roma", "value": 12, "goals": 12, "assists": 12, "app": 20,
                              "clean_sheets": 0}],
                 "clean_sheets": [{"rank": 1, "playerid": 230872, "player": "Mile Svilar", "teamid": 52,
                                   "team": "AS Roma", "value": 7, "goals": 0, "assists": 0, "app": 17,
                                   "clean_sheets": 7}],
                 "avg_rating": [{"rank": 1, "playerid": 265695, "player": "Matías Soulé", "teamid": 52,
                                 "team": "AS Roma", "value": 7.9, "goals": 12, "assists": 12, "app": 20,
                                 "clean_sheets": 0}]}],
        "fixtures": fixtures,
        "transfers": [{"playerid": 72997, "player": "Rodrigo Mora", "from_id": 236, "from": "FC Porto",
                       "to_id": 52, "to": "AS Roma", "fee": 28000000, "date": "2027-07-04", "type": "transfer",
                       "season": "2027/28"},
                      {"playerid": 231747, "player": "Lautaro Martínez", "from_id": 131682, "from": "Inter",
                       "to_id": 243, "to": "Real Madrid", "fee": 95000000, "date": "2027-08-20",
                       "type": "transfer", "season": "2027/28"}],
        "club_transfer_finance": [{"teamid": 52, "team": "AS Roma", "season": "2027/28", "income": 0,
                                   "expense": 28000000, "bought": 1, "sold": 0, "loans_in": 0, "loans_out": 0,
                                   "fee_unknown": 0}],
        "fixtures_played_total": 6, "fixtures_upcoming_total": 3,
    }
    d = os.path.join(out, "player")
    dump(os.path.join(d, "status.json"), status)
    dump(os.path.join(d, "heartbeat.json"), hb)
    dump(os.path.join(d, "snapshot.json"), snap)
    dump(os.path.join(d, "world.json"), world)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=os.path.join(ROOT, "tests", "fixtures"))
    a = ap.parse_args()
    snap, world = (None, None)
    if os.path.isfile(os.path.join(a.src, "snapshot.json")):
        snap, world = manager_fixture(a.src, a.out)
        print("manager fixture written from", a.src)
    else:
        print("no source bridge files in %s: manager fixture not rebuilt" % a.src, file=sys.stderr)
    player_fixture(a.out, snap, world)
    print("player fixture written")


if __name__ == "__main__":
    main()
