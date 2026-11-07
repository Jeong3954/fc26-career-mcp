"""[GLOUNDEV] FC26 Career MCP - tool logic shared by every transport.

Read-only. Opens the JSON files the Live Editor bridge writes (status.json, heartbeat.json,
snapshot.json, world.json, event_payloads.jsonl) and the save extract (save_extract.json,
refreshed through save/save_reader.py when the newest save changed). Never talks to the game.

Every tool answer is a dict: {"_meta": {...}, ...data}. `_meta` carries source (live / save /
live+save), scope (user_club / user_player / global / mixed), mode, live_game_date,
snapshot_written, snapshot_age_sec, heartbeat_age_sec, save_as_of (when the save was used),
compat and warnings[].

Python 3.9+, standard library only.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT_ROOT = os.path.dirname(HERE)
SAVE_DIR = os.path.join(PRODUCT_ROOT, "save")
if SAVE_DIR not in sys.path:
    sys.path.insert(0, SAVE_DIR)

PRODUCT = "FC26 Career MCP"
SERVER_NAME = "fc26-career-mcp"
VERSION = "1.0.1"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
HEARTBEAT_ALIVE_SEC = 900
DEFAULT_LIMIT = 10
MAX_LIMIT = 60

NEGOTIATION_CODES = {6: "offer made (buyer)", 2: "counter: asking terms (seller)", 3: "on hold (seller)",
                     0: "accepted (seller)", 4: "accepted (buyer)", 1: "rejected", 5: "expired / ended by buyer"}
SAVE_CATEGORIES = ("summary", "national_teams", "managers", "board_history", "suspensions", "loans", "presigned",
                   "youth", "grudge_love", "previous_clubs", "team_roles", "season_start", "squad_ranking",
                   "news_events")


# ----------------------------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------------------------

def default_data_root():
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "Documents", "FC26CareerMCP")


def resolve_data_dir(explicit=None):
    """Documents\\FC26CareerMCP, redirected by its config.json {"data_dir": ...}."""
    if explicit:
        return os.path.abspath(explicit)
    base = default_data_root()
    try:
        with open(os.path.join(base, "config.json"), encoding="utf-8") as f:
            redirect = json.load(f).get("data_dir")
        if isinstance(redirect, str) and redirect.strip():
            return redirect
    except Exception:
        pass
    return base


def default_saves_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "EA SPORTS FC 26", "settings")


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------

def ymd(v):
    """20270102 -> '2027-01-02' (other values unchanged)."""
    if isinstance(v, int) and 19000101 < v < 21000101:
        return "%04d-%02d-%02d" % (v // 10000, v // 100 % 100, v % 100)
    return v


def fifa_date(days):
    """FIFA day count since 1582-10-14 -> ISO date (None when not a number)."""
    try:
        return time.strftime("%Y-%m-%d", time.gmtime((int(days) - 141428) * 86400))
    except Exception:
        return None


def text_match(value, needle):
    return needle is None or (value is not None and str(needle).lower() in str(value).lower())


def strip_raw(row):
    """Drop undecoded negotiation words (raw*, keys)."""
    if not isinstance(row, dict):
        return row
    return {k: v for k, v in row.items() if not str(k).startswith("raw")}


def clamp_limit(value, default=DEFAULT_LIMIT, cap=MAX_LIMIT):
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


class ToolError(Exception):
    def __init__(self, code, hint=None, **extra):
        Exception.__init__(self, code)
        self.code = code
        self.hint = hint
        self.extra = extra

    def as_dict(self):
        d = {"error": self.code}
        if self.hint:
            d["hint"] = self.hint
        d.update(self.extra)
        return d


# ----------------------------------------------------------------------------------------------
# Store: the files
# ----------------------------------------------------------------------------------------------

class Store:
    """Loads the bridge files once per call set (fresh object per tool call is fine: files are small)."""

    def __init__(self, data_dir=None, saves_dir=None, now=None, save_refresh=True):
        self.data_dir = resolve_data_dir(data_dir)
        self.saves_dir = saves_dir or default_saves_dir()
        self.now = now if now is not None else time.time()
        self.save_refresh = save_refresh
        self._cache = {}
        self._world = None
        self._save = None
        self._save_status = None

    # -- raw files --
    def path(self, name):
        return os.path.join(self.data_dir, name)

    def exists(self, name):
        return os.path.isfile(self.path(name))

    def age(self, name):
        p = self.path(name)
        try:
            return int(self.now - os.path.getmtime(p))
        except OSError:
            return None

    def load(self, name):
        if name in self._cache:
            return self._cache[name]
        try:
            with open(self.path(name), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
        self._cache[name] = data
        return data

    # -- typed accessors --
    @property
    def status(self):
        return self.load("status.json") or {}

    @property
    def heartbeat(self):
        return self.load("heartbeat.json") or {}

    @property
    def snapshot(self):
        return self.load("snapshot.json") or {}

    @property
    def world(self):
        return self.load("world.json") or {}

    @property
    def mode(self):
        return self.snapshot.get("mode") or self.heartbeat.get("mode") or self.status.get("mode") or "unknown"

    @property
    def my_club(self):
        return (self.snapshot.get("user_team") or {}).get("id")

    @property
    def my_club_name(self):
        return (self.snapshot.get("user_team") or {}).get("name")

    @property
    def user_player(self):
        return self.snapshot.get("user_player") or {}

    @property
    def my_player(self):
        return self.user_player.get("playerid") if self.mode == "player" else None

    @property
    def squad(self):
        return (self.snapshot.get("squad") or {}).get("players") or []

    @property
    def squad_ids(self):
        ids = {p.get("playerid") for p in self.squad}
        if self.my_player:
            ids.add(self.my_player)
        ids.discard(None)
        return ids

    @property
    def heartbeat_age(self):
        hb = self.heartbeat
        if hb.get("unix"):
            try:
                return int(self.now - float(hb["unix"]))
            except (TypeError, ValueError):
                pass
        return self.age("heartbeat.json")

    @property
    def bridge_alive(self):
        a = self.heartbeat_age
        return a is not None and a < HEARTBEAT_ALIVE_SEC

    @property
    def compat(self):
        return self.snapshot.get("compat") or self.status.get("compat") or "unknown"

    # -- save extract --
    def save_extract(self, refresh=None):
        """save_extract.json, re-extracted through save_reader when the newest save changed."""
        if self._save is not None:
            return self._save, self._save_status
        status = None
        do_refresh = self.save_refresh if refresh is None else refresh
        if do_refresh:
            try:
                import save_reader  # noqa: WPS433 (product module next to the server)
                status = save_reader.run(self.data_dir, self.saves_dir)
            except Exception as e:  # the extract on disk is still usable
                status = {"ok": False, "error": "reader_failed", "detail": "%s: %s" % (type(e).__name__, e)}
            self._cache.pop("save_extract.json", None)
        self._save = self.load("save_extract.json") or {}
        self._save_status = status
        return self._save, status

    # -- names --
    def name_maps(self):
        """player id -> name and team id -> name from the live files (+ save maps as fallback)."""
        players, teams = {}, {}
        snap = self.snapshot
        for p in self.squad:
            players[p.get("playerid")] = p.get("name")
        for key in ("targets", "transfers", "offers", "offers_history", "bids", "player_talks"):
            for r in snap.get(key) or []:
                if isinstance(r, dict) and r.get("playerid") is not None:
                    players.setdefault(r["playerid"], r.get("name") or r.get("player"))
        if self.user_player.get("playerid"):
            players[self.user_player["playerid"]] = self.user_player.get("name")
        if self.my_club is not None:
            teams[self.my_club] = self.my_club_name
        w = self.world
        for tr in w.get("transfers") or []:
            players.setdefault(tr.get("playerid"), tr.get("player"))
            for a, b in (("from_id", "from"), ("to_id", "to")):
                if tr.get(a) is not None:
                    teams.setdefault(tr[a], tr.get(b))
        for c in w.get("top") or []:
            for v in c.values():
                if isinstance(v, list):
                    for r in v:
                        if isinstance(r, dict):
                            players.setdefault(r.get("playerid"), r.get("player"))
                            teams.setdefault(r.get("teamid"), r.get("team"))
        for t in w.get("tables") or []:
            for r in t.get("rows") or []:
                teams.setdefault(r.get("teamid"), r.get("team"))
        for f in list(w.get("fixtures") or []) + list(snap.get("last_results") or []) + list(snap.get("next_fixtures") or []):
            teams.setdefault(f.get("home_id"), f.get("home"))
            teams.setdefault(f.get("away_id"), f.get("away"))
        for lt in [snap.get("league_table") or {}]:
            for r in lt.get("rows") or []:
                teams.setdefault(r.get("teamid"), r.get("team"))
        sd = self._save or {}
        for k, v in (sd.get("player_names") or {}).items():
            try:
                players.setdefault(int(k), v)
            except ValueError:
                pass
        for k, v in (sd.get("team_names") or {}).items():
            try:
                teams.setdefault(int(k), v)
            except ValueError:
                pass
        players.pop(None, None)
        teams.pop(None, None)
        return players, teams


# ----------------------------------------------------------------------------------------------
# Scope / attribution (A1, A5b, A6)
# ----------------------------------------------------------------------------------------------

def scope_team(store, *ids):
    return "user_club" if store.my_club is not None and store.my_club in ids else "global"


def scope_player(store, pid, *team_ids):
    if store.my_player and pid == store.my_player:
        return "user_player"
    if pid in store.squad_ids or (store.my_club is not None and store.my_club in team_ids):
        return "user_club"
    return "global"


def our_view(store, fx):
    """A6: scores are home-away in the fixture's order; outcome only from our side via ids."""
    fx = dict(fx)
    club = store.my_club
    side = "home" if fx.get("home_id") == club else "away" if fx.get("away_id") == club else None
    hs, as_ = fx.get("home_score"), fx.get("away_score")
    if (hs is None or as_ is None) and isinstance(fx.get("score"), str) and "-" in fx["score"]:
        try:
            hs, as_ = (int(x) for x in fx["score"].split("-")[:2])
        except ValueError:
            hs = as_ = None
    if hs is not None and as_ is not None:
        fx["home_score"], fx["away_score"] = hs, as_
    if side:
        fx["scope"] = "user_club"
        fx["user_side"] = side
        fx["opponent"] = fx.get("away") if side == "home" else fx.get("home")
        fx["opponent_id"] = fx.get("away_id") if side == "home" else fx.get("home_id")
        if hs is not None and as_ is not None:
            us, them = (hs, as_) if side == "home" else (as_, hs)
            fx["user_score"] = "%d-%d" % (us, them)
            fx["outcome"] = "W" if us > them else "L" if us < them else "D"
    else:
        fx["scope"] = "global"
    return fx


