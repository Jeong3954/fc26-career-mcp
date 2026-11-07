#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - installer (run by an AI agent or a human; no wizard).

    python installer/install.py --detect [--json]        # look around, write nothing
    python installer/install.py [options]                # install
      --le-dir DIR          Live Editor folder (default: auto-detect)
      --data-dir DIR        bridge data folder (default %USERPROFILE%\\Documents\\FC26CareerMCP)
      --client NAME ...     claude-desktop claude-code cursor windsurf vscode gemini-cli codex lm-studio
                            generic none auto   (default auto = every client found on this PC)
      --python PATH         interpreter to register for the server (default: this one)
      --allow-coexist       install even when another bridge-like script sits in <LE>\\lua\\autorun\\
      --http-autostart      add a Startup entry that runs the local HTTP server (for URL-based clients)
      --home DIR            treat DIR as the user profile (sandbox / tests)
      --dry-run             print what would be done; write nothing
      --json                machine-readable result on stdout
      --yes                 never prompt

Steps: 1 detect  2 copy the bridge Lua into <LE>\\lua\\autorun\\  3 data folder (+config.json redirect)
4 register the stdio server in each client's config by JSON merge (backup first; TOML block for Codex)
5 write installer/install_state.json for uninstall.py.  Standard library only, Python 3.9+.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

PRODUCT_NAME = "FC26 Career MCP"
SERVER_NAME = "fc26-career-mcp"
HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCT_ROOT = os.path.dirname(HERE)
BRIDGE_DIR = os.path.join(PRODUCT_ROOT, "bridge")
BRIDGE_AUTORUN = "fc26cm_bridge_autorun.lua"
BRIDGE_MANUAL = "fc26cm_bridge.lua"
SERVER_PATH = os.path.join(PRODUCT_ROOT, "server", "fc26cm_mcp.py")
HTTP_PATH = os.path.join(PRODUCT_ROOT, "server", "fc26cm_http.py")
STATE_PATH = os.path.join(HERE, "install_state.json")
DEFAULT_LE_DIR = r"C:\FC 26 Live Editor"
HTTP_PORT = 26261
CLIENTS = ("claude-desktop", "claude-code", "cursor", "windsurf", "vscode", "gemini-cli", "codex", "lm-studio")
BRIDGE_MARKERS = ("heartbeat.json", "snapshot.json", "FC26CareerMCP")


# ----------------------------------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------------------------------

