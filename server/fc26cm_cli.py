#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - command line (for assistants without MCP, and for humans).

Prints a compact plain-text summary you can paste into any chat (ChatGPT, a web LLM, ...),
or the full JSON with --json.

    python fc26cm_cli.py status
    python fc26cm_cli.py career
    python fc26cm_cli.py squad [--position ST] [--sort goals]
    python fc26cm_cli.py player <name or id>
    python fc26cm_cli.py table | fixtures | top [--stat assists] | competitions
    python fc26cm_cli.py transfers [--history] | market
    python fc26cm_cli.py save [category]
    python fc26cm_cli.py events [--days 7] [--ours]
    python fc26cm_cli.py all            # every summary at once (paste this)
    python fc26cm_cli.py tools          # tool list
    python fc26cm_cli.py openapi        # OpenAPI JSON of the REST API

Options: --json (raw tool answer), --data-dir DIR, --saves-dir DIR, --limit N.
Standard library only. Read-only.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fc26cm_core as core  # noqa: E402

COMMANDS = {
    "status": ("fc26_status", {}),
    "career": ("fc26_my_career", {}),
    "squad": ("fc26_squad", {}),
    "player": ("fc26_player", {}),
    "table": ("fc26_competitions", {"view": "standings"}),
    "fixtures": ("fc26_competitions", {"view": "fixtures"}),
    "top": ("fc26_competitions", {"view": "top_players"}),
    "competitions": ("fc26_competitions", {"view": "index"}),
    "transfers": ("fc26_transfers", {"scope": "user_club"}),
    "market": ("fc26_transfers", {"scope": "market"}),
    "save": ("fc26_save_data", {}),
    "events": ("fc26_global_events", {}),
}


# ----------------------------------------------------------------------------------------------
# Rendering (compact text; every block starts with the freshness line)
# ----------------------------------------------------------------------------------------------

def _meta_line(res):
    m = res.get("_meta") or {}
    parts = ["[%s]" % core.SERVER_NAME, "source=%s" % m.get("source"), "scope=%s" % m.get("scope"),
             "mode=%s" % m.get("mode"), "game_date=%s" % m.get("live_game_date")]
    if m.get("save_as_of"):
        parts.append("save_as_of=%s" % m["save_as_of"])
    if m.get("snapshot_age_sec") is not None:
        parts.append("snapshot_age=%ss" % m["snapshot_age_sec"])
    parts.append("bridge_alive=%s" % m.get("bridge_alive"))
    parts.append("compat=%s" % m.get("compat"))
    lines = [" ".join(parts)]
    for w in m.get("warnings") or []:
        lines.append("! " + w)
    return lines


def _fx(f):
    s = f.get("score") or ("%s-%s" % (f.get("home_score"), f.get("away_score")) if f.get("home_score") is not None else "")
    line = "%s %s: %s vs %s %s" % (f.get("date"), f.get("competition"), f.get("home"), f.get("away"), s)
    if f.get("outcome"):
        line += "  -> %s (%s %s)" % (f["outcome"], "home" if f.get("user_side") == "home" else "away", f.get("user_score"))
    return line.strip()


def _r(v):
    return "-" if v is None else v


def render(name, res):
    """Plain-text summary of one tool answer."""
    lines = _meta_line(res)
    if res.get("error"):
        lines.append("ERROR %s: %s" % (res["error"], res.get("hint") or res.get("detail") or ""))
        for c in res.get("candidates") or []:
            lines.append("  candidate: %s (%s)" % (c.get("name"), c.get("playerid")))
        return "\n".join(lines) + "\n"
    fn = RENDERERS.get(name)
    if fn:
        lines.extend(fn(res))
    else:
        lines.append(core.to_json(res, compact=True))
    return "\n".join(lines) + "\n"