# ----------------------------------------------------------------------------------------------
# _meta
# ----------------------------------------------------------------------------------------------

def meta(store, source="live", scope="user_club", warnings=None, save_as_of=None, extra=None):
    snap = store.snapshot
    hb_age = store.heartbeat_age
    m = {
        "source": source,
        "scope": scope,
        "mode": store.mode,
        "live_game_date": snap.get("game_date") or store.heartbeat.get("game_date"),
        "snapshot_written": snap.get("written"),
        "snapshot_age_sec": store.age("snapshot.json"),
        "heartbeat_age_sec": hb_age,
        "bridge_alive": store.bridge_alive,
        "compat": store.compat,
        "warnings": list(warnings or []),
    }
    if save_as_of:
        m["save_as_of"] = save_as_of
    if source in ("live", "live+save"):
        if not snap:
            m["warnings"].append("no snapshot.json yet: the bridge has not completed a read in this career")
        if not store.bridge_alive:
            m["warnings"].append("bridge not beating (heartbeat older than %d s or absent): live values are as of "
                                 "live_game_date; the game may be further on" % HEARTBEAT_ALIVE_SEC)
        if snap.get("date_error"):
            m["warnings"].append("game date read failed: %s" % snap["date_error"])
    if store.compat == "partial":
        m["warnings"].append("compat partial: memory reads are off (no standings/fixtures/league table/transfers/"
                             "offers/world tables); squad, attributes, stats, names, events and save data still work")
    elif store.compat == "unsupported":
        m["warnings"].append("compat unsupported: this Live Editor / game version is not supported by the bridge")
    if extra:
        m.update(extra)
    return m


def require_snapshot(store):
    if not store.snapshot:
        raise ToolError("no_snapshot", "snapshot.json has not been written yet. Start FC 26 through Live Editor, "
                        "load an OFFLINE career, open the career hub and wait ~10 s; then call fc26_status.",
                        data_dir=store.data_dir)


def require_world(store):
    if not store.world:
        raise ToolError("no_world", "world.json has not been written yet (the bridge writes it once per game day "
                        "after the career hub); advance one day or re-open the hub.", data_dir=store.data_dir)


# ----------------------------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------------------------

