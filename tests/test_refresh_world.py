"""fc26_refresh_world (1.0.5): the server writes a read_now request, a fake bridge answers, the tool returns the
result; old bridges, unfinished reads and timeouts are handled without touching anything else."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import fc26cm_core as core  # noqa: E402
import fc26cm_cli as cli  # noqa: E402


class RefreshWorldTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.beat("1.0.5")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def beat(self, bridge):
        with open(os.path.join(self.dir, "heartbeat.json"), "w", encoding="utf-8") as f:
            json.dump({"bridge": bridge, "unix": time.time(), "reason": "read_now_waiting_hub_settling"}, f)

    def call(self, **args):
        return core.call_tool("fc26_refresh_world", args, data_dir=self.dir, save_refresh=False)

    def fake_bridge(self, ok=True):
        def run():
            req = os.path.join(self.dir, "read_now")
            for _ in range(100):
                if os.path.exists(req):
                    rid = open(req, encoding="utf-8").read().split()[0]
                    os.remove(req)
                    res = {"id": rid, "ok": ok, "tables": 3, "game_date": "2026-10-25"}
                    if not ok:
                        res["error"] = "ReadInt: access violation"
                    with open(os.path.join(self.dir, "read_now_result.json"), "w", encoding="utf-8") as f:
                        json.dump(res, f)
                    return
                time.sleep(0.05)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_done(self):
        t = self.fake_bridge()
        r = self.call(wait_seconds=5)
        t.join()
        self.assertEqual(r["status"], "done", r)
        self.assertTrue(r["result"]["ok"])
        self.assertIn("_meta", r)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "read_now")))
        self.assertIn("REFRESH status=done", cli.render("fc26_refresh_world", r))

    def test_failed(self):
        t = self.fake_bridge(ok=False)
        r = self.call(wait_seconds=5)
        t.join()
        self.assertEqual(r["status"], "failed")
        self.assertIn("access violation", r["result"]["error"])

    def test_timeout_withdraws_or_keeps(self):
        r = self.call(wait_seconds=1)
        self.assertEqual(r["status"], "withdrawn")
        self.assertIn("twice", r["hint"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "read_now")))
        r = self.call(wait_seconds=1, keep_pending=True)
        self.assertEqual(r["status"], "pending")
        self.assertTrue(self.call(cancel=True)["cancelled"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "read_now")))

    def test_refusals(self):
        self.beat("1.0.4")
        self.assertEqual(self.call(wait_seconds=1)["error"], "bridge_without_read_now")
        self.beat("1.0.5")
        open(os.path.join(self.dir, "read_now_inflight"), "w").close()
        self.assertEqual(self.call(wait_seconds=1)["error"], "read_now_inflight")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "read_now")))

    def test_cli_args(self):
        ns = cli.build_parser().parse_args(["refresh", "--wait", "5", "--keep-pending"])
        self.assertEqual(cli.args_for("refresh", ns), ("fc26_refresh_world", {"wait_seconds": 5, "keep_pending": True}))


if __name__ == "__main__":
    unittest.main()
