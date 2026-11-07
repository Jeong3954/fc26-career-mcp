#!/usr/bin/env python3
"""[GLOUNDEV] FC26 Career MCP - uninstaller.

Reverses install.py using installer/install_state.json:
  - removes the autorun and manual Lua copies from the Live Editor folder (other scripts untouched)
  - removes the server entry from every client config it was added to (backup first; TOML block for Codex)
  - removes the HTTP autostart entry when one was made
  - keeps the data folder unless --purge
  - deletes install_state.json

    python installer/uninstall.py [--dry-run] [--purge] [--state FILE]

Without a state file it still removes the Lua files and client entries it can find (--home / --le-dir).
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import install  # noqa: E402  (shared helpers: Env, backup_name, remove_toml_block, le_info)

SERVER_NAME = install.SERVER_NAME
STATE_PATH = install.STATE_PATH


def log(msg=""):
    print(msg)


def remove_file(path, dry):
    if not path:
        return
    if os.path.isfile(path):
        log("Remove: %s" % path)
        if not dry:
            os.remove(path)
    else:
        log("Already gone: %s" % path)


def remove_client_entry(client, path, dry):
    if not path or not os.path.isfile(path):
        log("%s: config not found, nothing to remove: %s" % (client, path))
        return
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if client == "codex":
        if not install.is_registered("codex", path):
            log("%s: no [mcp_servers.%s] block in %s" % (client, SERVER_NAME, path))
            return
        backup = install.backup_name(path)
        log("%s: backup %s; remove [mcp_servers.%s] block" % (client, backup, SERVER_NAME))
        if dry:
            return
        shutil.copyfile(path, backup)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(install.remove_toml_block(raw))
        os.replace(tmp, path)
        return
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        log("WARNING: %s is not valid JSON (%s); left untouched." % (path, e))
        return
    key = "servers" if client == "vscode" else "mcpServers"
    servers = data.get(key) if isinstance(data, dict) else None
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        log("%s: no %r entry in %s" % (client, SERVER_NAME, path))
        return
    backup = install.backup_name(path)
    log("%s: backup %s; remove %s[%r]" % (client, backup, key, SERVER_NAME))
    if dry:
        return
    shutil.copyfile(path, backup)
    del servers[SERVER_NAME]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description="Uninstall %s" % install.PRODUCT_NAME)
    p.add_argument("--state", default=STATE_PATH, help="install_state.json written by install.py")
    p.add_argument("--purge", action="store_true", help="also delete the data folder (your exported JSON files)")
    p.add_argument("--dry-run", action="store_true", help="print what would be done; remove nothing")
    p.add_argument("--home", help="user profile folder (sandbox / tests; used when there is no state file)")
    p.add_argument("--le-dir", help="Live Editor folder (used when there is no state file)")
    args = p.parse_args(argv)
    dry = args.dry_run

    state = {}
    if os.path.isfile(args.state):
        with open(args.state, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        log("No install state at %s: removing what can be found." % args.state)
        env = install.Env(args.home)
        det = install.detect(env, args.le_dir)
        le = det.get("live_editor")
        if le:
            state["autorun_path"] = os.path.join(le["autorun_dir"], install.BRIDGE_AUTORUN)
            state["manual_path"] = os.path.join(le["autorun_dir"], install.BRIDGE_MANUAL)
        state["clients"] = {c: {"config": v["config"]} for c, v in det["clients"].items() if v["registered"]}
        state["http_autostart"] = install.http_autostart_path(env)
        state["data_dir"] = det["data_dir"]
        state["data_root"] = det["data_root"]

    log("Uninstalling %s%s" % (install.PRODUCT_NAME, " (dry run)" if dry else ""))
    remove_file(state.get("autorun_path"), dry)
    remove_file(state.get("manual_path"), dry)
    for client, rec in (state.get("clients") or {}).items():
        remove_client_entry(client, (rec or {}).get("config"), dry)
    if state.get("http_autostart"):
        remove_file(state["http_autostart"], dry)

    data_dir, data_root = state.get("data_dir"), state.get("data_root")
    if args.purge:
        for d in (data_dir, data_root):
            if d and os.path.isdir(d):
                log("Purge data folder: %s" % d)
                if not dry:
                    shutil.rmtree(d, ignore_errors=True)
    else:
        log("Data folder kept: %s (run with --purge to delete it)" % data_dir)

    if os.path.isfile(args.state):
        log("Remove: %s" % args.state)
        if not dry:
            os.remove(args.state)
    log("Done. Clients registered through their own CLI may also need: claude mcp remove %s / codex mcp remove %s"
        % (SERVER_NAME, SERVER_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