def r_status(res):
    out = ["STATUS product=%s server=%s bridge=%s le=%s compat=%s memory_reads=%s" % (
        res.get("product"), res.get("server_version"), res.get("bridge"), res.get("le_version"), res.get("compat"),
        res.get("memory_reads"))]
    out.append("mode=%s career_ready=%s game_date=%s user=%s team=%s" % (
        res.get("mode"), res.get("career_ready"), res.get("game_date"),
        (res.get("user") or {}).get("name"), (res.get("user_team") or {}).get("name")))
    files = res.get("files") or {}
    out.append("files: " + ", ".join("%s%s" % (k, "" if v.get("exists") else "(missing)") +
                                      ("[%ss]" % v["age_sec"] if v.get("age_sec") is not None else "")
                                      for k, v in files.items()))
    for h in res.get("how_to_refresh") or []:
        out.append("refresh: " + h)
    out.append("data_dir=%s" % res.get("data_dir"))
    return out


def r_career(res):
    out = []
    u, t = res.get("user") or {}, res.get("user_team") or {}
    out.append("CAREER mode=%s user=%s %s club=%s (id %s) season=%s" % (
        res.get("mode"), u.get("firstname"), u.get("name"), t.get("name"), t.get("id"), u.get("seasons")))
    if res.get("mode") != "player":
        out.append("budgets: transfer=%s wage=%s %s" % (_r(res.get("transfer_budget")), _r(res.get("wage_budget")),
                                                        res.get("wage_budget_note") or ""))
    lp = res.get("league_position") or {}
    row = lp.get("user_row")
    if row:
        out.append("league %s (compobjid %s): pos %s/%s  P%s W%s D%s L%s GF%s GA%s pts %s" % (
            lp.get("competition"), lp.get("compobjid"), row.get("pos"), lp.get("teams"), row.get("p"), row.get("w"),
            row.get("d"), row.get("l"), row.get("gf"), row.get("ga"), row.get("pts")))
    out.append("last results:")
    out.extend("  " + _fx(f) for f in res.get("last_results") or [])
    out.append("next fixtures:")
    out.extend("  " + _fx(f) for f in res.get("next_fixtures") or [])
    if res.get("mode") == "player":
        up = res.get("user_player") or {}
        s = up.get("season") or {}
        out.append("my player: %s (id %s) %s age %s OVR %s POT %s wage %s" % (
            up.get("name"), up.get("playerid"), up.get("position"), up.get("age"), up.get("overall"),
            up.get("potential"), up.get("wage")))
        rr = [(x.get("rating_int_5_10", x.get("rating")) if isinstance(x, dict) else x)
              for x in up.get("recent_ratings") or []]
        out.append("season: app %s goals %s assists %s yellow %s red %s; recent ratings (int 5-10): %s avg %s" % (
            s.get("app"), s.get("goals"), s.get("assists"), s.get("yellow"), s.get("red"), rr,
            up.get("avg_rating_recent")))
        c = up.get("career") or {}
        out.append("requests: playerrequest=%s transfer=%s loan=%s withdraw=%s" % (
            c.get("playerrequest"), c.get("numtransferrequests"), c.get("numloanrequests"),
            c.get("numwithdrawrequests")))
        for h in up.get("history") or []:
            out.append("  history season %s: team %s app %s goals %s assists %s OVR %s value %s pos %s" % (
                h.get("season"), h.get("teamid"), h.get("appearances"), h.get("goals"), h.get("assists"),
                h.get("overall"), h.get("value"), h.get("tableposition")))
    else:
        sq = res.get("squad_summary") or {}
        inj = ", ".join("%s (%sd)" % (i.get("name"), i.get("days")) for i in sq.get("injured") or []) or "none"
        out.append("squad: %s players, weekly wages %s, injured: %s" % (sq.get("count"), sq.get("total_wages_weekly"), inj))
        out.append("open offers received: %s, open bids: %s, offers read at %s, pending reads: %s" % (
            res.get("open_offers"), res.get("open_bids"), res.get("offers_read_at"), res.get("pending_reads")))
    return out


