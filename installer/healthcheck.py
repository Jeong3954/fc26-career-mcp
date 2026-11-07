#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - health check. Prints one PASS / WARN / FAIL / SKIP line per check.

    python installer/healthcheck.py [--le-dir DIR] [--data-dir DIR] [--home DIR] [--http-port N] [--json]

Checks: Python version, product files, bridge installed in Live Editor and identical to the package,
data folder + bridge files (status / heartbeat age / snapshot / world), save folder, stdio server
handshake (initialize + tools/list + fc26_status), local HTTP server (only if one is running), client
registrations. Exit code 0 when nothing FAILed, 1 otherwise. Writes nothing. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PRODUCT_ROOT, "server"))

import install  # noqa: E402

SERVER_PATH = install.SERVER_PATH
HTTP_PORT = install.HTTP_PORT


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, name, detail=""):
        self.rows.append({"status": status, "check": name, "detail": detail})

    def ok(self, *a):
        self.add("PASS", *a)

    def warn(self, *a):
        self.add("WARN", *a)

    def fail(self, *a):
        self.add("FAIL", *a)

    def skip(self, *a):
        self.add("SKIP", *a)

    @property
    def failed(self):
        return any(r["status"] == "FAIL" for r in self.rows)


def same_file(a, b):
    try:
        with open(a, "rb") as f1, open(b, "rb") as f2:
            return f1.read() == f2.read()
    except OSError:
        return False


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def check_python(rep, python):
    ok, ver = install.python_ok(python)
    if install.is_store_stub(python):
        rep.fail("python", "%s is the Microsoft Store stub; install real Python 3.9+" % python)
    elif ok:
        rep.ok("python", "%s (%s)" % (python, ver))
    else:
        rep.fail("python", "%s: %s (need 3.9+)" % (python, ver))


def check_product(rep):
    missing = [p for p in ("server/fc26cm_core.py", "server/fc26cm_mcp.py", "server/fc26cm_http.py",
                           "server/fc26cm_cli.py", "save/save_reader.py", "bridge/fc26cm_bridge_autorun.lua",
                           "bridge/fc26cm_bridge.lua", "skill/SKILL.md", "AGENTS.md")
               if not os.path.isfile(os.path.join(PRODUCT_ROOT, p))]
    if missing:
        rep.fail("product files", "missing: " + ", ".join(missing))
    else:
        rep.ok("product files", PRODUCT_ROOT)


def check_bridge(rep, le_dir, env):
    cands = [le_dir] if le_dir else []
    det = install.detect(env, le_dir)
    le = det.get("live_editor")
    if not le:
        rep.fail("live editor folder", "not found (pass --le-dir); candidates: %s" % cands)
        return
    rep.ok("live editor folder", le["path"])
    dst = os.path.join(le["autorun_dir"], install.BRIDGE_AUTORUN)
    src = os.path.join(install.BRIDGE_DIR, install.BRIDGE_AUTORUN)
    if not os.path.isfile(dst):
        rep.fail("bridge installed", "%s missing (run installer/install.py)" % dst)
    elif same_file(src, dst):
        rep.ok("bridge installed", dst)
    else:
        rep.warn("bridge installed", "%s differs from the package copy (older version?)" % dst)
    if le.get("bridge_conflicts"):
        rep.warn("other bridge scripts", "also in autorun: %s (two bridges double the per-day reads)"
                 % ", ".join(le["bridge_conflicts"]))
    else:
        rep.ok("other bridge scripts", "none")


def check_data(rep, data_dir, now):
    if not os.path.isdir(data_dir):
        rep.fail("data folder", "%s does not exist (installer creates it; the bridge writes into it)" % data_dir)
        return
    rep.ok("data folder", data_dir)
    st = read_json(os.path.join(data_dir, "status.json"))
    if st is None:
        rep.warn("bridge status.json", "not written yet: start FC 26 through Live Editor and load an offline career")
    else:
        rep.ok("bridge status.json", "bridge %s, LE %s, compat %s, mode %s" % (st.get("bridge"), st.get("le_version"),
                                                                              st.get("compat"), st.get("mode")))
        if st.get("compat") == "partial":
            rep.warn("bridge compat", "partial: %s (see docs/COMPATIBILITY.md)" % st.get("reason"))
        elif st.get("compat") == "unsupported":
            rep.fail("bridge compat", "unsupported: %s" % st.get("reason"))
    hb = read_json(os.path.join(data_dir, "heartbeat.json"))
    if hb is None:
        rep.warn("bridge heartbeat", "no heartbeat.json yet (open the career hub and wait ~10 s)")
    else:
        age = int(now - hb["unix"]) if hb.get("unix") else None
        if age is not None and age < 900:
            rep.ok("bridge heartbeat", "%d s ago, game date %s, career_ready %s" % (age, hb.get("game_date"),
                                                                                   hb.get("career_ready")))
        else:
            rep.warn("bridge heartbeat", "last beat %s s ago (game date %s): game or bridge not running now; "
                     "data is still readable" % (age, hb.get("game_date")))
    snap = os.path.join(data_dir, "snapshot.json")
    if os.path.isfile(snap):
        s = read_json(snap) or {}
        rep.ok("snapshot.json", "schema %s, mode %s, game date %s, written %s" % (s.get("schema"), s.get("mode"),
                                                                                 s.get("game_date"), s.get("written")))
    else:
        rep.warn("snapshot.json", "not written yet (career hub + ~10 s; else load the manual Lua in Lua Engine)")
    if os.path.isfile(os.path.join(data_dir, "world.json")):
        rep.ok("world.json", "present")
    else:
        rep.warn("world.json", "not written yet (once per game day after the hub)")