def tool_status(store, args):
    snap, hb, st = store.snapshot, store.heartbeat, store.status
    files = {}
    for name in ("status.json", "heartbeat.json", "snapshot.json", "world.json", "event_payloads.jsonl",
                 "save_extract.json", "last_step.txt"):
        files[name] = {"exists": store.exists(name), "age_sec": store.age(name)}
    how = []
    if not st and not hb:
        how.append("nothing written yet: install the bridge, start FC 26 through Live Editor, load an OFFLINE "
                   "career, open the career hub, wait ~10 s")
    elif not snap:
        how.append("no snapshot yet: open the career hub (main career screen) and wait ~10 s; if nothing appears, "
                   "load bridge/fc26cm_bridge.lua in Live Editor -> Lua Engine and Execute (F5) on the hub")
    else:
        how.append("live values refresh on every game day and after each of your matches; to refresh now: advance "
                   "one day or return to the career hub")
    if not store.bridge_alive:
        how.append("heartbeat is old: the game or the bridge is not running; data is as of live_game_date")
    out = {
        "_meta": meta(store, "live", "mixed"),
        "product": PRODUCT, "server_version": VERSION, "data_dir": store.data_dir,
        "bridge": snap.get("bridge") or st.get("bridge") or hb.get("bridge"),
        "le_version": snap.get("le_version") or st.get("le_version"),
        "compat": store.compat, "compat_reason": snap.get("compat_reason") or st.get("reason"),
        "memory_reads": snap.get("memory_reads", st.get("memory_reads")),
        "supported_le_versions": st.get("supported_le_versions"),
        "mode": store.mode,
        "career_ready": hb.get("career_ready"), "heartbeat_reason": hb.get("reason"), "heartbeat_why": hb.get("why"),
        "game_date": snap.get("game_date") or hb.get("game_date"), "game_date_source": snap.get("game_date_source"),
        "user": snap.get("user"), "user_team": snap.get("user_team"),
        "pending_reads": snap.get("pending_reads"), "last_job": snap.get("last_job"),
        "files": files,
        "how_to_refresh": how,
        "rules": "Always say the in-game date values are as of; live = last bridge write, save = last save file.",
    }
    if store.my_player:
        out["user_player"] = {"playerid": store.my_player, "name": store.user_player.get("name")}
    return out


def _league_position(store):
    lt = store.snapshot.get("league_table") or {}
    rows = lt.get("rows") or []
    row = next((r for r in rows if r.get("teamid") == store.my_club), None)
    if not lt:
        return None
    around = []
    if row:
        i = rows.index(row)
        around = rows[max(0, i - 2): i + 3]
    return {"compobjid": lt.get("compobjid"), "competition": lt.get("competition"), "teams": len(rows),
            "user_row": row, "table_slice": around,
            "team_competitions": lt.get("team_competitions"),
            "rules": "A12/A14: one standings group = one compobjid; never merge groups by name"}


def tool_my_career(store, args):
    require_snapshot(store)
    snap = store.snapshot
    scope = "user_player" if store.mode == "player" else "user_club"
    out = {"_meta": meta(store, "live", scope),
           "mode": store.mode, "game_date": snap.get("game_date"),
           "user": snap.get("user"), "user_team": snap.get("user_team"),
           "transfer_budget": snap.get("transfer_budget"), "wage_budget": snap.get("wage_budget"),
           "wage_budget_note": snap.get("wage_budget_note"),
           "last_results": [our_view(store, f) for f in snap.get("last_results") or []],
           "next_fixtures": [our_view(store, f) for f in snap.get("next_fixtures") or []],
           "league_position": _league_position(store)}
    sq = snap.get("squad") or {}
    if store.mode == "player":
        up = dict(store.user_player)
        up["rating_unit"] = "rating_int_5_10"
        out["user_player"] = up
        out["club_context"] = {"squad_count": sq.get("count"), "total_wages_weekly": sq.get("total_wages")}
        out["transfer_requests"] = (up.get("career") or {})
        out["notes"] = ("recent_ratings are integer 5-10 match ratings; the screen shows one decimal. "
                        "history[] rows are per season (season 0 = first). squad_role is not readable.")
    else:
        injured = [{"playerid": p.get("playerid"), "name": p.get("name"), "days": (p.get("injury") or {}).get("days")}
                   for p in store.squad if (p.get("injury") or {}).get("days")]
        out["squad_summary"] = {"count": sq.get("count"), "total_wages_weekly": sq.get("total_wages"),
                                "injured": injured, "excluded_sold": sq.get("excluded")}
        offers = snap.get("offers") or []
        out["open_offers"] = len([o for o in offers if o.get("open", True)])
        out["open_bids"] = len([b for b in snap.get("bids") or [] if b.get("open")])
        out["offers_read_at"] = snap.get("offers_read_at")
        out["pending_reads"] = snap.get("pending_reads")
        out["board"] = snap.get("board")
    out["keys"] = "user_team.id is the user's club; fixtures carry home_id/away_id (A6); league row by teamid"
    return out


SQUAD_BRIEF = ("playerid", "name", "position", "age", "overall", "potential", "app", "goals", "assists", "yellow",
               "red", "wage", "contract_until", "contract_status_label", "intl_rep", "role")


def _save_extras_by_player(store):
    """Per-player save-only facts (suspensions, loans, presigned, youth graduate) keyed by playerid."""
    sd, _ = store.save_extract(refresh=False)
    if not sd:
        return {}, None
    if not _identity_ok(store, sd):
        return {}, None
    extras = {}

    def add(pid, key, val):
        if pid is None:
            return
        extras.setdefault(pid, {})[key] = val
    for r in sd.get("suspensions") or []:
        add(r.get("playerid"), "suspension", {k: v for k, v in r.items() if k != "playerid"})
    for r in sd.get("loans") or []:
        d = {k: v for k, v in r.items() if k != "playerid"}
        if d.get("loandateend"):
            d["loan_end"] = fifa_date(d["loandateend"])
        add(r.get("playerid"), "loan", d)
    for r in sd.get("presigned") or []:
        add(r.get("playerid"), "presigned", {k: v for k, v in r.items() if k != "playerid"})
    for pid in sd.get("youth_graduates") or []:
        add(pid, "youth_graduate", True)
    for r in sd.get("season_start") or []:
        add(r.get("playerid"), "overall_at_season_start", r.get("overall"))
    return extras, sd.get("as_of_save")


def tool_squad(store, args):
    require_snapshot(store)
    snap = store.snapshot
    rows = list(store.squad)
    warnings = []
    if store.mode == "player" and not rows:
        warnings.append("player career: the bridge snapshot holds no club squad rows in this build; "
                        "use fc26_my_career for the user's player")
    include_attr = bool(args.get("include_attributes"))
    position = args.get("position")
    if position:
        rows = [p for p in rows if str(p.get("position") or "").upper() == str(position).upper()]
    sort = args.get("sort") or "overall"
    rows.sort(key=lambda p: (p.get(sort) if isinstance(p.get(sort), (int, float)) else str(p.get(sort) or "")),
              reverse=sort not in ("name", "position", "contract_until", "age"))
    extras, save_as_of = _save_extras_by_player(store)
    players = []
    for p in rows:
        r = dict(p) if include_attr else {k: p.get(k) for k in SQUAD_BRIEF if p.get(k) is not None}
        if not include_attr:
            if (p.get("injury") or {}).get("days"):
                r["injury_days"] = p["injury"]["days"]
            fl = [k for k, v in (p.get("flags") or {}).items() if v]
            if fl:
                r["flags"] = fl
            rt = [x.get("rating") for x in p.get("ratings") or []]
            if rt:
                r["recent_ratings_int_5_10"] = rt
            if p.get("by_competition"):
                r["by_competition"] = p["by_competition"]
        ex = extras.get(p.get("playerid"))
        if ex:
            r["save_extras"] = ex
        r["scope"] = "user_club"
        players.append(r)
    sq = snap.get("squad") or {}
    source = "live+save" if extras else "live"
    out = {"_meta": meta(store, source, "user_club", warnings, save_as_of=save_as_of if extras else None),
           "user_team": snap.get("user_team"), "count": len(players), "total_wages_weekly": sq.get("total_wages"),
           "excluded_sold": sq.get("excluded"), "reads": {k: sq.get(k) for k in sq if k.endswith("_read")},
           "players": players,
           "notes": "app/goals/assists include every competition (by_competition splits them); ratings are integer "
                    "5-10 bands (rating_int_5_10), not the 0.0-10.0 screen value; wages are weekly in game currency; "
                    "save_extras are as of the last save (A11)"}
    if store.mode == "player":
        out["user_player"] = {"playerid": store.my_player, "name": store.user_player.get("name")}
    return out


