"""Exercise the complete autorun script with game APIs forbidden during unsafe flows."""
import pathlib
import re
import unittest

try:
    import lupa
except ImportError:
    lupa = None

BRIDGE = pathlib.Path(__file__).resolve().parents[1] / "bridge" / "fc26cm_bridge_autorun.lua"


@unittest.skipIf(lupa is None, "lupa not installed")
class LifecycleTest(unittest.TestCase):
    def setUp(self):
        source = BRIDGE.read_text()
        self.lua = lupa.LuaRuntime()
        for index, name in enumerate(sorted(set(re.findall(r"\bENUM_\w+", source))), 1):
            self.lua.globals()[name] = index
        self.lua.execute('''
            now = 100; writes = 0; calls = 0; event_name = ""
            LE_VERSION = "v26.3.5"
            function require(name)
                return {encode=function(value) encoded=value; return "{}" end}
            end
            os.time = function() return now end
            os.date = function() return "2026-11-07" end
            os.remove = function() return true end
            os.rename = function() return true end
            io.open = function(path, mode)
                if mode == "r" then return nil end
                return {write=function(self) writes=writes+1; return self end,
                        close=function() return true end}
            end
            LOGGER = {LogInfo=function() end}
            function AddEventHandler() end
            function GetCMEventNameByID()
                if reenter then reenter=false; FC26CD_OnEvent(nil, 999, nil) end
                return event_name
            end
            function IsInCM() calls=calls+1; return false end
            function GetUserTeamID() error("unexpected game call") end
            function GetCurrentDate() error("unexpected date call") end
        ''')
        self.lua.execute(source)
        self.assertEqual(self.lua.globals().writes, 0, "autorun must not touch files")

    def event(self, name, now):
        self.lua.globals().event_name = name
        self.lua.globals().now = now
        self.lua.execute("FC26CD_OnEvent(nil, 999, nil)")

    def test_hub_and_sim_wait_without_game_calls(self):
        self.event("ENTERED_HUB_FIRST_TIME", 100)
        self.event("SCREEN_HAS_DONE_LOADING", 109)
        self.assertEqual(self.lua.globals().calls, 0)
        self.event("SCREEN_HAS_DONE_LOADING", 110)
        self.assertEqual(self.lua.globals().calls, 1)
        self.lua.execute("calls=0; FC26CD.hub_at=90")
        self.event("DAY_PASSED", 120)
        self.event("DAY_PASSED", 121)
        self.event("SCREEN_HAS_DONE_LOADING", 130)
        self.assertEqual(self.lua.globals().calls, 0)
        self.assertFalse(self.lua.globals().FC26CD.handling)

    def test_exit_creation_and_save_clear_identity(self):
        for name in ("PREPARE_FOR_SAVE", "PREPARE_FOR_FIRST_SAVE", "POST_LOAD_PREPARE",
                     "ABOUT_TO_INIT_MODE", "CAREER_TYPE_SELECTED", "EXIT_CAREER"):
            self.lua.execute('FC26CD.hub_at=1; FC26CD.loaded=true; FC26CD.names={old=true}; '
                             'FC26CD.payloads={{scope="ours"}}; FC26CD.world_day=20261107')
            self.event(name, 200)
            self.event("SCREEN_HAS_DONE_LOADING", 220)
            self.assertEqual(self.lua.globals().calls, 0, name)
            self.assertIsNone(self.lua.globals().FC26CD.hub_at)
            self.assertIsNone(self.lua.globals().FC26CD.names)
            self.assertEqual(len(self.lua.globals().FC26CD.payloads), 0)

    def test_reentrant_callback_returns_and_releases_guard(self):
        self.lua.execute("reenter=true")
        self.event("DAY_PASSED", 100)
        self.assertEqual(self.lua.globals().calls, 0)
        self.assertFalse(self.lua.globals().FC26CD.handling)


if __name__ == "__main__":
    unittest.main()
