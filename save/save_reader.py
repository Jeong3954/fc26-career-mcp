"""[GLOUNDEV] FC26 Career MCP - save reader (read-only, offline).

Reads a COPY of the newest career save under %LOCALAPPDATA%\\EA SPORTS FC 26\\settings
with the vendored fc26-mcp SquadFile parser (MIT, save/vendor/fc26_mcp) and writes
<data_dir>\\save_extract.json (schema 2) with the tables the live bridge cannot read safely:
suspensions, grudge/love clubs, previous clubs, youth-academy graduates, captain and set-piece
takers, season-start overall, squad ranking, pre-signed contracts, loans, board info and past
seasons, persistent news events, national-team rosters, the manager table, and the user's
identity (career_users / career_playasplayer) so the server can match it with the live snapshot.

Usage:
    python save_reader.py [--data-dir DIR] [--saves-dir DIR] [--force] [--quiet] [--json]

Prints exactly one JSON line on stdout (suppressed by --quiet unless --json is also given):
    {"ok": true, "unchanged": true}
    {"ok": true, "save": "...", "career_type": "manager"|"player", "team": <id>, "squad": <n>}
    {"ok": false, "error": "no_save"|"save_busy"|"parse_failed"|"write_failed", "detail": "..."}
Exit code is 0 in every handled case; only an unexpected crash exits non-zero.

Never writes the save, never touches the game. Python 3.9+, stdlib only (plus the vendored parser).
"""
import argparse
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time

# The vendored parser lives next to this script (save/vendor/fc26_mcp); never a pip package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from fc26_mcp import fifa_squad  # noqa: E402

PRODUCT = "fc26-career-mcp"
SCHEMA = 2
META = os.path.join(_VENDOR, "fc26_mcp", "data", "fifa_ng_db-meta-fc26.xml")
OUT_NAME = "save_extract.json"
MAX_EVENTS = 25
MAX_ROSTER = 40
MAX_MANAGERS = 1200
NATIONAL_LEAGUES = {78: "men", 2136: "women"}
# Career save names: "Cm" + type letters + "C" + anything (CmMgrC..., CmPlyC...).
SAVE_NAME_RE = re.compile(r"^Cm[A-Za-z]{1,8}C")
STABLE_DELAY = 0.2
STABLE_TRIES = 5

USER_FIELDS = ("userid", "clubteamid", "firstname", "surname", "playertype", "usertype",
               "nationalteamid", "primarycompobjid", "leagueid")
PLAYER_FIELDS = ("playerid", "position", "playerrequest", "numtransferrequests", "numloanrequests",
                 "numwithdrawrequests", "requestactiondays", "lastwithdrawdate", "playedlastmatch")
PLAYER_HISTORY_FIELDS = ("season", "teamid", "appearances", "goals", "assists", "motm", "matchratings",
                         "cleansheets", "wins", "draws", "loses", "wage", "overall", "value", "tableposition",
                         "leaguetrophies", "domesticcuptrophies", "continentalcuptrophies", "clublevel")
TEAM_ROLE_FIELDS = ("captainid", "penaltytakerid", "freekicktakerid", "longkicktakerid",
                    "leftcornerkicktakerid", "rightcornerkicktakerid")


# ----------------------------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------------------------

def default_saves_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "EA SPORTS FC 26", "settings")


