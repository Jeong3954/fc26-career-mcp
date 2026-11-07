"""Tests for installer/install.py, uninstall.py and healthcheck.py.

Runs against a temporary copy of the product so nothing outside tmp is touched (the real
server/bridge files are used; --home redirects every client config into the sandbox).
Run from the product root:  python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PRODUCT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_NAME = "fc26-career-mcp"


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fc26cm-test-")
        self.product = os.path.join(self.tmp, "product")
        shutil.copytree(PRODUCT_ROOT, self.product,
                        ignore=shutil.ignore_patterns("__pycache__", "install_state.json", "tests", ".git", "tmp"))
        self.le_dir = os.path.join(self.tmp, "FC 26 Live Editor")
        os.makedirs(os.path.join(self.le_dir, "lua"))
        self.data_dir = os.path.join(self.tmp, "data")
        self.home = os.path.join(self.tmp, "home")
        self.appdata = os.path.join(self.home, "AppData", "Roaming")
        self.claude_cfg = os.path.join(self.appdata, "Claude", "claude_desktop_config.json")
        os.makedirs(os.path.dirname(self.claude_cfg))
        with open(self.claude_cfg, "w") as f:
            json.dump({"mcpServers": {"other-server": {"command": "x", "args": ["y"]}}, "theme": "dark"}, f)
        self.codex_cfg = os.path.join(self.home, ".codex", "config.toml")
        os.makedirs(os.path.dirname(self.codex_cfg))
        with open(self.codex_cfg, "w") as f:
            f.write('[model]\nname = "x"\n\n[mcp_servers.other]\ncommand = "o"\n')
        os.makedirs(os.path.join(self.appdata, "Code", "User"))  # vscode present, no mcp.json yet
        self.state = os.path.join(self.product, "installer", "install_state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script, *args):
        cmd = [sys.executable, os.path.join(self.product, "installer", script)] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def install_args(self, *extra):
        return ("--le-dir", self.le_dir, "--home", self.home, "--data-dir", self.data_dir, "--no-cli",
                "--yes") + extra

    def autorun(self, name):
        return os.path.join(self.le_dir, "lua", "autorun", name)

    def test_detect_json(self):
        r = self.run_py("install.py", "--detect", "--json", "--home", self.home, "--le-dir", self.le_dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["live_editor"]["path"], self.le_dir)
        self.assertTrue(d["clients"]["claude-desktop"]["present"])
        self.assertTrue(d["clients"]["codex"]["present"])
        self.assertTrue(d["clients"]["vscode"]["present"])
        self.assertFalse(d["clients"]["windsurf"]["present"])
        self.assertFalse(d["clients"]["claude-desktop"]["registered"])
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge_autorun.lua")))

    def test_dry_run_writes_nothing(self):
        r = self.run_py("install.py", *self.install_args("--dry-run"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge_autorun.lua")))
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(os.path.exists(self.data_dir))
        with open(self.claude_cfg) as f:
            self.assertNotIn(SERVER_NAME, json.load(f)["mcpServers"])
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.claude_cfg)) if ".bak-" in n], [])

    def test_install_auto_then_uninstall(self):
        r = self.run_py("install.py", *self.install_args())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(self.autorun("fc26cm_bridge_autorun.lua")))
        self.assertTrue(os.path.isfile(self.autorun("fc26cm_bridge.lua")))
        self.assertTrue(os.path.isdir(self.data_dir))
        cfg = os.path.join(self.home, "Documents", "FC26CareerMCP", "config.json")
        with open(cfg) as f:
            self.assertEqual(json.load(f)["data_dir"], self.data_dir)

        # Claude Desktop: merged, others intact, one backup
        with open(self.claude_cfg) as f:
            data = json.load(f)
        self.assertIn("other-server", data["mcpServers"])
        self.assertEqual(data["theme"], "dark")
        entry = data["mcpServers"][SERVER_NAME]
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(entry["args"], [os.path.join(self.product, "server", "fc26cm_mcp.py")])
        self.assertEqual(len([n for n in os.listdir(os.path.dirname(self.claude_cfg)) if ".bak-" in n]), 1)
        # Codex: TOML block appended, other tables intact
        with open(self.codex_cfg) as f:
            toml = f.read()
        self.assertIn("[mcp_servers.%s]" % SERVER_NAME, toml)
        self.assertIn("[mcp_servers.other]", toml)
        self.assertIn('name = "x"', toml)
        # VS Code: servers key with type stdio, created from nothing
        with open(os.path.join(self.appdata, "Code", "User", "mcp.json")) as f:
            vs = json.load(f)["servers"][SERVER_NAME]
        self.assertEqual(vs["type"], "stdio")
        # claude-code present (its dir .claude.json parent = home) -> registered in ~/.claude.json
        with open(os.path.join(self.home, ".claude.json")) as f:
            self.assertIn(SERVER_NAME, json.load(f)["mcpServers"])
        # not-present clients untouched
        self.assertFalse(os.path.exists(os.path.join(self.home, ".codeium")))

        with open(self.state) as f:
            state = json.load(f)
        self.assertEqual(state["le_dir"], self.le_dir)
        self.assertEqual(set(state["clients"]), {"claude-desktop", "claude-code", "vscode", "codex"})
        self.assertIn("snapshot.json", r.stdout)

        # re-install: previous Lua backed up, config entries stay single
        r = self.run_py("install.py", *self.install_args())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any(".bak-" in n for n in os.listdir(os.path.dirname(self.autorun("x")))))
        with open(self.codex_cfg) as f:
            self.assertEqual(f.read().count("[mcp_servers.%s]" % SERVER_NAME), 1)

        # uninstall dry run keeps everything
        r = self.run_py("uninstall.py", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(self.autorun("fc26cm_bridge_autorun.lua")))
        self.assertTrue(os.path.isfile(self.state))

        r = self.run_py("uninstall.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge_autorun.lua")))
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge.lua")))
        with open(self.claude_cfg) as f:
            data = json.load(f)
        self.assertNotIn(SERVER_NAME, data["mcpServers"])
        self.assertIn("other-server", data["mcpServers"])
        with open(self.codex_cfg) as f:
            toml = f.read()
        self.assertNotIn(SERVER_NAME, toml)
        self.assertIn("[mcp_servers.other]", toml)
        with open(os.path.join(self.appdata, "Code", "User", "mcp.json")) as f:
            self.assertNotIn(SERVER_NAME, json.load(f)["servers"])
        self.assertTrue(os.path.isdir(self.data_dir), "data folder is kept without --purge")
        self.assertFalse(os.path.exists(self.state))

    def test_bridge_conflict_blocks_unless_allowed(self):
        os.makedirs(os.path.join(self.le_dir, "lua", "autorun"))
        with open(self.autorun("le_bridge_ro_auto.lua"), "w") as f:
            f.write('-- personal bridge\nlocal p = "heartbeat.json"\n')
        r = self.run_py("install.py", *self.install_args("--client", "none"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("le_bridge_ro_auto.lua", r.stderr)
        self.assertIn("--allow-coexist", r.stderr)
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge_autorun.lua")))
        r = self.run_py("install.py", *self.install_args("--client", "none", "--allow-coexist"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(self.autorun("fc26cm_bridge_autorun.lua")))
        self.assertTrue(os.path.isfile(self.autorun("le_bridge_ro_auto.lua")), "other script untouched")
        with open(self.claude_cfg) as f:
            self.assertNotIn(SERVER_NAME, json.load(f)["mcpServers"])

    def test_specific_client_and_http_autostart(self):
        r = self.run_py("install.py", *self.install_args("--client", "cursor", "--http-autostart"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.home, ".cursor", "mcp.json")) as f:
            self.assertIn(SERVER_NAME, json.load(f)["mcpServers"])
        with open(self.claude_cfg) as f:
            self.assertNotIn(SERVER_NAME, json.load(f)["mcpServers"])
        startup = os.path.join(self.appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
                               "fc26-career-mcp-http.cmd")
        self.assertTrue(os.path.isfile(startup))
        with open(startup) as f:
            self.assertIn("fc26cm_http.py", f.read())
        r = self.run_py("uninstall.py", "--purge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(startup))
        self.assertFalse(os.path.exists(self.data_dir))

    def test_uninstall_without_state_finds_things(self):
        r = self.run_py("install.py", *self.install_args("--client", "claude-desktop"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        os.remove(self.state)
        r = self.run_py("uninstall.py", "--home", self.home, "--le-dir", self.le_dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.autorun("fc26cm_bridge_autorun.lua")))
        with open(self.claude_cfg) as f:
            self.assertNotIn(SERVER_NAME, json.load(f)["mcpServers"])

    def test_invalid_client_json_refused(self):
        with open(self.claude_cfg, "w") as f:
            f.write("{not json")
        r = self.run_py("install.py", *self.install_args("--client", "claude-desktop"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not valid JSON", r.stderr)
        with open(self.claude_cfg) as f:
            self.assertEqual(f.read(), "{not json")

    def test_missing_le_dir_fails_with_message(self):
        r = self.run_py("install.py", "--le-dir", os.path.join(self.tmp, "nope"), "--home", self.home, "--yes",
                        "--data-dir", self.data_dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Live Editor folder not found", r.stderr)

    def test_healthcheck_lines(self):
        r = self.run_py("install.py", *self.install_args("--client", "claude-desktop"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        fixtures = os.path.join(PRODUCT_ROOT, "tests", "fixtures", "manager")
        for n in os.listdir(fixtures):
            shutil.copyfile(os.path.join(fixtures, n), os.path.join(self.data_dir, n))
        r = self.run_py("healthcheck.py", "--home", self.home, "--le-dir", self.le_dir, "--data-dir", self.data_dir,
                        "--http-port", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[-1], "RESULT PASS")
        status = {ln.split()[1] + " " + ln.split()[2]: ln.split()[0] for ln in lines[:-1] if ln.split()}
        self.assertEqual(status["python"] if "python" in status else [s for k, s in status.items() if k.startswith("python")][0], "PASS")
        joined = r.stdout
        self.assertIn("PASS product files", joined)
        self.assertIn("PASS live editor folder", joined)
        self.assertIn("PASS bridge installed", joined)
        self.assertIn("PASS stdio server", joined)
        self.assertIn("SKIP http server", joined)
        self.assertIn("PASS client claude-desktop", joined)
        self.assertIn("WARN bridge heartbeat", joined)
        r = self.run_py("healthcheck.py", "--home", self.home, "--le-dir", self.le_dir, "--data-dir", self.data_dir,
                        "--http-port", "1", "--json")
        d = json.loads(r.stdout)
        self.assertTrue(d["ok"])
        self.assertTrue(any(c["check"] == "stdio server" and c["status"] == "PASS" for c in d["checks"]))

    def test_healthcheck_fails_without_bridge(self):
        r = self.run_py("healthcheck.py", "--home", self.home, "--le-dir", self.le_dir, "--data-dir", self.data_dir,
                        "--http-port", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL bridge installed", r.stdout)
        self.assertIn("RESULT FAIL", r.stdout)


if __name__ == "__main__":
    unittest.main()
