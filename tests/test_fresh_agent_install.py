"""Simulated fresh agent install: follows ONLY the executable steps of INSTALL_FOR_AI.md.

The "agent" parses the fenced blocks tagged `fc26cm-step` (install flow) and `fc26cm-rollback`
(uninstall) from INSTALL_FOR_AI.md, substitutes {PY} / {PRODUCT}, appends the sandbox flags the
guide allows (--home / --le-dir / --data-dir plus --no-cli), and runs them in order in a temp
sandbox: a fake Live Editor folder, a fake user profile with a Claude Desktop config and a Cursor
folder, no game. Expectations are the ones the guide states for each step.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(ROOT, "INSTALL_FOR_AI.md")
FENCE = re.compile(r"```sh (fc26cm-[a-z]+)\n(.*?)```", re.S)


def guide_steps():
    with open(GUIDE, encoding="utf-8") as f:
        text = f.read()
    steps = {}
    for tag, body in FENCE.findall(text):
        cmds = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        steps.setdefault(tag, []).extend(cmds)
    return steps


class FreshAgentInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fc26cm-agent-")
        self.product = os.path.join(self.tmp, "fc26-career-mcp")
        shutil.copytree(ROOT, self.product,
                        ignore=shutil.ignore_patterns("__pycache__", "install_state.json", "tests", ".git"))
        self.home = os.path.join(self.tmp, "home")
        self.le_dir = os.path.join(self.tmp, "FC 26 Live Editor")
        os.makedirs(os.path.join(self.le_dir, "lua", "autorun"))
        with open(os.path.join(self.le_dir, "lua", "autorun", "unrelated_tool.lua"), "w") as f:
            f.write("-- some other Live Editor script\n")
        self.claude_cfg = os.path.join(self.home, "AppData", "Roaming", "Claude", "claude_desktop_config.json")
        os.makedirs(os.path.dirname(self.claude_cfg))
        with open(self.claude_cfg, "w") as f:
            json.dump({"mcpServers": {"keep-me": {"command": "k"}}}, f)
        os.makedirs(os.path.join(self.home, ".cursor"))
        self.data_dir = os.path.join(self.home, "Documents", "FC26CareerMCP")
        self.sandbox_flags = ["--home", self.home, "--le-dir", self.le_dir]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_step(self, cmd):
        cmd = cmd.replace("{PY}", sys.executable).replace("{PRODUCT}", self.product)
        argv = shlex.split(cmd)
        if any(s in argv[1] for s in ("install.py", "uninstall.py", "healthcheck.py")):
            argv += self.sandbox_flags
        if argv[1].endswith(os.sep + "install.py"):
            argv.append("--no-cli")
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return cp

    def test_guide_has_the_steps(self):
        steps = guide_steps()
        self.assertIn("fc26cm-step", steps)
        self.assertIn("fc26cm-rollback", steps)
        self.assertEqual(len(steps["fc26cm-step"]), 3, steps)  # detect, install, healthcheck
        self.assertIn("--detect", steps["fc26cm-step"][0])
        self.assertIn("install.py", steps["fc26cm-step"][1])
        self.assertIn("healthcheck.py", steps["fc26cm-step"][2])

    def test_follow_the_guide(self):
        steps = guide_steps()
        detect, install, health = steps["fc26cm-step"]

        # Step 3: detect writes nothing and reports what the guide's table describes.
        cp = self.run_step(detect)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("Live Editor : %s" % self.le_dir, cp.stdout)
        self.assertIn("other autorun scripts: unrelated_tool.lua", cp.stdout)
        self.assertIn("bridge conflicts     : none", cp.stdout)
        self.assertIn("Client claude-desktop present=True", cp.stdout)
        self.assertIn("Client cursor         present=True", cp.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.le_dir, "lua", "autorun", "fc26cm_bridge_autorun.lua")))

        # Step 4: install with --client auto.
        cp = self.run_step(install)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("Done. Next steps", cp.stdout)
        autorun = os.path.join(self.le_dir, "lua", "autorun")
        self.assertTrue(os.path.isfile(os.path.join(autorun, "fc26cm_bridge_autorun.lua")))
        self.assertTrue(os.path.isfile(os.path.join(autorun, "unrelated_tool.lua")), "never delete user scripts")
        self.assertTrue(os.path.isdir(self.data_dir))
        with open(self.claude_cfg) as f:
            cfg = json.load(f)
        self.assertIn("fc26-career-mcp", cfg["mcpServers"])
        self.assertIn("keep-me", cfg["mcpServers"])
        self.assertTrue(any(".bak-" in n for n in os.listdir(os.path.dirname(self.claude_cfg))), "backup first")
        with open(os.path.join(self.home, ".cursor", "mcp.json")) as f:
            self.assertIn("fc26-career-mcp", json.load(f)["mcpServers"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".codeium")), "absent clients untouched")

        # Step 5: health check. Before the game ran: FAIL-free, with the WARNs the guide lists as normal.
        cp = self.run_step(health + " --http-port 1")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue(cp.stdout.strip().endswith("RESULT PASS"), cp.stdout)
        self.assertNotIn("FAIL ", cp.stdout)
        for normal_warn in ("WARN bridge status.json", "WARN snapshot.json", "WARN save folder"):
            self.assertIn(normal_warn, cp.stdout)
        self.assertIn("PASS stdio server", cp.stdout)
        self.assertIn("PASS client claude-desktop", cp.stdout)

        # Section 7 simulated: the game ran once -> bridge files appear -> WARNs become PASS.
        fixtures = os.path.join(ROOT, "tests", "fixtures", "player")
        for n in os.listdir(fixtures):
            shutil.copyfile(os.path.join(fixtures, n), os.path.join(self.data_dir, n))
        cp = self.run_step(health + " --http-port 1")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("PASS snapshot.json", cp.stdout)
        self.assertIn("mode player", cp.stdout)

        # Paste mode for a non-MCP assistant (section 6) works on the installed copy.
        cp = subprocess.run([sys.executable, os.path.join(self.product, "server", "fc26cm_cli.py"), "career",
                             "--data-dir", self.data_dir], capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("CAREER mode=player", cp.stdout)

        # Section 8: rollback (dry run then real).
        dry, real = steps["fc26cm-rollback"]
        cp = self.run_step(dry)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue(os.path.isfile(os.path.join(autorun, "fc26cm_bridge_autorun.lua")))
        cp = self.run_step(real)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertFalse(os.path.exists(os.path.join(autorun, "fc26cm_bridge_autorun.lua")))
        self.assertTrue(os.path.isfile(os.path.join(autorun, "unrelated_tool.lua")))
        with open(self.claude_cfg) as f:
            cfg = json.load(f)
        self.assertNotIn("fc26-career-mcp", cfg["mcpServers"])
        self.assertIn("keep-me", cfg["mcpServers"])
        self.assertTrue(os.path.isdir(self.data_dir), "data kept without --purge")

    def test_conflict_branch_asks_the_human(self):
        with open(os.path.join(self.le_dir, "lua", "autorun", "le_bridge_ro_auto.lua"), "w") as f:
            f.write("-- personal read-only bridge writing snapshot.json\n")
        steps = guide_steps()
        cp = self.run_step(steps["fc26cm-step"][0])
        self.assertIn("bridge conflicts     : le_bridge_ro_auto.lua", cp.stdout)
        cp = self.run_step(steps["fc26cm-step"][1])
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("Ask the user", cp.stderr)
        self.assertIn("Nothing was changed", cp.stderr)
        with open(self.claude_cfg) as f:
            self.assertNotIn("fc26-career-mcp", json.load(f)["mcpServers"])
        cp = self.run_step(steps["fc26cm-step"][1] + " --allow-coexist")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.le_dir, "lua", "autorun", "le_bridge_ro_auto.lua")))


if __name__ == "__main__":
    unittest.main()