def default_data_dir():
    """Documents\\FC26CareerMCP, redirected by its config.json {"data_dir": ...} when present."""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    base = os.path.join(home, "Documents", "FC26CareerMCP")
    cfg = os.path.join(base, "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            redirect = json.load(f).get("data_dir")
        if isinstance(redirect, str) and redirect.strip():
            return redirect
    except Exception:
        pass
    return base


# ----------------------------------------------------------------------------------------------
# Parsing (file -> tables)
# ----------------------------------------------------------------------------------------------

class DBAt(fifa_squad.SquadFile):
    """SquadFile positioned on the n-th T3DB inside a save (career saves hold two)."""

    def __init__(self, raw, offset, meta):
        self.path = None
        self.meta_path = meta
        (self.table_names, self.field_names, self.field_range,
         self.field_depth, self.field_type) = fifa_squad.load_meta(meta)
        self.raw = raw
        self.db_offset = offset
        self.db_size = struct.unpack_from("<I", raw, offset + 8)[0]
        self.db_data = raw[offset:offset + self.db_size]
        self.tables_meta = self._read_table_index()
        self._records, self._table_fields, self._dirty = {}, {}, set()


class SaveTables:
    """All T3DB databases of one save file; `table(name)` returns the rows of the first DB holding it."""

    def __init__(self, path, meta=META):
        with open(path, "rb") as f:
            raw = bytearray(f.read())
        self.dbs = []
        pos = 0
        while True:
            off = raw.find(fifa_squad.DB_HEADER, pos)
            if off < 0:
                break
            try:
                self.dbs.append(DBAt(raw, off, meta))
            except Exception:
                pass
            pos = off + 8
        if not self.dbs:
            raise ValueError("no T3DB database found in save")
        self._cache = {}

    def table(self, name):
        if name in self._cache:
            return self._cache[name]
        rows = []
        for db in self.dbs:
            if any(t["name"] == name for t in db.tables_meta):
                try:
                    rows = [r for r in db.get_table(name) if isinstance(r, dict)]
                except Exception:
                    rows = []
                break
        self._cache[name] = rows
        return rows


class DictTables:
    """Table source backed by a plain dict {table_name: [rows]} (tests, fixtures)."""

    def __init__(self, tables):
        self.tables = tables or {}

    def table(self, name):
        return list(self.tables.get(name) or [])


# ----------------------------------------------------------------------------------------------
# Extraction (tables -> save_extract dict)
# ----------------------------------------------------------------------------------------------

def _pick(row, fields):
    return {k: row.get(k) for k in fields}


def _first(rows):
    return rows[0] if rows else {}


def detect_career_type(dbs):
    """"player" when career_playasplayer has a row with a real player id, else "manager"."""
    for r in dbs.table("career_playasplayer"):
        try:
            if int(r.get("playerid") or 0) > 0:
                return "player"
        except (TypeError, ValueError):
            continue
    return "manager"


def extract(dbs, save_file=None, save_mtime=None, now=None):
    """Build the save_extract dict from any object exposing table(name) -> list[dict]."""
    now = now if now is not None else time.time()
    career_type = detect_career_type(dbs)
    users = _first(dbs.table("career_users"))
    info = _first(dbs.table("career_managerinfo")) if career_type == "manager" else {}
    # In a player career the "club" is the user's club (career_users.clubteamid), same squad rules.
    team = users.get("clubteamid") if career_type == "player" else (info.get("clubteamid") or users.get("clubteamid"))

    # Squad rule: players linked to the club (teamplayerlinks) + players loaned out by it (flagged).
    # career_playercontract keeps stale rows of sold players: excluded.
    links = {r.get("playerid") for r in dbs.table("teamplayerlinks") if r.get("teamid") == team}
    contracts = {r.get("playerid") for r in dbs.table("career_playercontract") if r.get("teamid") == team}
    all_loans = dbs.table("playerloans")
    loaned_out = {r.get("playerid") for r in all_loans if r.get("teamidloanedfrom") == team}
    squad = links | (loaned_out & contracts)
    squad.discard(None)

    def in_squad(r):
        return r.get("playerid") in squad

    loans = [r for r in all_loans if in_squad(r) or r.get("teamidloanedfrom") == team]
    presigned = [r for r in dbs.table("career_presignedcontract")
                 if team in (r.get("teamid"), r.get("offerteamid")) or in_squad(r)]
    events = [r for r in dbs.table("persistent_events")
              if r.get("player1id") in squad or team in (r.get("team1id"), r.get("team2id"))]
    events.sort(key=lambda r: r.get("eventdate") or 0)
    teams = dbs.table("teams")
    team_row = next((t for t in teams if t.get("teamid") == team), {})

    res = {
        "schema": SCHEMA, "source": "save", "product": PRODUCT,
        "save_file": save_file,
        "as_of_save": (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(save_mtime))
                       if save_mtime is not None else None),
        "save_mtime": save_mtime,
        "written": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "career_type": career_type,
        "user": _pick(users, USER_FIELDS) if users else None,
        "team_id": team, "squad_ids": sorted(squad), "loaned_out_ids": sorted(loaned_out & squad),
        "excluded_contract_rows": sorted(x for x in (contracts - squad) if x is not None),
        "squad_rule": "teamplayerlinks.teamid = club + playerloans.teamidloanedfrom = club; sold players excluded",
        "game_date_note": "the save has no reliable game date; any save-based date is an estimate: "
                          "use the live bridge game_date",
        "names_note": "team_names/player_names are save-local (may be localized); real player names "
                      "come from the live snapshot name maps",
        "suspensions": [r for r in dbs.table("playersuspensions") if r.get("teamid") == team or in_squad(r)],
        "grudgelove": [r for r in dbs.table("player_grudgelove") if in_squad(r)],
        "previous_teams": [r for r in dbs.table("previousteam") if in_squad(r)],
        "season_start": [{"playerid": r.get("playerid"), "overall": r.get("overall")}
                         for r in dbs.table("career_playergrowthuserseason") if in_squad(r)],
        "squad_ranking": [r for r in dbs.table("career_squadranking") if in_squad(r)],
        # one row per player promoted from the club's youth academy
        "youth_graduates": sorted(r.get("playerid") for r in dbs.table("career_youthplayerhistory") if in_squad(r)),
        # captain and set-piece takers of the user's club (teams row); missing/0 means unknown
        "team_roles": {k: v for k, v in team_row.items() if k in TEAM_ROLE_FIELDS and v},
        "presigned": presigned[:20], "loans": loans[:30],
        "manager_info": {k: v for k, v in info.items() if k != "artificialkey"},
        "manager_history": dbs.table("career_managerhistory")[-6:],
        "news_events": events[-MAX_EVENTS:],
    }

    # Player career: the user's player row and its season history.
    if career_type == "player":
        prow = _first(dbs.table("career_playasplayer"))
        res["player"] = _pick(prow, PLAYER_FIELDS)
        res["player_history"] = [_pick(r, PLAYER_HISTORY_FIELDS) for r in dbs.table("career_playasplayerhistory")]
    else:
        res["player"] = None
        res["player_history"] = []
    res["user_player"] = res["player"]  # alias used by DESIGN.md

    # Schema 2: national teams (league 78 men / 2136 women) with rosters from teamplayerlinks.
    team_name = {t.get("teamid"): t.get("teamname") for t in teams}
    rosters = {}
    for r in dbs.table("teamplayerlinks"):
        rosters.setdefault(r.get("teamid"), []).append(r.get("playerid"))
    national = []
    seen = set()
    for r in dbs.table("leagueteamlinks"):
        gender = NATIONAL_LEAGUES.get(r.get("leagueid"))
        tid = r.get("teamid")
        if gender is None or tid in seen or tid is None:
            continue
        seen.add(tid)
        roster = [p for p in rosters.get(tid, []) if p is not None]
        national.append({"teamid": tid, "name": team_name.get(tid), "gender": gender,
                         "roster": roster[:MAX_ROSTER], "roster_count": len(roster)})
    res["national_teams"] = national

    # Schema 2: manager table.
    managers = []
    for r in dbs.table("manager")[:MAX_MANAGERS]:
        name = r.get("commonname") or " ".join(x for x in (r.get("firstname"), r.get("surname")) if x)
        managers.append({"managerid": r.get("managerid"), "name": name or None, "teamid": r.get("teamid"),
                         "nationality": r.get("nationality"), "birthdate": r.get("birthdate"),
                         "starrating": r.get("starrating")})
    res["managers"] = managers

    # Save-local names for the clubs and non-squad players referenced above.
    team_ids = {team}
    for r in res["grudgelove"]:
        team_ids.add(r.get("emotional_teamid"))
    for r in res["previous_teams"]:
        team_ids.add(r.get("previousteamid"))
    for r in loans:
        team_ids.update((r.get("teamidloanedfrom"), r.get("teamidloanedto")))
    for r in presigned:
        team_ids.update((r.get("teamid"), r.get("offerteamid")))
    for r in res["news_events"]:
        team_ids.update((r.get("team1id"), r.get("team2id")))
    team_ids.discard(None)
    res["team_names"] = {str(t.get("teamid")): t.get("teamname") for t in teams if t.get("teamid") in team_ids}
    pids = {r.get("playerid") for r in loans + presigned} | {r.get("player1id") for r in res["news_events"]}
    pids.discard(None)
    names = {n.get("nameid"): n.get("name") for n in dbs.table("dcplayernames")}
    names.update({n.get("nameid"): n.get("name") for n in dbs.table("playernames")})
    pn = {}
    for p in dbs.table("players"):
        if p.get("playerid") in pids:
            common = names.get(p.get("commonnameid"))
            pn[str(p.get("playerid"))] = common or " ".join(
                x for x in (names.get(p.get("firstnameid")), names.get(p.get("lastnameid"))) if x)
    res["player_names"] = pn
    return res