def r_squad(res):
    out = ["SQUAD %s: %s players, weekly wages %s" % ((res.get("user_team") or {}).get("name"), res.get("count"),
                                                      res.get("total_wages_weekly"))]
    for p in res.get("players") or []:
        bits = ["%-3s" % _r(p.get("position")), "%-24s" % (p.get("name") or p.get("playerid")),
                "id %s" % p.get("playerid"), "age %s" % _r(p.get("age")), "OVR %s/%s" % (_r(p.get("overall")), _r(p.get("potential"))),
                "app %s g %s a %s" % (_r(p.get("app")), _r(p.get("goals")), _r(p.get("assists")))]
        if p.get("wage") is not None:
            bits.append("wage %s" % p["wage"])
        if p.get("contract_until"):
            bits.append("until %s" % p["contract_until"])
        if p.get("injury_days"):
            bits.append("INJ %sd" % p["injury_days"])
        if p.get("flags"):
            bits.append("flags " + "/".join(p["flags"]))
        if p.get("recent_ratings_int_5_10"):
            bits.append("ratings %s" % p["recent_ratings_int_5_10"])
        ex = p.get("save_extras") or {}
        for k in ("suspension", "loan", "presigned", "youth_graduate"):
            if ex.get(k):
                bits.append(k.upper())
        out.append("  " + " ".join(bits))
    if res.get("notes"):
        out.append("notes: " + res["notes"])
    return out


def r_player(res):
    out = ["PLAYER %s (id %s) scope=%s" % (res.get("name"), res.get("playerid"), res.get("scope"))]
    sp = res.get("squad_player") or res.get("user_player")
    if sp:
        out.append("  %s age %s OVR %s POT %s wage %s contract %s app %s goals %s assists %s" % (
            sp.get("position"), sp.get("age"), sp.get("overall"), sp.get("potential"), _r(sp.get("wage")),
            _r(sp.get("contract_until")), _r(sp.get("app")), _r(sp.get("goals")), _r(sp.get("assists"))))
        if sp.get("attributes"):
            out.append("  attributes: " + ", ".join("%s %s" % kv for kv in sp["attributes"].items()))
        rt = [x.get("rating") for x in sp.get("ratings") or []] or sp.get("recent_ratings")
        if rt:
            out.append("  recent ratings (int 5-10): %s" % rt)
        if sp.get("by_competition"):
            out.append("  by competition: " + "; ".join("%s app %s g %s a %s" % (
                c.get("competition"), c.get("app"), c.get("goals"), c.get("assists")) for c in sp["by_competition"]))
    tg = res.get("transfer_target")
    if tg:
        out.append("  target: team %s age %s %s OVR %s POT %s attrs %s" % (
            tg.get("team") or tg.get("teamid"), tg.get("age"), tg.get("position"), tg.get("overall"),
            tg.get("potential"), tg.get("attributes")))
    for k, rows in (res.get("negotiations") or {}).items():
        for r in rows:
            out.append("  %s: %s %s club %s amount %s last_action %s open=%s" % (
                k, r.get("type"), r.get("direction"), r.get("club"), _r(r.get("amount")), r.get("last_action"),
                r.get("open")))
    for r in res.get("top_list_rows") or []:
        out.append("  top list %s %s: rank %s value %s (app %s)" % (r.get("competition"), r.get("stat"), r.get("rank"),
                                                                  r.get("value"), r.get("app")))
    for r in res.get("market_transfers") or []:
        out.append("  market: %s %s -> %s fee %s (%s)" % (r.get("date"), r.get("from"), r.get("to"), _r(r.get("fee")),
                                                        r.get("type")))
    ex = res.get("save_extras")
    if ex:
        out.append("  save extras (as of last save): " + core.to_json(ex, compact=True))
    return out


def r_competitions(res):
    view = res.get("view")
    out = ["COMPETITIONS view=%s world_game_date=%s season=%s" % (view, res.get("world_game_date"), res.get("season"))]
    if view == "standings":
        for t in res.get("tables") or []:
            out.append("table %s (compobjid %s, %s teams, %s games played, scope %s):" % (
                t.get("competition"), t.get("compobjid"), t.get("teams"), t.get("games_played"), t.get("scope")))
            for r in t.get("rows") or []:
                out.append("  %2s %-24s P%-3s W%-3s D%-3s L%-3s GF%-3s GA%-3s GD%-4s pts %s%s" % (
                    r.get("pos"), r.get("team"), r.get("p"), r.get("w"), r.get("d"), r.get("l"), r.get("gf"),
                    r.get("ga"), _r(r.get("gd")), r.get("pts"), "  <- user" if r.get("user_club") else ""))
    elif view == "fixtures":
        out.append("played (%s total):" % res.get("played_total"))
        out.extend("  " + _fx(f) for f in res.get("played") or [])
        out.append("upcoming (%s total):" % res.get("upcoming_total"))
        out.extend("  " + _fx(f) for f in res.get("upcoming") or [])
    elif view == "top_players":
        for c in res.get("top_players") or []:
            out.append("%s %s (compobjid %s):" % (c.get("competition"), c.get("stat"), c.get("compobjid")))
            for r in c.get("rows") or []:
                out.append("  %2s %-24s %-20s %s (app %s) scope=%s" % (r.get("rank"), r.get("player"), r.get("team"),
                                                                      r.get("value"), r.get("app"), r.get("scope")))
    elif view == "index":
        for c in res.get("competitions") or []:
            out.append("  %s %s groups=%s fixtures=%s played=%s%s" % (
                c.get("compobjid"), c.get("competition"), len(c.get("standings_groups") or []), _r(c.get("fixtures")),
                _r(c.get("played")), "  <- user plays" if c.get("user_plays") else ""))
    return out