def _find_player(store, args):
    """Resolve playerid / name against every known name map. Returns (pid, candidates)."""
    pid = args.get("playerid")
    name = args.get("name")
    players, _ = store.name_maps()
    if pid is not None:
        try:
            return int(pid), []
        except (TypeError, ValueError):
            raise ToolError("bad_playerid", "playerid must be an integer")
    if not name:
        raise ToolError("missing_argument", "give playerid or name")
    needle = str(name).strip().lower()
    exact = [(k, v) for k, v in players.items() if v and v.lower() == needle]
    if len(exact) == 1:
        return exact[0][0], []
    partial = [(k, v) for k, v in players.items() if v and needle in v.lower()]
    if len(partial) == 1:
        return partial[0][0], []
    return None, [{"playerid": k, "name": v} for k, v in sorted(partial, key=lambda kv: kv[1])[:20]]


def tool_player(store, args):
    require_snapshot(store)
    pid, candidates = _find_player(store, args)
    players, teams = store.name_maps()
    if pid is None:
        if candidates:
            raise ToolError("ambiguous_name", "several players match; call again with playerid",
                            candidates=candidates)
        raise ToolError("unknown_name", "no player with that name in the live files (squad, targets, world top "
                        "lists, transfers); real names exist only in the live files (A13)")
    snap = store.snapshot
    out = {"_meta": None, "playerid": pid, "name": players.get(pid)}
    sources = ["live"]
    scope = scope_player(store, pid)
    found = False
    if store.my_player == pid:
        out["user_player"] = store.user_player
        found = True
    sp = next((p for p in store.squad if p.get("playerid") == pid), None)
    if sp:
        out["squad_player"] = sp
        out["team"] = snap.get("user_team")
        found = True
    tg = next((t for t in snap.get("targets") or [] if t.get("playerid") == pid), None)
    if tg:
        out["transfer_target"] = tg
        found = True
    # negotiations involving this player
    neg = {}
    for key in ("offers", "offers_history", "bids", "player_talks"):
        rows = [strip_raw(r) for r in snap.get(key) or [] if isinstance(r, dict) and r.get("playerid") == pid]
        if rows:
            neg[key] = rows
            found = True
    if neg:
        out["negotiations"] = neg
    # world rows: top lists + transfers
    w = store.world
    top_rows = []
    for c in w.get("top") or []:
        for stat in ("goals", "assists", "clean_sheets", "clean_sheets_all_positions", "avg_rating"):
            for r in c.get(stat) or []:
                if isinstance(r, dict) and r.get("playerid") == pid:
                    top_rows.append({"compobjid": c.get("compobjid"), "competition": c.get("competition"),
                                     "season": c.get("season"), "stat": stat, **r})
    if top_rows:
        out["top_list_rows"] = top_rows
        found = True
    tr = [t for t in w.get("transfers") or [] if t.get("playerid") == pid]
    if tr:
        out["market_transfers"] = [dict(t, scope=scope_team(store, t.get("from_id"), t.get("to_id"))) for t in tr]
        found = True
    # save extras
    sd, _ = store.save_extract(refresh=False)
    save_as_of = None
    if sd and _identity_ok(store, sd):
        ex = {}
        for key, out_key in (("grudgelove", "grudge_love"), ("previous_teams", "previous_clubs"),
                             ("suspensions", "suspensions"), ("loans", "loans"), ("presigned", "presigned")):
            rows = [r for r in sd.get(key) or [] if isinstance(r, dict) and r.get("playerid") == pid]
            if rows:
                ex[out_key] = [_hydrate(r, players, teams) for r in rows]
        if pid in (sd.get("youth_graduates") or []):
            ex["youth_graduate"] = True
        for r in sd.get("season_start") or []:
            if r.get("playerid") == pid:
                ex["overall_at_season_start"] = r.get("overall")
        for nt in sd.get("national_teams") or []:
            if pid in (nt.get("roster") or []):
                ex.setdefault("national_rosters", []).append({"teamid": nt.get("teamid"), "team": nt.get("team"),
                                                             "gender": nt.get("gender")})
        if ex:
            out["save_extras"] = ex
            sources.append("save")
            save_as_of = sd.get("as_of_save")
            found = True
    if not found:
        raise ToolError("player_not_found", "id %d is not in the squad, targets, negotiations, world top lists, "
                        "market transfers or save extras" % pid, playerid=pid)
    out["scope"] = scope
    out["_meta"] = meta(store, "+".join(sources) if len(sources) > 1 else sources[0], scope, save_as_of=save_as_of)
    out["notes"] = ("ratings are integer 5-10 (rating_int_5_10); other clubs' players appear only through world "
                    "top lists, the market or save extras; attributes exist for squad players and targets only")
    return out


def _hydrate(row, players, teams):
    r = dict(row)
    for k in list(r):
        if k.endswith("playerid") and r[k] in players:
            r[k.replace("playerid", "player")] = players[r[k]]
        elif k.endswith("teamid") and r[k] in teams:
            r[k.replace("teamid", "team")] = teams[r[k]]
    return r


def _comp_ok(row, compobjid, competition):
    if compobjid is not None:
        return compobjid in (row.get("compobjid"), row.get("stage_compobjid"), row.get("competition_compobjid"))
    return text_match(row.get("competition"), competition)


def _user_league_compobjid(store):
    lt = store.snapshot.get("league_table") or {}
    return lt.get("compobjid")