# ----------------------------------------------------------------------------------------------
# File discovery and the CLI
# ----------------------------------------------------------------------------------------------

def newest_save(saves_dir):
    """Newest career save (Cm<type>C*) in saves_dir, or None."""
    try:
        entries = os.listdir(saves_dir)
    except OSError:
        return None
    files = [os.path.join(saves_dir, n) for n in entries
             if SAVE_NAME_RE.match(n) and os.path.isfile(os.path.join(saves_dir, n))]
    return max(files, key=os.path.getmtime) if files else None


def wait_size_stable(path, delay=STABLE_DELAY, tries=STABLE_TRIES, sleep=time.sleep):
    """True when two size stats `delay` apart agree (the game is not mid-write)."""
    for _ in range(tries):
        a = os.path.getsize(path)
        sleep(delay)
        b = os.path.getsize(path)
        if a == b:
            return True
    return False


def parse_save(path):
    """Copy the save to a temp file, parse the copy, delete it."""
    fd, tmp = tempfile.mkstemp(prefix="fc26cm_save_", suffix=".bin")
    os.close(fd)
    try:
        shutil.copyfile(path, tmp)
        return SaveTables(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def write_atomic(out_path, data):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, out_path)


def run(data_dir, saves_dir, force=False):
    """The whole job; returns the status dict that the CLI prints."""
    save = newest_save(saves_dir)
    if not save:
        return {"ok": False, "error": "no_save", "detail": "no Cm*C* career save in %s" % saves_dir}
    out_path = os.path.join(data_dir, OUT_NAME)
    save_name = os.path.basename(save)
    mtime = os.path.getmtime(save)
    if not force:
        try:
            with open(out_path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = {}
        if (old.get("schema") == SCHEMA and old.get("save_file") == save_name
                and old.get("save_mtime") == mtime):
            return {"ok": True, "unchanged": True}
    try:
        if not wait_size_stable(save):
            return {"ok": False, "error": "save_busy", "detail": "save size keeps changing: %s" % save_name}
        mtime = os.path.getmtime(save)
        dbs = parse_save(save)
        res = extract(dbs, save_file=save_name, save_mtime=mtime)
    except Exception as e:  # corrupt/unknown save layout: report, never crash
        return {"ok": False, "error": "parse_failed", "detail": "%s: %s" % (type(e).__name__, e)}
    try:
        write_atomic(out_path, res)
    except Exception as e:
        return {"ok": False, "error": "write_failed", "detail": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "save": save_name, "career_type": res["career_type"], "team": res["team_id"],
            "squad": len(res["squad_ids"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="FC26 Career MCP save reader (read-only)")
    ap.add_argument("--data-dir", default=None, help="output folder (default Documents\\FC26CareerMCP, config.json redirect honoured)")
    ap.add_argument("--saves-dir", default=None, help="FC 26 settings folder holding the Cm* saves")
    ap.add_argument("--force", action="store_true", help="re-extract even when the save is unchanged")
    ap.add_argument("--quiet", action="store_true", help="print nothing (unless --json)")
    ap.add_argument("--json", action="store_true", help="always print the JSON status line")
    args = ap.parse_args(argv)
    data_dir = args.data_dir or default_data_dir()
    saves_dir = args.saves_dir or default_saves_dir()
    try:
        status = run(data_dir, saves_dir, force=args.force)
    except Exception as e:  # last resort: still one JSON line, exit 0
        status = {"ok": False, "error": "crash", "detail": "%s: %s" % (type(e).__name__, e)}
    if args.json or not args.quiet:
        sys.stdout.write(json.dumps(status, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return status


if __name__ == "__main__":
    main()
