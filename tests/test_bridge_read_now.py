"""On-demand world read (1.0.5, read_now): offline mock run of the autorun bridge in a real Lua (lupa).
A request file asks for one standings + fixtures read; it waits for a quiet hub, never runs in a match / save /
load flow, aborts and blocks on the first failed read, and refuses stale or unfinished requests."""
import json
import os
import re
import unittest

try:
    import lupa
except ImportError:  # pragma: no cover
    lupa = None

HERE = os.path.dirname(os.path.abspath(__file__))
AUTORUN = os.path.join(HERE, "..", "bridge", "fc26cm_bridge_autorun.lua")
R = "C:\\U\\Documents\\FC26CareerMCP\\"

MOCK = r'''
now = 1000; clock = 0; reads = 0; fail_at = nil; event_name = ""; FS = {}
os.getenv = function() return "C:\\U" end
os.time = function(t) if t then return 0 end return now end
os.clock = function() return clock end
os.date = function() return "2026-11-08" end
os.remove = function(p) local had = FS[p] ~= nil; FS[p] = nil; return had end
os.rename = function(a, b) FS[b] = FS[a]; FS[a] = nil; return true end
io.open = function(p, mode)
    if mode == "r" or mode == nil then
        if FS[p] == nil then return nil end
        return { read = function() return FS[p] end, close = function() return true end }
    end
    local buf = (mode == "a" and FS[p]) or ""
    return { write = function(self, ...) for _, v in ipairs({...}) do buf = buf .. tostring(v) end; return self end,
             close = function() FS[p] = buf; return true end }
end
local function enc(v)
    local t = type(v)
    if t == "table" then
        local parts = {}
        if #v > 0 or next(v) == nil then
            for _, x in ipairs(v) do parts[#parts+1] = enc(x) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for k, x in pairs(v) do parts[#parts+1] = string.format("%q", tostring(k)) .. ":" .. enc(x) end
        return "{" .. table.concat(parts, ",") .. "}"
    elseif t == "string" then return string.format("%q", v)
    elseif t == "number" or t == "boolean" then return tostring(v) end
    return "null"
end
function require(name) if name:find("json") then return { encode = enc } end return {} end
LE_VERSION = "v26.3.5"
LOGGER = { LogInfo = function() end }
function AddEventHandler() end
function GetCMEventNameByID() return event_name end
function IsInCM() return true end
function GetUserTeamID() return 1906 end
function GetCurrentDate() return 20261025 end
function GetTeamName(id) return "T" .. id end
function GetPlayerName(id) return "P" .. id end
function GetPlayersStats() return {} end
function GetUserTransferBudget() return 0 end
function MessageBox() end
-- fake memory: data manager chain, standings (4 teams, comp 1164), fixtures (2 played, 1 upcoming)
M = {}
local IF, A, B, DM, SL, SB, FL, FB = 0x100000, 0x110000, 0x120000, 0x200000, 0x300000, 0x310000, 0x400000, 0x410000
function GetPlugin() return IF end
M[IF + 0x18] = A; M[A + 0x10] = B; M[B + 0x08] = 0x130000; M[0x130000] = DM
M[DM + 0x88] = SL; M[SL + 0x28] = SB; M[SL + 0x1C] = 4
local teams = { 1906, 245, 246, 247 }
for i, t in ipairs(teams) do
    local p = SB + 0x18 * (i - 1)
    M[p + 0x16] = 1; M[p + 0x02] = 1164; M[p + 0x04] = t; M[p + 0x09] = 5 - i; M[p + 0x14] = 3 * (5 - i)
end
M[DM + 0x60] = FL; M[FL + 0x28] = FB; M[FL + 0x1C] = 3
local fx = { { 20261018, 0, 1, 2, 1, 1 }, { 20261022, 2, 0, 0, 3, 1 }, { 20261101, 0, 3, 0, 0, 0 } }
for i, f in ipairs(fx) do
    local p = FB + 0x18 * (i - 1)
    M[p + 0x14] = 1; M[p] = f[1]; M[p + 0x08] = 1164; M[p + 0x0A] = f[2]; M[p + 0x0C] = f[3]
    M[p + 0x0F] = f[4]; M[p + 0x11] = f[5]; M[p + 0x13] = f[6]
end
local function rd(_, a)
    reads = reads + 1
    if fail_at and reads >= fail_at then error("access violation (mock)") end
    return M[a] or 0
end
MEMORY = { ReadPointer = rd, ReadInt = rd, ReadShort = rd, ReadChar = rd, ReadBool = rd,
           ReadBytes = function(_, a, n) reads = reads + 1; local t = {} for i = 1, n do t[i] = 0 end return t end }
'''