def tool_competitions(store, args):
    require_snapshot(store)
    require_world(store)
    w = store.world
    view = args.get("view") or "standings"
    compobjid = args.get("compobjid")
    competition = args.get("competition")
    teamid = args.get("teamid")
    limit = clamp_limit(args.get("limit") or args.get("last") or args.get("next"), default=DEFAULT_LIMIT)
    base = {"_meta": None, "view": view, "world_game_date": w.get("game_date"), "season": w.get("season")}
    if view == "index":
        idx = {}
        for t in w.get("tables") or []:
            c = idx.setdefault(t.get("competition_compobjid") or t.get("compobjid"),
                               {"compobjid": t.get("competition_compobjid") or t.get("compobjid"),
                                "competition": t.get("competition"), "standings_groups": [], "user_plays": False})
            c["standings_groups"].append({"group_compobjid": t.get("compobjid"), "stage": t.get("stage_name"),
                                          "kind": t.get("kind"), "teams": t.get("teams"),
                                          "games_played": t.get("games_played")})
            if store.my_club is not None and any(r.get("teamid") == store.my_club for r in t.get("rows") or []):
                c["user_plays"] = True
        for f in w.get("fixtures_per_competition") or []:
            c = idx.setdefault(f.get("compobjid"), {"compobjid": f.get("compobjid"), "competition": f.get("competition"),
                                                    "standings_groups": [], "user_plays": False})
            c.update({"fixtures": f.get("fixtures"), "played": f.get("played")})
        for t in w.get("top") or []:
            c = idx.setdefault(t.get("compobjid"), {"compobjid": t.get("compobjid"), "competition": t.get("competition"),
                                                    "standings_groups": [], "user_plays": False})
            c["has_top_players"] = True
        for tc in (store.snapshot.get("league_table") or {}).get("team_competitions") or []:
            if tc.get("compobjid") in idx:
                idx[tc["compobjid"]]["user_plays"] = True
        rows = list(idx.values())
        if competition:
            rows = [r for r in rows if text_match(r.get("competition"), competition)]
        rows.sort(key=lambda r: (not r.get("user_plays"), -(r.get("played") or 0)))
        base.update({"competitions": rows, "count": len(rows),
                     "rules": "A14: a competition can have several standings groups (group_compobjid); A15: top lists "
                              "exist only for tracked competitions"})
        base["_meta"] = meta(store, "live", "global")
        return base
    if view == "standings":
        tabs = [t for t in w.get("tables") or [] if _comp_ok(t, compobjid, competition)]
        if teamid is not None:
            tabs = [t for t in tabs if any(r.get("teamid") == teamid for r in t.get("rows") or [])]
        elif compobjid is None and not competition:
            tabs = [t for t in tabs if any(r.get("teamid") == store.my_club for r in t.get("rows") or [])]
        tabs = [t for t in tabs if (t.get("games_played") or 0) > 0] or tabs
        tabs.sort(key=lambda t: -(t.get("games_played") or 0))
        out_tabs = []
        for t in tabs[:12]:
            rows = t.get("rows") or []
            has_user = any(r.get("teamid") == store.my_club for r in rows)
            out_tabs.append({**{k: t.get(k) for k in ("compobjid", "stage_compobjid", "competition_compobjid",
                                                      "competition", "stage_name", "kind", "season", "teams",
                                                      "games_played")},
                             "scope": "user_club" if has_user else "global",
                             "rows": [dict(r, user_club=True) if r.get("teamid") == store.my_club else r
                                      for r in rows[:max(limit, 24) if args.get("limit") is None else limit]]})
        base.update({"tables": out_tabs, "groups_found": len(tabs),
                     "rules": "A12/A14: one group = one table; report the compobjid; points/gd as given"})
        base["_meta"] = meta(store, "live", "user_club" if any(t["scope"] == "user_club" for t in out_tabs) else "global")
        return base
    if view == "fixtures":
        rows = [f for f in w.get("fixtures") or [] if _comp_ok(f, compobjid, competition)]
        tid = teamid
        if tid is None and compobjid is None and not competition:
            tid = store.my_club
        if tid is not None:
            rows = [f for f in rows if tid in (f.get("home_id"), f.get("away_id"))]
        if args.get("date_from"):
            rows = [f for f in rows if (f.get("date") or "") >= args["date_from"]]
        if args.get("date_to"):
            rows = [f for f in rows if (f.get("date") or "") <= args["date_to"]]
        played = sorted([f for f in rows if f.get("played")], key=lambda f: f.get("date") or "", reverse=True)
        upcoming = sorted([f for f in rows if not f.get("played")], key=lambda f: f.get("date") or "")
        last = clamp_limit(args.get("last"), default=5)
        nxt = clamp_limit(args.get("next"), default=5)
        keep = ("date", "competition", "compobjid", "stage_compobjid", "group", "home", "home_id", "away", "away_id",
                "score", "home_score", "away_score", "pens", "penalties", "played")
        slim = lambda f: our_view(store, {k: f.get(k) for k in keep if f.get(k) is not None})  # noqa: E731
        base.update({"teamid": tid, "played": [slim(f) for f in played[:last]], "played_total": len(played),
                     "upcoming": [slim(f) for f in upcoming[:nxt]], "upcoming_total": len(upcoming),
                     "rules": "A6: score is home-away in the fixture's order; user_score/outcome are from the "
                              "user's side and exist only for the user's club"})
        base["_meta"] = meta(store, "live", "user_club" if tid == store.my_club and tid is not None else "global")
        return base
    if view == "top_players":
        stat = args.get("stat") or "goals"
        comps = [t for t in w.get("top") or [] if _comp_ok(t, compobjid, competition)]
        if compobjid is None and not competition:
            mine = {f.get("compobjid") for f in store.snapshot.get("next_fixtures") or []} | \
                   {f.get("compobjid") for f in store.snapshot.get("last_results") or []}
            comps = [t for t in comps if t.get("compobjid") in mine] or comps[:3]
        out = []
        for t in comps[:6]:
            rows = t.get(stat)
            if rows is None:
                raise ToolError("bad_stat", "stat must be one of goals, assists, clean_sheets, "
                                "clean_sheets_all_positions, avg_rating")
            if teamid is not None:
                rows = [r for r in rows if r.get("teamid") == teamid]
            out.append({"compobjid": t.get("compobjid"), "competition": t.get("competition"), "season": t.get("season"),
                        "stat": stat, "players_in_stats": t.get("players"),
                        "rows": [dict(r, scope=scope_player(store, r.get("playerid"), r.get("teamid")))
                                 for r in rows[:limit]]})
        base.update({"stat": stat, "top_players": out,
                     "rules": "A15: top 10 per competition, only tracked competitions; avg_rating is the game's "
                              "internal value, not the 0.0-10.0 screen rating"})
        base["_meta"] = meta(store, "live", "global")
        return base
    raise ToolError("bad_view", "view must be standings, fixtures, top_players or index")


def _decode_actions(row, detail):
    r = strip_raw(row)
    acts = r.get("actions") or []
    if acts:
        last = acts[-1]
        r["last_action"] = last.get("code")
        r["last_action_label"] = NEGOTIATION_CODES.get(last.get("code"), "unknown")
        r["last_action_date"] = ymd(last.get("date"))
        r["stage"] = "open" if last.get("code") in (6, 2, 3) else "closed"
        if not detail:
            r["actions_count"] = len(acts)
            r.pop("actions", None)
        else:
            r["actions"] = [{"code": a.get("code"), "label": NEGOTIATION_CODES.get(a.get("code"), "unknown"),
                             "date": ymd(a.get("date"))} for a in acts]
    return r