class Env:
    """Where things live for this user (overridable with --home for sandboxes)."""

    def __init__(self, home=None):
        self.home = os.path.abspath(home) if home else (os.environ.get("USERPROFILE") or os.path.expanduser("~"))
        if home:
            self.appdata = os.path.join(self.home, "AppData", "Roaming")
            self.localappdata = os.path.join(self.home, "AppData", "Local")
        else:
            self.appdata = os.environ.get("APPDATA") or os.path.join(self.home, "AppData", "Roaming")
            self.localappdata = os.environ.get("LOCALAPPDATA") or os.path.join(self.home, "AppData", "Local")

    def data_root(self):
        return os.path.join(self.home, "Documents", "FC26CareerMCP")

    def saves_dir(self):
        return os.path.join(self.localappdata, "EA SPORTS FC 26", "settings")

    def startup_dir(self):
        return os.path.join(self.appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")

    def client_config(self, client):
        h, a = self.home, self.appdata
        return {
            "claude-desktop": os.path.join(a, "Claude", "claude_desktop_config.json"),
            "claude-code": os.path.join(h, ".claude.json"),
            "cursor": os.path.join(h, ".cursor", "mcp.json"),
            "windsurf": os.path.join(h, ".codeium", "windsurf", "mcp_config.json"),
            "vscode": os.path.join(a, "Code", "User", "mcp.json"),
            "gemini-cli": os.path.join(h, ".gemini", "settings.json"),
            "codex": os.path.join(h, ".codex", "config.toml"),
            "lm-studio": os.path.join(h, ".lmstudio", "mcp.json"),
        }[client]

    def client_present(self, client):
        """Is the client installed / used by this user? (config file, its folder, or its CLI on PATH)"""
        cfg = self.client_config(client)
        if os.path.exists(cfg) or os.path.isdir(os.path.dirname(cfg)):
            return True
        if client == "claude-code":
            return bool(shutil.which("claude")) or os.path.isdir(os.path.join(self.home, ".claude"))
        if client == "codex":
            return bool(shutil.which("codex"))
        if client == "gemini-cli":
            return bool(shutil.which("gemini"))
        if client == "claude-desktop":
            return os.path.isdir(os.path.join(self.localappdata, "AnthropicClaude"))
        if client == "vscode":
            return bool(shutil.which("code")) or os.path.isdir(os.path.join(self.localappdata, "Programs", "Microsoft VS Code"))
        if client == "cursor":
            return os.path.isdir(os.path.join(self.localappdata, "Programs", "cursor"))
        if client == "lm-studio":
            return os.path.isdir(os.path.join(self.localappdata, "LM-Studio"))
        return False


def backup_name(path):
    """Unique '<path>.bak-<timestamp>' (suffix -2, -3 ... if that second is taken)."""
    base = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    name, n = base, 1
    while os.path.exists(name):
        n += 1
        name = "%s-%d" % (base, n)
    return name


def is_store_stub(python):
    return "windowsapps" in python.lower()


def python_ok(python):
    """(ok, detail): real CPython 3.9+ that answers?"""
    try:
        out = subprocess.run([python, "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
                             capture_output=True, text=True, timeout=20)
        major, minor = (int(x) for x in out.stdout.split()[:2])
        return (major, minor) >= (3, 9), "%d.%d" % (major, minor)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ----------------------------------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------------------------------

def scan_for_le(env):
    roots = ["C:\\", "D:\\", "E:\\", env.localappdata, os.environ.get("PROGRAMFILES") or "",
             os.path.join(env.home, "Desktop"), os.path.join(env.home, "Downloads")]
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if "live" in low and "editor" in low:
                path = os.path.join(root, name)
                if os.path.isdir(path) and path not in found:
                    found.append(path)
    return found


def le_info(le_dir):
    """What a Live Editor folder looks like: exe present, lua/autorun, other autorun scripts, conflicts."""
    info = {"path": le_dir, "exists": os.path.isdir(le_dir)}
    if not info["exists"]:
        return info
    try:
        names = os.listdir(le_dir)
    except OSError:
        names = []
    info["has_exe"] = any(n.lower().endswith(".exe") and "live" in n.lower() for n in names)
    autorun = os.path.join(le_dir, "lua", "autorun")
    info["autorun_dir"] = autorun
    info["autorun_exists"] = os.path.isdir(autorun)
    others, conflicts = [], []
    if info["autorun_exists"]:
        for n in sorted(os.listdir(autorun)):
            if not n.lower().endswith(".lua") or n in (BRIDGE_AUTORUN, BRIDGE_MANUAL):
                continue
            p = os.path.join(autorun, n)
            bridge_like = "bridge" in n.lower()
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(200000)
                bridge_like = bridge_like or any(m in head for m in BRIDGE_MARKERS)
            except OSError:
                pass
            others.append(n)
            if bridge_like:
                conflicts.append(n)
    info["other_autorun_scripts"] = others
    info["bridge_conflicts"] = conflicts
    info["ours_installed"] = os.path.isfile(os.path.join(autorun, BRIDGE_AUTORUN))
    return info


def detect(env, le_dir=None, python=None):
    python = python or sys.executable
    ok, pyver = python_ok(python)
    d = {"product_root": PRODUCT_ROOT, "server_path": SERVER_PATH,
         "python": {"path": python, "ok": ok and not is_store_stub(python), "version": pyver,
                    "store_stub": is_store_stub(python)},
         "home": env.home, "appdata": env.appdata,
         "data_root": env.data_root(), "data_dir": resolve_data_dir(env), "saves_dir": env.saves_dir(),
         "saves_present": _saves_present(env.saves_dir())}
    cands = [le_dir] if le_dir else []
    if not le_dir and os.path.isdir(DEFAULT_LE_DIR):
        cands.append(DEFAULT_LE_DIR)
    for c in scan_for_le(env):
        if c not in cands:
            cands.append(c)
    d["live_editor_candidates"] = [le_info(c) for c in cands]
    d["live_editor"] = next((c for c in d["live_editor_candidates"] if c.get("exists")), None)
    d["clients"] = {}
    for c in CLIENTS:
        cfg = env.client_config(c)
        d["clients"][c] = {"present": env.client_present(c), "config": cfg, "config_exists": os.path.isfile(cfg),
                           "registered": is_registered(c, cfg)}
    d["state_file"] = STATE_PATH if os.path.isfile(STATE_PATH) else None
    return d


def _saves_present(saves_dir):
    try:
        return any(n.startswith("Cm") for n in os.listdir(saves_dir))
    except OSError:
        return False


def resolve_data_dir(env):
    cfg = os.path.join(env.data_root(), "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            redirect = json.load(f).get("data_dir")
        if isinstance(redirect, str) and redirect.strip():
            return redirect
    except Exception:
        pass
    return env.data_root()


def is_registered(client, cfg):
    if not os.path.isfile(cfg):
        return False
    try:
        with open(cfg, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return False
    if client == "codex":
        return ("[mcp_servers.%s]" % SERVER_NAME) in raw or ('[mcp_servers."%s"]' % SERVER_NAME) in raw
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return False
    key = "servers" if client == "vscode" else "mcpServers"
    return isinstance(data, dict) and isinstance(data.get(key), dict) and SERVER_NAME in data[key]


# ----------------------------------------------------------------------------------------------
# Client registration (JSON merge with backup; TOML block for Codex)
# ----------------------------------------------------------------------------------------------

def server_entry(client, python):
    entry = {"command": python, "args": [SERVER_PATH]}
    if client in ("vscode", "claude-code"):
        entry = {"type": "stdio", **entry}
    return entry


def json_merge_register(path, key, entry, dry, log):
    data, backup = {}, None
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError as e:
                raise SystemExit("%s is not valid JSON (%s). Fix it or move it away, then run again." % (path, e))
        if not isinstance(data, dict):
            raise SystemExit("%s does not contain a JSON object; not touching it." % path)
        backup = backup_name(path)
        log("  backup: %s" % backup)
        if not dry:
            shutil.copyfile(path, backup)
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
        data[key] = servers
    servers[SERVER_NAME] = entry
    log("  %s: %s[%r] set" % (path, key, SERVER_NAME))
    if not dry:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    return backup


def toml_str(s):
    return json.dumps(s)  # a JSON string literal is a valid TOML basic string for paths


def codex_register(path, python, dry, log):
    block = "\n[mcp_servers.%s]\ncommand = %s\nargs = [%s]\n" % (SERVER_NAME, toml_str(python), toml_str(SERVER_PATH))
    raw, backup = "", None
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        backup = backup_name(path)
        log("  backup: %s" % backup)
        if not dry:
            shutil.copyfile(path, backup)
    if is_registered("codex", path):
        raw = remove_toml_block(raw)
    new = raw.rstrip("\n") + "\n" + block if raw.strip() else block.lstrip("\n")
    log("  %s: [mcp_servers.%s] block written" % (path, SERVER_NAME))
    if not dry:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, path)
    return backup


def remove_toml_block(raw):
    """Drop our [mcp_servers.<name>] table (up to the next table header)."""
    out, skipping = [], False
    for line in raw.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("["):
            skipping = s in ("[mcp_servers.%s]" % SERVER_NAME, '[mcp_servers."%s"]' % SERVER_NAME)
        if not skipping:
            out.append(line)
    return "".join(out)


def register_client(client, env, python, dry, log, use_cli=True):
    """Returns a state record {config, backup, method}."""
    cfg = env.client_config(client)
    entry = server_entry(client, python)
    log("Client %s:" % client)
    if client == "claude-code" and use_cli and shutil.which("claude") and not dry:
        cmd = ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", python, SERVER_PATH]
        log("  run: " + " ".join(cmd))
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if cp.returncode == 0:
                return {"config": cfg, "backup": None, "method": "claude mcp add --scope user"}
            log("  claude mcp add failed (%s); falling back to a JSON merge" % (cp.stderr or cp.stdout).strip()[:200])
        except Exception as e:
            log("  claude mcp add failed (%s); falling back to a JSON merge" % e)
    if client == "codex" and use_cli and shutil.which("codex") and not dry:
        cmd = ["codex", "mcp", "add", SERVER_NAME, "--", python, SERVER_PATH]
        log("  run: " + " ".join(cmd))
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if cp.returncode == 0:
                return {"config": cfg, "backup": None, "method": "codex mcp add"}
            log("  codex mcp add failed (%s); falling back to a TOML block" % (cp.stderr or cp.stdout).strip()[:200])
        except Exception as e:
            log("  codex mcp add failed (%s); falling back to a TOML block" % e)
    if client == "codex":
        backup = codex_register(cfg, python, dry, log)
        return {"config": cfg, "backup": backup, "method": "toml"}
    key = "servers" if client == "vscode" else "mcpServers"
    backup = json_merge_register(cfg, key, entry, dry, log)
    return {"config": cfg, "backup": backup, "method": "json-merge:" + key}


def generic_snippets(python):
    return {
        "stdio_json": {"mcpServers": {SERVER_NAME: {"command": python, "args": [SERVER_PATH]}}},
        "streamable_http_url": "http://127.0.0.1:%d/mcp" % HTTP_PORT,
        "rest_openapi": "http://127.0.0.1:%d/openapi.json" % HTTP_PORT,
        "http_server_command": [python, HTTP_PATH],
        "cli_paste": [python, os.path.join(PRODUCT_ROOT, "server", "fc26cm_cli.py"), "all"],
    }


def http_autostart_path(env):
    return os.path.join(env.startup_dir(), "fc26-career-mcp-http.cmd")


def write_http_autostart(env, python, dry, log):
    path = http_autostart_path(env)
    pyw = python[:-10] + "pythonw.exe" if python.lower().endswith("python.exe") else python
    content = ('@echo off\r\nrem %s - local HTTP server (MCP Streamable HTTP + REST), localhost only\r\n'
               'start "" /min "%s" "%s" --port %d\r\n' % (PRODUCT_NAME, pyw, HTTP_PATH, HTTP_PORT))
    log("HTTP autostart: %s" % path)
    if not dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return path


# ----------------------------------------------------------------------------------------------
# Installer
# ----------------------------------------------------------------------------------------------

class Installer:
    def __init__(self, args):
        self.args = args
        self.dry = args.dry_run
        self.env = Env(args.home)
        self.lines = []
        self.state = {"product": PRODUCT_NAME, "server_name": SERVER_NAME,
                      "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "product_root": PRODUCT_ROOT,
                      "python": args.python, "server_path": SERVER_PATH, "home": self.env.home,
                      "clients": {}, "dry_run": self.dry}

    def log(self, msg=""):
        self.lines.append(msg)
        if not self.args.json:
            print(msg)

    # ---- step 1: Live Editor folder --------------------------------------
    def locate_le(self, det):
        if self.args.le_dir:
            le = os.path.abspath(self.args.le_dir)
            if not os.path.isdir(le):
                if self.dry:
                    self.log("[dry-run] Live Editor folder does not exist yet: %s" % le)
                    return le
                raise SystemExit("Live Editor folder not found: %s" % le)
            return le
        cands = [c["path"] for c in det["live_editor_candidates"] if c.get("exists")]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1 and not self.args.yes and sys.stdin.isatty():
            self.log("Several possible Live Editor folders:")
            for i, c in enumerate(cands, 1):
                self.log("  %d) %s" % (i, c))
            pick = input("Pick a number (or paste a folder path): ").strip().strip('"')
            if pick.isdigit() and 1 <= int(pick) <= len(cands):
                return cands[int(pick) - 1]
            if os.path.isdir(pick):
                return os.path.abspath(pick)
        if len(cands) > 1:
            raise SystemExit("Several Live Editor folders found: %s. Run again with --le-dir \"<folder>\"."
                             % "; ".join(cands))
        raise SystemExit("Could not find the FC 26 Live Editor folder.\nInstall Live Editor first (default: %s), "
                         "or run again with --le-dir \"<folder>\"." % DEFAULT_LE_DIR)

    # ---- step 2: Lua files -----------------------------------------------
    def install_bridge(self, le_dir):
        info = le_info(le_dir)
        if info.get("bridge_conflicts") and not self.args.allow_coexist:
            raise SystemExit(
                "Another bridge-like script is already in %s: %s.\n"
                "Ask the user whether to keep it (then run again with --allow-coexist: both will run; two "
                "bridges double the per-day reads), or let them move it away first. Nothing was changed."
                % (info["autorun_dir"], ", ".join(info["bridge_conflicts"])))
        autorun_dir = os.path.join(le_dir, "lua", "autorun")
        src_auto = os.path.join(BRIDGE_DIR, BRIDGE_AUTORUN)
        src_manual = os.path.join(BRIDGE_DIR, BRIDGE_MANUAL)
        for src in (src_auto, src_manual):
            if not os.path.isfile(src):
                msg = "Bridge file missing from the package: %s" % src
                if self.dry:
                    self.log("[dry-run] " + msg)
                else:
                    raise SystemExit(msg)
        dst_auto = os.path.join(autorun_dir, BRIDGE_AUTORUN)
        dst_manual = os.path.join(autorun_dir, BRIDGE_MANUAL)
        self.log("Lua autorun : %s" % dst_auto)
        self.log("Lua manual  : %s" % dst_manual)
        if info.get("other_autorun_scripts"):
            self.log("Other autorun scripts kept untouched: %s" % ", ".join(info["other_autorun_scripts"]))
        if not self.dry:
            os.makedirs(autorun_dir, exist_ok=True)
            for src, dst in ((src_auto, dst_auto), (src_manual, dst_manual)):
                if os.path.isfile(dst):
                    b = backup_name(dst)
                    shutil.copyfile(dst, b)
                    self.log("  previous copy backed up: %s" % b)
                shutil.copyfile(src, dst)
        self.state.update({"le_dir": le_dir, "autorun_path": dst_auto, "manual_path": dst_manual,
                           "coexisting_scripts": info.get("bridge_conflicts") or []})

    # ---- step 3: data folder ---------------------------------------------
    def install_data_dir(self):
        root = self.env.data_root()
        data_dir = os.path.abspath(self.args.data_dir) if self.args.data_dir else root
        custom = os.path.normcase(data_dir) != os.path.normcase(root)
        self.log("Data folder : %s%s" % (data_dir, " (custom)" if custom else ""))
        self.state["data_dir"] = data_dir
        self.state["data_root"] = root
        if not self.dry:
            os.makedirs(data_dir, exist_ok=True)
        if custom:
            cfg = os.path.join(root, "config.json")
            self.log("Redirect    : %s -> {\"data_dir\": %s}" % (cfg, json.dumps(data_dir)))
            if not self.dry:
                os.makedirs(root, exist_ok=True)
                with open(cfg, "w", encoding="utf-8") as f:
                    json.dump({"data_dir": data_dir}, f, indent=2)
            self.state["config_json"] = cfg

    # ---- step 4: clients -------------------------------------------------
    def pick_clients(self, det):
        wanted = self.args.client or ["auto"]
        if "none" in wanted:
            return []
        if "auto" in wanted:
            found = [c for c in CLIENTS if det["clients"][c]["present"]]
            wanted = [c for c in wanted if c != "auto"] + found
        out = []
        for c in wanted:
            if c == "generic":
                continue
            if c not in CLIENTS:
                raise SystemExit("unknown client %r (choose from %s, generic, none, auto)" % (c, ", ".join(CLIENTS)))
            if c not in out:
                out.append(c)
        return out

    def register_clients(self, clients):
        for c in clients:
            rec = register_client(c, self.env, self.args.python, self.dry, self.log, use_cli=not self.args.no_cli)
            self.state["clients"][c] = rec

    # ---- step 5: state ---------------------------------------------------
    def write_state(self):
        self.log("Install state: %s" % STATE_PATH)
        if self.dry:
            return
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def run(self):
        self.log("%s installer%s" % (PRODUCT_NAME, " (dry run, nothing is written)" if self.dry else ""))
        det = detect(self.env, self.args.le_dir, self.args.python)
        self.log("Product root: %s" % PRODUCT_ROOT)
        self.log("Python      : %s (%s)" % (self.args.python, det["python"]["version"]))
        if det["python"]["store_stub"]:
            raise SystemExit("The registered Python would be the Microsoft Store stub (%s). Install real Python "
                             "(python.org or `winget install Python.Python.3.13`) and pass --python <path>."
                             % self.args.python)
        if not det["python"]["ok"] and not self.dry:
            raise SystemExit("Python 3.9+ is required for the server; %s reports %s" % (self.args.python,
                                                                                         det["python"]["version"]))
        if not os.path.isfile(SERVER_PATH):
            self.log("WARNING: server file not found: %s (the package looks incomplete)" % SERVER_PATH)
        if os.name != "nt" and not self.args.home and not self.dry:
            self.log("WARNING: not Windows; installing into the given folders anyway.")
        le_dir = self.locate_le(det)
        self.log("Live Editor : %s" % le_dir)
        self.install_bridge(le_dir)
        self.install_data_dir()
        clients = self.pick_clients(det)
        self.log("Clients     : %s" % (", ".join(clients) or "none"))
        self.register_clients(clients)
        if self.args.http_autostart:
            self.state["http_autostart"] = write_http_autostart(self.env, self.args.python, self.dry, self.log)
        self.state["snippets"] = generic_snippets(self.args.python)
        self.write_state()
        self.print_checklist(clients)
        if self.args.json:
            print(json.dumps({"ok": True, "dry_run": self.dry, "state": self.state, "log": self.lines}, indent=2))
        return 0

    def print_checklist(self, clients):
        self.log()
        self.log("Done. Next steps (the human does these):")
        self.log("  1. Start FC 26 through Live Editor as usual and load an OFFLINE career.")
        self.log("  2. Open the career hub (the main career screen) and wait about 10 seconds.")
        self.log("  3. snapshot.json should appear in: %s" % self.state["data_dir"])
        self.log("     (if not: Live Editor -> Lua Engine -> load %s -> Execute/F5 while the hub is on screen)"
                 % self.state.get("manual_path", BRIDGE_MANUAL))
        if clients:
            self.log("  4. Restart these clients completely so they load the new server: %s" % ", ".join(clients))
        self.log("  5. Health check: python installer\\healthcheck.py")
        self.log("  Any other client: stdio command = %s %s ; URL (after starting server\\fc26cm_http.py) = %s"
                 % (self.args.python, SERVER_PATH, "http://127.0.0.1:%d/mcp" % HTTP_PORT))
        self.log("  Uninstall: python installer\\uninstall.py (keeps the data folder unless --purge).")


def build_parser():
    p = argparse.ArgumentParser(description="Install %s" % PRODUCT_NAME)
    p.add_argument("--detect", action="store_true", help="detect Live Editor, Python, clients; write nothing")
    p.add_argument("--le-dir", help="FC 26 Live Editor folder (default: %s or auto-detect)" % DEFAULT_LE_DIR)
    p.add_argument("--data-dir", help="data folder (default: %%USERPROFILE%%\\Documents\\FC26CareerMCP)")
    p.add_argument("--python", default=sys.executable, help="Python interpreter to register for the MCP server")
    p.add_argument("--client", nargs="*", help="clients to register (default auto): %s generic none auto"
                   % " ".join(CLIENTS))
    p.add_argument("--allow-coexist", action="store_true", help="install next to another bridge-like autorun script")
    p.add_argument("--http-autostart", action="store_true", help="Startup entry for the local HTTP server")
    p.add_argument("--no-cli", action="store_true", help="never call `claude`/`codex` CLIs; edit configs directly")
    p.add_argument("--home", help="treat this folder as the user profile (sandbox / tests)")
    p.add_argument("--dry-run", action="store_true", help="print what would be done; write nothing")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--yes", action="store_true", help="never prompt")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.python = os.path.abspath(args.python)
    if args.detect:
        det = detect(Env(args.home), args.le_dir, args.python)
        if args.json:
            print(json.dumps(det, indent=2))
        else:
            le = det["live_editor"]
            print("Python      : %s (%s%s)" % (det["python"]["path"], det["python"]["version"],
                                                ", STORE STUB" if det["python"]["store_stub"] else ""))
            print("Live Editor : %s" % (le["path"] if le else "NOT FOUND (pass --le-dir)"))
            if le:
                print("  autorun   : %s (ours installed: %s)" % (le["autorun_dir"], le["ours_installed"]))
                print("  other autorun scripts: %s" % (", ".join(le["other_autorun_scripts"]) or "none"))
                print("  bridge conflicts     : %s" % (", ".join(le["bridge_conflicts"]) or "none"))
            print("Data folder : %s" % det["data_dir"])
            print("Saves       : %s (saves present: %s)" % (det["saves_dir"], det["saves_present"]))
            for c, v in det["clients"].items():
                print("Client %-14s present=%-5s registered=%-5s %s" % (c, v["present"], v["registered"], v["config"]))
        return 0
    return Installer(args).run()


if __name__ == "__main__":
    sys.exit(main())