def check_saves(rep, saves_dir):
    try:
        saves = [n for n in os.listdir(saves_dir) if n.startswith("Cm")]
    except OSError:
        rep.warn("save folder", "%s not found (fc26_save_data needs a saved career)" % saves_dir)
        return
    if saves:
        rep.ok("save folder", "%d career save(s) in %s" % (len(saves), saves_dir))
    else:
        rep.warn("save folder", "no Cm* career save in %s yet" % saves_dir)


def check_stdio(rep, python, data_dir):
    if not os.path.isfile(SERVER_PATH):
        rep.fail("stdio server", "missing %s" % SERVER_PATH)
        return
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "healthcheck", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "fc26_status", "arguments": {}}}]
    inp = "".join(json.dumps(m) + "\n" for m in msgs)
    cmd = [python, SERVER_PATH, "--data-dir", data_dir]
    try:
        cp = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=60)
    except Exception as e:
        rep.fail("stdio server", "could not start: %s" % e)
        return
    lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
    try:
        res = [json.loads(ln) for ln in lines]
    except ValueError:
        rep.fail("stdio server", "non-JSON output: %s" % cp.stdout[:200])
        return
    by_id = {r.get("id"): r for r in res}
    if not by_id.get(1) or "result" not in by_id[1]:
        rep.fail("stdio server", "initialize failed: %s %s" % (cp.stdout[:200], cp.stderr[:200]))
        return
    tools = [t["name"] for t in by_id.get(2, {}).get("result", {}).get("tools", [])]
    if len(tools) != 8:
        rep.fail("stdio server", "tools/list returned %d tools: %s" % (len(tools), tools))
        return
    call = by_id.get(3, {}).get("result", {})
    rep.ok("stdio server", "%s v%s, 8 tools, fc26_status answered (isError=%s)"
           % (by_id[1]["result"]["serverInfo"]["name"], by_id[1]["result"]["serverInfo"]["version"],
              call.get("isError")))


def check_http(rep, port):
    url = "http://127.0.0.1:%d/health" % port
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        rep.ok("http server", "%s -> %s v%s (mcp: /mcp, rest: /api/<tool>)" % (url, data.get("server"),
                                                                              data.get("version")))
    except Exception:
        rep.skip("http server", "nothing on %s (only needed for URL-based clients; start server/fc26cm_http.py)" % url)


def check_clients(rep, env):
    det = install.detect(env)
    any_reg = False
    for c, v in det["clients"].items():
        if v["registered"]:
            any_reg = True
            rep.ok("client %s" % c, "registered in %s" % v["config"])
        elif v["present"]:
            rep.warn("client %s" % c, "present but not registered (%s)" % v["config"])
    if not any_reg:
        rep.warn("client registration", "no MCP client has the server yet (install.py --client ..., or use the "
                 "generic JSON / HTTP URL / CLI paste mode)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="%s - health check" % install.PRODUCT_NAME)
    ap.add_argument("--le-dir")
    ap.add_argument("--data-dir")
    ap.add_argument("--home")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--http-port", type=int, default=HTTP_PORT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    env = install.Env(args.home)
    state = read_json(install.STATE_PATH) or {}
    data_dir = args.data_dir or state.get("data_dir") or install.resolve_data_dir(env)
    le_dir = args.le_dir or state.get("le_dir")
    rep = Report()
    check_python(rep, os.path.abspath(args.python))
    check_product(rep)
    check_bridge(rep, le_dir, env)
    check_data(rep, data_dir, time.time())
    check_saves(rep, env.saves_dir())
    check_stdio(rep, os.path.abspath(args.python), data_dir)
    check_http(rep, args.http_port)
    check_clients(rep, env)
    if args.json:
        print(json.dumps({"ok": not rep.failed, "checks": rep.rows}, indent=2))
    else:
        for r in rep.rows:
            print("%-4s %-24s %s" % (r["status"], r["check"], r["detail"]))
        print("RESULT %s" % ("FAIL" if rep.failed else "PASS"))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