def tool_transfers(store, args):
    require_snapshot(store)
    snap = store.snapshot
    scope = args.get("scope") or "user_club"
    include_history = bool(args.get("include_history"))
    detail = bool(args.get("detail"))
    limit = clamp_limit(args.get("limit"), default=20)
    teamid = args.get("teamid")
    w = store.world
    if scope == "market":
        require_world(store)
        rows = w.get("transfers") or []
        if teamid is not None:
            rows = [t for t in rows if teamid in (t.get("from_id"), t.get("to_id"))]
        if args.get("type"):
            rows = [t for t in rows if t.get("type") == args["type"]]
        rows = sorted(rows, key=lambda t: t.get("date") or "", reverse=True)
        fin = w.get("club_transfer_finance") or []
        if teamid is not None:
            fin = [c for c in fin if c.get("teamid") == teamid]
        out = {"_meta": meta(store, "live", "global"), "scope": "market", "season": w.get("season"),
               "count": len(rows),
               "transfers": [dict(t, scope=scope_team(store, t.get("from_id"), t.get("to_id"))) for t in rows[:limit]],
               "club_transfer_finance": [dict(c, scope=scope_team(store, c.get("teamid"))) for c in fin[:limit]],
               "notes": "market-wide, this season, newest first, cap 400 in the bridge file; fee null/0 = unknown "
                        "or free; game currency"}
        return out
    if scope != "user_club":
        raise ToolError("bad_scope", "scope must be user_club or market")
    out = {"_meta": None, "scope": "user_club", "user_team": snap.get("user_team"), "game_date": snap.get("game_date")}
    # club transfers this season: snapshot (manager) or world rows involving the club (both modes)
    club_tr = snap.get("transfers")
    if club_tr is None and store.my_club is not None:
        club_tr = [t for t in w.get("transfers") or [] if store.my_club in (t.get("from_id"), t.get("to_id"))]
    out["transfers"] = [dict(t, scope="user_club") for t in (club_tr or [])[:limit]]
    if store.mode == "player":
        out["offers"] = None
        out["not_available_reason"] = ("player career: club negotiations are not exposed; the user's own "
                                       "transfer/loan/withdraw requests are in fc26_my_career.user_player.career")
        out["user_requests"] = (store.user_player.get("career") or {})
        out["_meta"] = meta(store, "live", "user_player")
        return out
    offers = [_decode_actions(o, detail) for o in snap.get("offers") or []]
    out["offers"] = [o for o in offers if o.get("open", True)][:limit]
    out["bids"] = [_decode_actions(b, detail) for b in (snap.get("bids") or [])[:limit]]
    out["player_talks"] = [_decode_actions(t, detail) for t in (snap.get("player_talks") or [])[:limit]]
    out["targets"] = snap.get("targets") or []
    if include_history:
        out["offers_history"] = [_decode_actions(o, detail) for o in (snap.get("offers_history") or [])[:limit]]
    out["offers_read_at"] = snap.get("offers_read_at")
    out["offers_error"] = snap.get("offers_error")
    out["pending_reads"] = snap.get("pending_reads")
    out["codes"] = NEGOTIATION_CODES
    out["rules"] = ("the user is the SELLER in offers[] (received) and the BUYER in bids[] (sent); stage = last "
                    "action code (6/2/3 open; 0/4 accepted; 1 rejected; 5 expired); quote fees/wages only when the "
                    "field is present")
    out["_meta"] = meta(store, "live", "user_club")
    return out


def _identity_ok(store, sd):
    """Save identity must match the live snapshot (club id; player id in player careers)."""
    snap = store.snapshot
    if not snap or not sd:
        return True  # nothing to compare: the caller labels the source
    su = sd.get("user") or {}
    club_live = (snap.get("user") or {}).get("club_teamid") or store.my_club
    if club_live is not None and su.get("clubteamid") is not None and su.get("clubteamid") != club_live:
        return False
    if store.mode == "player" and sd.get("career_type") == "player":
        sp = (sd.get("user_player") or sd.get("player") or {}).get("playerid")
        if sp is not None and store.my_player is not None and sp != store.my_player:
            return False
    if store.mode == "manager" and sd.get("career_type") == "player":
        return False
    if store.mode == "player" and sd.get("career_type") == "manager":
        return False
    return True