def _neg(r):
    return "%s %s %s | club %s | amount %s | %s | last %s (%s) %s" % (
        r.get("date"), r.get("type"), r.get("player") or r.get("playerid"), r.get("club"), _r(r.get("amount")),
        r.get("status") or "", r.get("last_action"), r.get("last_action_label"), r.get("stage") or "")


def r_transfers(res):
    if res.get("scope") == "market":
        out = ["MARKET season %s (%s transfers known)" % (res.get("season"), res.get("count"))]
        for t in res.get("transfers") or []:
            out.append("  %s %s: %s -> %s fee %s (%s) scope=%s" % (t.get("date"), t.get("player"), t.get("from"),
                                                                  t.get("to"), _r(t.get("fee")), t.get("type"),
                                                                  t.get("scope")))
        for c in res.get("club_transfer_finance") or []:
            out.append("  finance %s: income %s expense %s bought %s sold %s loans in/out %s/%s" % (
                c.get("team"), c.get("income"), c.get("expense"), c.get("bought"), c.get("sold"), c.get("loans_in"),
                c.get("loans_out")))
        return out
    out = ["TRANSFERS %s game_date=%s" % ((res.get("user_team") or {}).get("name"), res.get("game_date"))]
    out.append("club transfers this season:")
    for t in res.get("transfers") or []:
        out.append("  %s %s: %s -> %s fee %s (%s)" % (t.get("date"), t.get("player"), t.get("from"), t.get("to"),
                                                     _r(t.get("fee")), t.get("type")))
    if res.get("offers") is None:
        out.append("offers: not available (%s)" % res.get("not_available_reason"))
        out.append("user requests: %s" % core.to_json(res.get("user_requests") or {}, compact=True))
        return out
    out.append("open offers received (user = seller), read at %s:" % res.get("offers_read_at"))
    out.extend("  " + _neg(r) for r in res.get("offers") or [])
    out.append("bids sent (user = buyer):")
    out.extend("  " + _neg(r) for r in res.get("bids") or [])
    out.append("player talks:")
    out.extend("  " + _neg(r) for r in res.get("player_talks") or [])
    if res.get("offers_history") is not None:
        out.append("offers history:")
        out.extend("  " + _neg(r) for r in res.get("offers_history") or [])
    for t in res.get("targets") or []:
        out.append("  target: %s (id %s) %s age %s OVR %s POT %s" % (t.get("name"), t.get("playerid"), t.get("position"),
                                                                     t.get("age"), t.get("overall"), t.get("potential")))
    if res.get("offers_error"):
        out.append("offers_error: %s" % res["offers_error"])
    return out


def r_save(res):
    out = ["SAVE DATA category=%s save=%s as_of=%s career_type=%s team_id=%s" % (
        res.get("category"), res.get("save_file"), res.get("as_of_save"), res.get("career_type"), res.get("team_id"))]
    skip = {"_meta", "category", "save_file", "as_of_save", "career_type", "team_id"}
    for k, v in res.items():
        if k in skip:
            continue
        if isinstance(v, list):
            out.append("%s (%d):" % (k, len(v)))
            for r in v[:40]:
                out.append("  " + core.to_json(r, compact=True))
        else:
            out.append("%s: %s" % (k, core.to_json(v, compact=True) if isinstance(v, dict) else v))
    return out