@unittest.skipIf(lupa is None, "lupa not installed")
class ReadNowTest(unittest.TestCase):
    def setUp(self):
        with open(AUTORUN, encoding="utf-8") as f:
            SRC = f.read()
        self.lua = lupa.LuaRuntime()
        g = self.lua.globals()
        for i, name in enumerate(sorted(set(re.findall(r"\bENUM_\w+", SRC))), 1):
            g[name] = 1000 + i
        self.lua.execute(MOCK)
        g.FS[R + "disable_memory"] = "1"
        self.lua.execute(SRC)
        self.lua.execute('FC26CD.hub_at = 900; FC26CD.loaded = true; FC26CD.booted = true')

    def ev(self, name, t):
        g = self.lua.globals(); g.event_name = name; g.now = t
        self.lua.execute("FC26CD_OnEvent(nil, 1, nil)")

    def fs(self, name):
        return self.lua.globals().FS[R + name]

    def request(self, t, rid="abc"):
        self.lua.globals().FS[R + "read_now"] = "%s %d" % (rid, t)

    def result(self):
        return json.loads(self.fs("read_now_result.json"))

    def test_no_request_no_memory(self):
        self.ev("SCREEN_HAS_DONE_LOADING", 1000)
        self.assertEqual(self.lua.globals().reads, 0)
        self.assertIsNone(self.fs("world.json"))

    def test_waits_after_day_then_reads_once(self):
        self.request(1000)
        self.ev("DAY_PASSED", 1000)
        self.ev("SCREEN_HAS_DONE_LOADING", 1011)  # day work runs (DB only), read_now waits
        self.assertEqual(self.lua.globals().reads, 0)
        self.assertIsNotNone(self.fs("read_now"))
        self.ev("SCREEN_HAS_DONE_LOADING", 1016)
        self.assertGreater(self.lua.globals().reads, 0)
        r = self.result()
        self.assertTrue(r["ok"], r)
        self.assertIsNone(self.fs("read_now")); self.assertIsNone(self.fs("read_now_inflight"))
        w = self.fs("world.json"); self.assertIn("read_now", w); self.assertIn("transfers_note", w)
        snap = self.fs("snapshot.json"); self.assertIn('"source":"read_now"', snap.replace(" ", ""))
        n = self.lua.globals().reads
        self.ev("SCREEN_HAS_DONE_LOADING", 1030)  # no second read without a new request
        self.assertEqual(self.lua.globals().reads, n)

    def test_save_and_exit_block(self):
        self.request(1000)
        self.ev("PREPARE_FOR_SAVE", 1000)
        self.ev("SCREEN_HAS_DONE_LOADING", 1020)  # hub dropped: no game call
        self.ev("ENTERED_HUB_FIRST_TIME", 1021)
        self.ev("SCREEN_HAS_DONE_LOADING", 1031)  # bootstrap tick (memory off) - read_now not yet
        self.assertEqual(self.lua.globals().reads, 0)
        self.ev("SCREEN_HAS_DONE_LOADING", 1040)
        self.assertTrue(self.result()["ok"])

    def test_match_blocks(self):
        self.request(1000)
        self.lua.execute("FC26CD.in_match = true")
        self.ev("SCREEN_HAS_DONE_LOADING", 1100)
        self.assertEqual(self.lua.globals().reads, 0)

    def test_failure_aborts_and_blocks(self):
        self.lua.globals().fail_at = 7
        self.request(1000)
        self.ev("SCREEN_HAS_DONE_LOADING", 1000)
        r = self.result()
        self.assertFalse(r["ok"]); self.assertIn("access violation", r["error"])
        self.assertIsNone(self.fs("world.json")); self.assertEqual(self.lua.globals().reads, 7)
        self.lua.globals().fail_at = None
        self.request(1100, "next")
        self.ev("SCREEN_HAS_DONE_LOADING", 1100)
        self.assertIn("blocked", self.result()["error"]); self.assertEqual(self.lua.globals().reads, 7)

    def test_inflight_and_expired_refused(self):
        self.lua.globals().FS[R + "read_now_inflight"] = "x 1"
        self.request(1000)
        self.ev("SCREEN_HAS_DONE_LOADING", 1000)
        self.assertIn("did not finish", self.result()["error"])
        self.lua.globals().FS[R + "read_now_inflight"] = None
        self.request(0)
        self.ev("SCREEN_HAS_DONE_LOADING", 1001)
        self.assertIn("expired", self.result()["error"])
        self.assertEqual(self.lua.globals().reads, 0)


if __name__ == "__main__":
    unittest.main()