def tool_save_data(store, args):
    category = args.get("category") or "summary"
    if category not in SAVE_CATEGORIES:
        raise ToolError("bad_category", "category must be one of: " + ", ".join(SAVE_CATEGORIES))
    sd, status = store.save_extract()
    if not sd:
        raise ToolError("no_save_extract", "no save_extract.json and no readable save: play and save the career "
                        "once (the save reader parses a COPY of the newest save under %s)" % store.saves_dir,
                        reader_status=status)
    warnings = []
    if status and not status.get("ok"):
        warnings.append("save reader: %s (%s); using the extract on disk" % (status.get("error"),
                                                                           status.get("detail", "")[:200]))
    if not _identity_ok(store, sd):
        return {"_meta": meta(store, "save", "mixed", warnings, save_as_of=sd.get("as_of_save")),
                "error": "different_career",
                "hint": "the newest save on disk belongs to another career than the one the bridge is watching; "
                        "say so instead of mixing careers",
                "save_user": sd.get("user"), "save_career_type": sd.get("career_type"),
                "live_user": store.snapshot.get("user"), "live_mode": store.mode}
    players, teams = store.name_maps()
    limit = clamp_limit(args.get("limit"), default=30, cap=200)
    teamid = args.get("teamid")
    out = {"_meta": meta(store, "save", "user_club", warnings, save_as_of=sd.get("as_of_save")),
           "category": category, "save_file": sd.get("save_file"), "as_of_save": sd.get("as_of_save"),
           "career_type": sd.get("career_type"), "team_id": sd.get("team_id")}

    def hyd(rows):
        res = []
        for r in rows[:limit]:
            if isinstance(r, dict):
                res.append(_hydrate(r, players, teams))
            else:
                res.append({"playerid": r, "name": players.get(r)})
        return res
    if category == "summary":
        out["sections"] = {k: (len(v) if isinstance(v, list) else ("yes" if v else "no"))
                           for k, v in sd.items() if k in ("suspensions", "grudgelove", "previous_teams", "season_start",
                                                           "squad_ranking", "youth_graduates", "presigned", "loans",
                                                           "manager_history", "news_events", "national_teams",
                                                           "managers", "team_roles", "manager_info")}
        out["squad_ids_in_save"] = len(sd.get("squad_ids") or [])
        out["loaned_out_ids"] = hyd(sd.get("loaned_out_ids") or [])
        out["excluded_contract_rows"] = hyd(sd.get("excluded_contract_rows") or [])
        out["categories"] = list(SAVE_CATEGORIES)
    elif category == "national_teams":
        rows = sd.get("national_teams") or []
        squad = set(sd.get("squad_ids") or []) | store.squad_ids
        if teamid is not None:
            rows = [r for r in rows if r.get("teamid") == teamid]
            out["national_teams"] = [dict(r, roster=[{"playerid": p, "name": players.get(p),
                                                      "scope": scope_player(store, p)} for p in r.get("roster") or []])
                                     for r in rows[:limit]]
            out["_meta"]["scope"] = "global"
        else:
            calls = []
            for r in rows:
                for p in r.get("roster") or []:
                    if p in squad:
                        calls.append({"playerid": p, "name": players.get(p), "national_teamid": r.get("teamid"),
                                      "national_team": r.get("team"), "gender": r.get("gender")})
            out["user_players_in_national_rosters"] = calls[:limit]
            out["national_teams_index"] = [{"teamid": r.get("teamid"), "team": r.get("team"), "gender": r.get("gender"),
                                            "roster_size": len(r.get("roster") or [])} for r in rows]
        out["rules"] = "A18: rosters as of the last save; a call-up is claimed only from a diff between two saves"
    elif category == "managers":
        rows = sd.get("managers") or []
        if teamid is not None:
            rows = [r for r in rows if r.get("teamid") == teamid]
        if args.get("name"):
            rows = [r for r in rows if text_match(r.get("name"), args["name"])]
        out["count"] = len(rows)
        out["managers"] = [dict(_hydrate(r, players, teams), birthdate_iso=fifa_date(r.get("birthdate")),
                                scope=scope_team(store, r.get("teamid"))) for r in rows[:limit]]
        out["_meta"]["scope"] = "global"
    elif category == "board_history":
        out["manager_info"] = sd.get("manager_info")
        out["manager_history"] = sd.get("manager_history")
        out["stale_fields"] = ("A11: board confidence, season objectives and reputation are placeholder zeros in the "
                               "save; manager_history W/D/L does not track the current season live")
    elif category == "suspensions":
        out["suspensions"] = hyd(sd.get("suspensions") or [])
    elif category == "loans":
        rows = hyd(sd.get("loans") or [])
        for r in rows:
            if r.get("loandateend"):
                r["loan_end"] = fifa_date(r["loandateend"])
        out["loans"] = rows
        out["loaned_out_ids"] = hyd(sd.get("loaned_out_ids") or [])
        out["rules"] = "A9: loaned-out players are still the user's players; their season stats belong to the loan club"
    elif category == "presigned":
        rows = hyd(sd.get("presigned") or [])
        for r in rows:
            r["completedate"] = ymd(r.get("completedate"))
        out["presigned"] = rows
    elif category == "youth":
        out["youth_graduates"] = hyd(sd.get("youth_graduates") or [])
        out["rules"] = "A10: mid-season youth players have no real name; graduates get a contract at the user's club"
    elif category == "grudge_love":
        out["grudge_love"] = hyd(sd.get("grudgelove") or [])
        out["notes"] = "level_of_emotion: positive = love, negative = grudge (raw game code)"
    elif category == "previous_clubs":
        out["previous_clubs"] = hyd(sd.get("previous_teams") or [])
    elif category == "team_roles":
        out["team_roles"] = _hydrate(sd.get("team_roles") or {}, players, teams)
    elif category == "season_start":
        out["season_start"] = hyd(sd.get("season_start") or [])
        out["notes"] = "overall at the start of the season (compare with live overall for progress)"
    elif category == "squad_ranking":
        out["squad_ranking"] = hyd(sd.get("squad_ranking") or [])
        out["notes"] = "A7: curroverall/lastoverall are overall x10-ish (875 = OVR 85); 1023 / 20 are sentinels"
    elif category == "news_events":
        out["news_events"] = (sd.get("news_events") or [])[:limit]
        out["_meta"]["scope"] = "global"
    return out


def _payload_row(store, e, players):
    f = e.get("fields") or {}
    ids = [v for k, v in f.items() if "teamid" in str(k)]
    pid = f.get("playerid")
    sc = scope_player(store, pid, *ids) if pid else scope_team(store, *ids) if ids else "unattributed"
    r = {"date": ymd(e.get("date")), "id": e.get("id"), "kind": f.get("kind"), "le_name": e.get("name"),
         "fields": {k: v for k, v in f.items() if k != "kind"}, "scope": sc}
    if pid in players:
        r["player"] = players[pid]
    return r