def r_events(res):
    out = ["EVENTS game_date=%s" % res.get("game_date")]
    en = res.get("event_names") or {}
    out.append("event names (GLOBAL, never attributed; %s):" % en.get("count"))
    out.extend("  %s %s x%s" % (r.get("date"), r.get("name"), r.get("count")) for r in en.get("rows") or [])
    ep = res.get("event_payloads") or {}
    out.append("decoded payloads (%s):" % ep.get("count"))
    for r in ep.get("rows") or []:
        out.append("  %s id %s %s scope=%s %s %s" % (r.get("date"), r.get("id"), r.get("kind"), r.get("scope"),
                                                     r.get("player") or "", core.to_json(r.get("fields"), compact=True)))
    return out


RENDERERS = {"fc26_status": r_status, "fc26_my_career": r_career, "fc26_squad": r_squad, "fc26_player": r_player,
             "fc26_competitions": r_competitions, "fc26_transfers": r_transfers, "fc26_save_data": r_save,
             "fc26_global_events": r_events}


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(description="%s - command line (read-only)" % core.PRODUCT)
    ap.add_argument("command", choices=sorted(COMMANDS) + ["all", "tools", "openapi"])
    ap.add_argument("target", nargs="?", help="player name/id (player), save category (save)")
    ap.add_argument("--json", action="store_true", help="print the raw tool answer as JSON")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--saves-dir", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--position", default=None)
    ap.add_argument("--sort", default=None)
    ap.add_argument("--stat", default=None)
    ap.add_argument("--compobjid", type=int, default=None)
    ap.add_argument("--competition", default=None)
    ap.add_argument("--teamid", type=int, default=None)
    ap.add_argument("--history", action="store_true", help="transfers: include finished offers")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--ours", action="store_true", help="events: only rows attributed to the user")
    ap.add_argument("--attributes", action="store_true", help="squad: full rows with attributes")
    return ap


def args_for(cmd, ns):
    name, base = COMMANDS[cmd]
    a = dict(base)
    if cmd == "player":
        if not ns.target:
            raise SystemExit("player: give a name or playerid")
        a["playerid" if str(ns.target).isdigit() else "name"] = int(ns.target) if str(ns.target).isdigit() else ns.target
    if cmd == "save" and ns.target:
        a["category"] = ns.target
    if cmd == "squad":
        if ns.position:
            a["position"] = ns.position
        if ns.sort:
            a["sort"] = ns.sort
        if ns.attributes:
            a["include_attributes"] = True
    if cmd in ("table", "fixtures", "top", "competitions"):
        for k in ("compobjid", "competition", "teamid", "stat"):
            if getattr(ns, k) is not None:
                a[k] = getattr(ns, k)
    if cmd == "transfers" and ns.history:
        a["include_history"] = True
    if cmd == "market" and ns.teamid is not None:
        a["teamid"] = ns.teamid
    if cmd == "events":
        if ns.days is not None:
            a["days"] = ns.days
        if ns.ours:
            a["ours_only"] = True
    if ns.limit is not None and cmd not in ("career", "status"):
        a["limit"] = ns.limit
    return name, a


def main(argv=None):
    ns = build_parser().parse_args(argv)
    if ns.command == "tools":
        for t in core.tool_specs():
            print("%s: %s" % (t["name"], t["description"][:200]))
            print("   args: " + ", ".join((t["inputSchema"].get("properties") or {}).keys()))
        return 0
    if ns.command == "openapi":
        import fc26cm_http
        print(core.to_json(fc26cm_http.openapi_spec("http://127.0.0.1:%d" % fc26cm_http.DEFAULT_PORT)))
        return 0
    cmds = ["status", "career", "squad", "table", "fixtures", "transfers", "events"] if ns.command == "all" else [ns.command]
    rc = 0
    for cmd in cmds:
        name, a = args_for(cmd, ns)
        res = core.call_tool(name, a, data_dir=ns.data_dir, saves_dir=ns.saves_dir)
        if res.get("error"):
            rc = 1
        if ns.json:
            print(core.to_json(res))
        else:
            sys.stdout.write(render(name, res))
            if len(cmds) > 1:
                print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