def tool_global_events(store, args):
    require_snapshot(store)
    snap = store.snapshot
    kind = args.get("kind")
    days = args.get("days")
    ours_only = bool(args.get("ours_only"))
    limit = clamp_limit(args.get("limit"), default=30, cap=200)
    players, _ = store.name_maps()
    names = snap.get("events") or []
    payloads = [_payload_row(store, e, players) for e in snap.get("event_payloads") or []]
    if args.get("history"):
        rows = []
        try:
            with open(store.path("event_payloads.jsonl"), encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        if rows:
            payloads = [_payload_row(store, e, players) for e in rows]
    if not args.get("include_undecoded"):
        payloads = [p for p in payloads if p.get("kind")]
    if kind:
        names = [n for n in names if text_match(n.get("name"), kind)]
        payloads = [p for p in payloads if text_match(p.get("kind"), kind) or text_match(p.get("le_name"), kind)]
    if days:
        try:
            cutoff = _shift_date(snap.get("game_date"), -int(days))
        except Exception:
            cutoff = None
        if cutoff:
            names = [n for n in names if (n.get("date") or "") >= cutoff]
            payloads = [p for p in payloads if (p.get("date") or "") >= cutoff]
    if ours_only:
        payloads = [p for p in payloads if p["scope"] in ("user_club", "user_player")]
    out = {"_meta": meta(store, "live", "global"), "game_date": snap.get("game_date"),
           "event_names": {"scope": "global", "count": len(names), "rows": names[-limit:][::-1],
                           "rules": "A5: event names carry no owner; they say something happened somewhere in the "
                                    "game world; never attribute them to the user's club or a named player"},
           "event_payloads": {"count": len(payloads), "rows": payloads[-limit:][::-1],
                              "rules": "A5b: scope comes only from the payload's own teamid/playerid; le_name is "
                                       "Live Editor's enum name and is wrong for many ids past ~35: trust id + kind"},
           "events_note": snap.get("events_note")}
    return out


def _shift_date(iso, delta_days):
    import datetime
    d = datetime.date.fromisoformat(iso) + datetime.timedelta(days=delta_days)
    return d.isoformat()


# ----------------------------------------------------------------------------------------------
# Tool registry (schemas are the single source for MCP, REST/OpenAPI and the CLI)
# ----------------------------------------------------------------------------------------------

INT = {"type": "integer"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}
RULES = ("Every answer has _meta: source (live/save), scope (user_club/user_player/global), live_game_date, data "
         "age and warnings. Say what date values are as of. Only scope user_club/user_player is about the user; "
         "event names never are. Scores are home-away; use user_score/outcome. Ratings are integers 5-10. Never "
         "invent missing values. Read-only: nothing here changes the game.")

TOOLS = [
    {"name": "fc26_status",
     "description": "START HERE. Is the Live Editor bridge alive, which career mode (manager/player), the live "
                    "game date, how old snapshot/heartbeat are, compat (full/partial/unsupported), which files "
                    "exist, and how to refresh. No game access. " + RULES,
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "run": tool_status},
    {"name": "fc26_my_career",
     "description": "One compact overview of the user's career. Manager: club, budgets, league position with a "
                    "table slice, last results and next fixtures from the user's side (user_score, outcome W/D/L, "
                    "opponent), squad counts and injuries, open offers. Player: the user's player profile "
                    "(attributes, recent integer ratings, season stats, transfer/loan requests, season history) "
                    "plus club context. " + RULES,
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "run": tool_my_career},
    {"name": "fc26_squad",
     "description": "The user's club squad: per player position, age, overall, potential, season apps/goals/"
                    "assists/cards, wage, contract, injury days, flags, recent integer ratings, plus save extras "
                    "(suspension, loan, pre-signed, youth graduate, overall at season start). "
                    "include_attributes=true returns full rows. Filters position, sort "
                    "(overall|potential|age|goals|assists|app|wage|contract_until|name). " + RULES,
     "inputSchema": {"type": "object", "properties": {"include_attributes": BOOL, "position": STR, "sort": STR},
                     "additionalProperties": False},
     "run": tool_squad},
    {"name": "fc26_player",
     "description": "One player by playerid or name (exact, else partial; ambiguous names return candidates). "
                    "Squad players: full detail. Transfer targets: detail from negotiations. Other players: "
                    "world top-list rows, market transfers and save extras (previous clubs, grudge/love, national "
                    "rosters). The user's own player (player career) is in fc26_my_career. " + RULES,
     "inputSchema": {"type": "object", "properties": {"playerid": INT, "name": STR}, "additionalProperties": False},
     "run": tool_player},
    {"name": "fc26_competitions",
     "description": "Any competition the game simulates (world.json, written once per game day). view=standings "
                    "(default: groups containing the user's club), fixtures (default: the user's club; last/next "
                    "counts; date_from/date_to), top_players (stat goals|assists|clean_sheets|"
                    "clean_sheets_all_positions|avg_rating; default: the user's competitions), index (every "
                    "competition with its standings groups). Filters compobjid, competition (name substring), "
                    "teamid, limit. " + RULES,
     "inputSchema": {"type": "object",
                     "properties": {"view": {"type": "string", "enum": ["standings", "fixtures", "top_players", "index"]},
                                    "compobjid": INT, "competition": STR, "teamid": INT, "last": INT, "next": INT,
                                    "stat": STR, "limit": INT, "date_from": STR, "date_to": STR},
                     "additionalProperties": False},
     "run": tool_competitions},
    {"name": "fc26_transfers",
     "description": "scope=user_club (default): the user's club transfers this season, open offers received "
                    "(decoded stages), sent bids, player contract talks, targets; include_history=true adds "
                    "finished offers; detail=true keeps action lists. scope=market: market-wide transfers this "
                    "season (teamid, type transfer|loan) and per-club transfer finance. Player career: offers=null "
                    "with not_available_reason and the user's own requests. " + RULES,
     "inputSchema": {"type": "object",
                     "properties": {"scope": {"type": "string", "enum": ["user_club", "market"]}, "teamid": INT,
                                    "include_history": BOOL, "detail": BOOL, "type": STR, "limit": INT},
                     "additionalProperties": False},
     "run": tool_transfers},
    {"name": "fc26_save_data",
     "description": "Save-file-only tables (parsed from a COPY of the newest save, 'as of the last save', "
                    "identity-checked against the live career): category summary|national_teams|managers|"
                    "board_history|suspensions|loans|presigned|youth|grudge_love|previous_clubs|team_roles|"
                    "season_start|squad_ranking|news_events; teamid, name, limit. Names come from the live files; "
                    "unknown ids stay ids. Board confidence/objectives are stale zeros in the save. " + RULES,
     "inputSchema": {"type": "object",
                     "properties": {"category": {"type": "string", "enum": list(SAVE_CATEGORIES)}, "teamid": INT,
                                    "name": STR, "limit": INT},
                     "additionalProperties": False},
     "run": tool_save_data},
    {"name": "fc26_global_events",
     "description": "The game's event feed: filtered event names (GLOBAL counts, never attributed) and decoded "
                    "event payloads (injury, suspension, transfer offer, transfer completed, OVR change...) with "
                    "a scope per row (user_club/user_player only when the payload's own ids are the user's). "
                    "Filters kind, days (back from the game date), ours_only, history=true (whole log), "
                    "include_undecoded, limit. " + RULES,
     "inputSchema": {"type": "object",
                     "properties": {"kind": STR, "days": INT, "ours_only": BOOL, "history": BOOL,
                                    "include_undecoded": BOOL, "limit": INT},
                     "additionalProperties": False},
     "run": tool_global_events},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def tool_specs():
    """Tool list without the callables (what MCP tools/list returns)."""
    return [{k: v for k, v in t.items() if k != "run"} for t in TOOLS]


def _coerce(schema, args):
    """REST / CLI give strings: coerce by the schema (integers, booleans)."""
    props = (schema or {}).get("properties") or {}
    out = {}
    for k, v in (args or {}).items():
        p = props.get(k) or {}
        t = p.get("type")
        if isinstance(v, str):
            if t == "integer":
                try:
                    v = int(v)
                except ValueError:
                    raise ToolError("bad_argument", "%s must be an integer" % k)
            elif t == "boolean":
                v = v.strip().lower() in ("1", "true", "yes", "on")
        out[k] = v
    return out


def call_tool(name, args=None, data_dir=None, saves_dir=None, now=None, save_refresh=True):
    """Run one tool; always returns a dict (errors as {"error":..., "hint":...})."""
    tool = BY_NAME.get(name)
    if not tool:
        return {"error": "unknown_tool", "hint": "tools: " + ", ".join(BY_NAME)}
    store = Store(data_dir=data_dir, saves_dir=saves_dir, now=now, save_refresh=save_refresh)
    try:
        a = _coerce(tool["inputSchema"], args or {})
        unknown = [k for k in a if k not in (tool["inputSchema"].get("properties") or {})]
        if unknown:
            return {"error": "unknown_argument", "hint": "unknown argument(s): %s" % ", ".join(unknown),
                    "_meta": meta(store, "live", "mixed")}
        return tool["run"](store, a)
    except ToolError as e:
        d = e.as_dict()
        d["_meta"] = meta(store, "live", "mixed")
        return d
    except Exception as e:  # never kill the server
        return {"error": "tool_exception", "detail": "%s: %s" % (type(e).__name__, e),
                "_meta": meta(store, "live", "mixed")}


def to_json(obj, compact=False):
    if compact:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
