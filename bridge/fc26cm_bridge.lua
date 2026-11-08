-- 1.0.5: on-demand world read (read_now): one standings + fixtures read per request, on a quiet hub; memory still opt-in.
-- 1.0.4: lifecycle guards, bounded diagnostics, per-tick reuse, explicit freshness; memory remains opt-in.
-- FC26 Career MCP - read-only career bridge for FC 26 Live Editor (Lua Engine, API v2).
--
-- What it does: while an OFFLINE career is open it writes JSON files that a local MCP server reads
-- (status.json, heartbeat.json, snapshot.json, world.json, event_payloads.jsonl). It never writes to the game.
--
-- Safety rules (all of them are deliberate, keep them when you change this file):
--   * READ-ONLY: no write handlers, no Lua execution from outside, no raw DB row dumps, no per-player stats calls;
--   * no polling loop (LE Lua has no Sleep); work happens on "Execute" and on career events (post__CareerModeEvent);
--   * autorun-safe: at load time the script only registers its handler; no game call and no file IO until the
--     career hub has been reached (ENTERED_HUB_*) plus HUB_SETTLE_S seconds; events whose name contains LOAD are
--     ignored before that; a career (re)load resets all state; manager pointers are never cached across events;
--   * bounded: DB scans only on tables known to be safe to read (SAFE_TABLES), key field only, MAX_SCAN records,
--     at most one heavy table read per game-day tick, none between pre-match and post-match;
--   * memory reads (standings, fixtures, negotiations) run only on a Live Editor version listed in SUPPORTED, every
--     pointer and vector size is checked, and any unexpected error disables memory reads for the session
--     (status.json compat = "partial"); documented-API reads keep working;
--   * (1.0.1) memory reads go through MEM (address checked before the native read, first failure stops the tick)
--     and never run in the tick of a day / match / sim / season event: MEM_SETTLE_S after the last one, none in
--     multi-day sims; the file disable_memory in the data folder turns them off;
--   * (1.0.3) memory reads are OFF by default: they run only while the file enable_memory exists in the data folder
--     (explicit opt-in) and disable_memory does not; the event payload logger is a memory read too. Without the
--     opt-in the bridge uses DB-table reads and documented getters only. No game call at all during a match flow,
--     a multi-day sim or MEM_SETTLE_S after a day / match / sim / season event: DAY_PASSED and post-match work
--     runs on the next quiet hub event;
--   * (1.0.5) on-demand read: <data folder>\read_now (written by the server's refresh_world tool; content = request id
--     + unix time) asks for ONE standings + fixtures read even without enable_memory. It runs only on a quiet hub
--     event: hub settled, no day / match / sim / season / save / load / exit event for READ_NOW_QUIET_S, no pending
--     day or post-match work, not in a match. read_now is deleted BEFORE the first read (never retried, even after
--     a crash) and read_now_inflight marks the read: a marker left behind (game died mid-read) refuses later
--     requests until it is deleted by hand. No event payloads, no TransferManager walks; the first failed read
--     aborts, nothing is written, and read_now is refused until the game restarts. Result: read_now_result.json;
--     data: world.json (tables, fixtures, leaders; transfers not read) + snapshot last_results / next_fixtures /
--     league_table (memory.source = "read_now"). Requests older than READ_NOW_MAX_AGE_S expire; the file
--     disable_read_now refuses all requests;
--   * every call goes through pcall; last_step.txt is a breadcrumb written before risky steps.
--
-- Files (default folder %USERPROFILE%\Documents\FC26CareerMCP, redirect with config.json {"data_dir": "..."}):
--   status.json        bridge/LE version, compat level, memory reads on/off, mode
--   heartbeat.json     last time the bridge was alive (wall clock + game date)
--   snapshot.json      user data (manager: club/squad/offers/negotiations; player: user player profile) - schema 4
--   world.json         every standings group, top players per competition, all fixtures/results, market transfers
--   event_payloads.jsonl  raw event payload dumps (capped)
--   last_step.txt      breadcrumb
--
-- Run manually: Live Editor -> Lua Engine -> load this file -> Execute (F5) with the career hub on screen.
-- Autorun: the installer copies fc26cm_bridge_autorun.lua (same file, AUTORUN = true) into <LE>\lua\autorun\.
-- Re-running is safe (the event handler is registered once per game session) and refreshes everything.
--
-- Derived from the GLOUNDEV internal bridge (ro14, 2026-11). Negotiation/standings layouts: Live Editor v26.3.5
-- scripts (export_fixtures.lua, export_transfer_history.lua). Not affiliated with EA SPORTS or xAranaktu.

require 'imports/career_mode/enums'
require 'imports/career_mode/helpers'
pcall(require, 'imports/other/helpers') -- GetCurrentDate, CalculatePlayerAge, GetPlayerPrimaryPositionName(pos_id)
local json = require 'imports/external/json'

local VERSION = "1.0.5"
local PRODUCT = "fc26-career-mcp"
-- true only in the autorun copy (fc26cm_bridge_autorun.lua, generated by tools/build_autorun.py).
local AUTORUN = false
local bootstrap -- defined in main
local write_world -- defined in the world section (ro10)
local read_now_ready, do_read_now -- 1.0.5, defined before FC26CD_OnEvent
local HUB_SETTLE_S = 5           -- seconds after entering the hub before any game call on events
local MAX_SCAN = 40000
local MAX_ROWS = 5
local MAX_SQUAD = 60              -- contract rows of the user team
local MAX_EVENTS = 40             -- filtered event log kept in memory and in the snapshot
local MAX_RATINGS_PER_PLAYER = 3  -- match rating history rows kept per squad player
local MAX_TRANSFERS = 15          -- user club transfers kept
local MAX_TARGETS = 15            -- non-squad players detailed (ids in offers / bids / talks)
local MAX_HISTORY = 10            -- player career: seasons kept from career_playasplayerhistory
local MAX_VEC = 4000              -- entries walked per transfer-history vector
local MAX_WORLD_TRANSFERS = 400   -- world.json: all clubs' transfers kept (most recent first)
local MAX_WORLD_PLAYED = 1200     -- world.json: played fixtures kept (most recent first)
local MAX_WORLD_UPCOMING = 600    -- world.json: upcoming fixtures kept (soonest first)
local MAX_WORLD_NAMES = 2500      -- world.json: GetTeamName / GetPlayerName calls per write
local WORLD_MIN_APPS_AVG = 3      -- world.json: minimum appearances for the average rating top 10

-- Data folder: Documents\FC26CareerMCP, or the folder named in Documents\FC26CareerMCP\config.json {"data_dir"}.
local DEFAULT_ROOT = string.format("%s\\Documents\\FC26CareerMCP", os.getenv('USERPROFILE') or ".")
local function resolve_root()
    local f = io.open(DEFAULT_ROOT .. "\\config.json", "r")
    if not f then return DEFAULT_ROOT end
    local txt = f:read("*a"); f:close()
    local ok, cfg = pcall(json.decode, txt)
    if ok and type(cfg) == "table" and type(cfg.data_dir) == "string" and #cfg.data_dir > 2 then
        return (cfg.data_dir:gsub("[\\/]+$", ""))
    end
    return DEFAULT_ROOT
end
-- Paths are set from DEFAULT_ROOT at load time (no file IO at load: autorun rule) and re-resolved from
-- config.json by ensure_dirs() on the first real read.
local ROOT, STATUS_PATH, SNAPSHOT_PATH, HEARTBEAT_PATH, STEP_PATH, PAYLOAD_PATH, WORLD_PATH
local function set_root(r)
    ROOT = r
    STATUS_PATH = ROOT .. "\\status.json"
    SNAPSHOT_PATH = ROOT .. "\\snapshot.json"
    HEARTBEAT_PATH = ROOT .. "\\heartbeat.json"
    STEP_PATH = ROOT .. "\\last_step.txt"
    PAYLOAD_PATH = ROOT .. "\\event_payloads.jsonl"
    WORLD_PATH = ROOT .. "\\world.json"
end
set_root(DEFAULT_ROOT)
local READ_NOW_QUIET_S = 15      -- seconds without a busy / save / load / exit event before a read_now
local READ_NOW_MAX_AGE_S = 900   -- older read_now requests expire unread
local PAYLOAD_BYTES = 0x100
local PAYLOAD_MAX_FILE = 8 * 1024 * 1024
local PAYLOAD_PER_NAME_DAY = 6
local MAX_PAYLOADS = 60

FC26CD = FC26CD or {}
for k, v in pairs({ cache = {}, need = {}, events = {} }) do
    if type(FC26CD[k]) ~= "table" then FC26CD[k] = v end
end

---------------------------------------------------------------- version gate (memory offsets)

-- Memory reads (standings, fixtures, transfer negotiations) depend on Live Editor's internal layout. They run ONLY
-- when LE_VERSION is a key of SUPPORTED. Element layouts inside the readers below are those of v26.3.5; a new
-- Live Editor release must be verified (compare its lua/scripts/export_fixtures.lua and export_transfer_history.lua)
-- before its version is added here. Unknown version => compat "partial": documented-API data only.
local SUPPORTED = {
    ["v26.3.5"] = { game = "FC 26 title update 1.6.3 (1.0.139.20381)",
        fce_dm = { 0x18, 0x10, 0x08, 0x00 }, standings = 0x88, fixtures = 0x60, tm_storage = 0x1DD0 },
}
local LE_VER = type(LE_VERSION) == "string" and LE_VERSION or tostring(LE_VERSION)
local OFFS = SUPPORTED[LE_VER]

local function supported_versions()
    local out = {}
    for v in pairs(SUPPORTED) do table.insert(out, v) end
    table.sort(out)
    return out
end
-- 1.0.3: memory reads are opt-in (data folder file enable_memory); off by default because they crashed some games.
local function mem_opt_in()
    local disabled = ROOT and io.open(ROOT .. "\\disable_memory", "r")
    if disabled then disabled:close(); return false end
    local f = ROOT and io.open(ROOT .. "\\enable_memory", "r")
    if f then f:close(); return true end
    return false
end
local function mem_ok() return OFFS ~= nil and not FC26CD.mem_disabled and (FC26CD.read_now_on or mem_opt_in()) end
local function compat_level()
    if type(LE_VERSION) ~= "string" then return "unsupported" end
    return (OFFS ~= nil and not FC26CD.mem_disabled) and "full" or "partial"
end
local function compat_reason()
    if type(LE_VERSION) ~= "string" then return "LE_VERSION missing: not running inside FC 26 Live Editor's Lua Engine" end
    if OFFS == nil then return "Live Editor " .. LE_VER .. " is not in the supported list (" .. table.concat(supported_versions(), ", ") .. "): memory reads off" end
    if not FC26CD.mem_disabled and not mem_opt_in() then
        return "memory reads off by default (opt in: create an empty file enable_memory in the data folder)"
    end
    return FC26CD.mem_reason
end
local write_status -- defined after the file helpers
-- Canary: an unexpected error inside a memory walk disables every memory read for the rest of the game session.
-- "not ready" assertions are transient (manager objects not built yet) and do not trip the canary.
local function mem_walk(name, fn, default)
    if not mem_ok() then return default end
    local ok, res = pcall(fn)
    if ok then return res end
    local msg = tostring(res)
    -- MEM gate / per-tick stop (ro15): transient, the read is retried on a later tick
    if msg:find("not ready", 1, true) or msg:find("memory closed", 1, true) or msg:find("memory read stopped", 1, true) then
        return default
    end
    FC26CD.mem_disabled = true
    FC26CD.mem_reason = name .. ": " .. msg
    pcall(function() LOGGER:LogInfo("[" .. PRODUCT .. "] memory reads disabled: " .. FC26CD.mem_reason) end)
    if write_status then pcall(write_status, "memory_canary") end
    return default
end

---------------------------------------------------------------- helpers

local function log(msg) pcall(function() LOGGER:LogInfo("[" .. PRODUCT .. " " .. VERSION .. "] " .. msg) end) end

local function ensure_dirs()
    set_root(resolve_root())
    for _, d in ipairs({ ROOT }) do
        local f = io.open(d .. "\\.probe", "w")
        if f then f:close(); os.remove(d .. "\\.probe") else os.execute(string.format('mkdir "%s"', d)) end
    end
end

local function write_atomic(path, text)
    local tmp = path .. ".tmp"
    local f = io.open(tmp, "w")
    if not f then return false end
    local wrote, write_err = f:write(text)
    local closed, close_err = f:close()
    if not wrote or not closed then
        os.remove(tmp)
        log("file write failed: " .. tostring(write_err or close_err))
        return false
    end
    -- Windows Lua cannot replace an existing destination with rename. Keep the gap short;
    -- readers must tolerate a temporarily missing file. Check every result instead of claiming success.
    os.remove(path)
    local renamed, rename_err = os.rename(tmp, path)
    if not renamed then log("file rename failed: " .. tostring(rename_err)); return false end
    return true
end

local function step(name)
    local f = io.open(STEP_PATH, "w")
    if f then f:write(os.date("%Y-%m-%d %H:%M:%S"), " ", VERSION, " ", name, "\n"); f:close() end
end

local function safe(fn, default)
    local ok, res = pcall(fn)
    if ok then return res end
    log("error: " .. tostring(res))
    return default
end

write_status = function(trigger)
    write_atomic(STATUS_PATH, json.encode({
        schema = 1, product = PRODUCT, bridge = VERSION, le_version = LE_VER, compat = compat_level(),
        memory_reads = mem_ok(), reason = compat_reason(), supported_le_versions = supported_versions(),
        game_version_note = OFFS and OFFS.game or nil, mode = FC26CD.cache.mode, trigger = trigger,
        data_dir = ROOT, written = os.date("!%Y-%m-%dT%H:%M:%SZ"),
    }))
end

-- GetCurrentDate() may fail or return an odd value (ro1 manager career): never required, diagnosed in heartbeat.
local function read_current_date()
    local ok, d = pcall(GetCurrentDate)
    if not ok then return nil, "GetCurrentDate error: " .. tostring(d) end
    if type(d) == "number" and d > 19000000 then
        return { year = d // 10000, month = (d // 100) % 100, day = d % 100 }
    end
    if type(d) ~= "table" and type(d) ~= "userdata" then return nil, "GetCurrentDate returned " .. type(d) end
    local okf, y, m, dd = pcall(function() return d.year or d.Year, d.month or d.Month, d.day or d.Day end)
    if okf and type(y) == "number" and y >= 2000 and type(m) == "number" and type(dd) == "number" then
        return { year = y, month = m, day = dd }
    end
    return nil, string.format("GetCurrentDate odd value (year=%s month=%s day=%s)", tostring(y), tostring(m), tostring(dd))
end

local tick_date, tick_date_error, tick_date_read
local function current_date()
    if not FC26CD.handling then return read_current_date() end
    if not tick_date_read then
        tick_date, tick_date_error = read_current_date()
        tick_date_read = true
    end
    return tick_date, tick_date_error
end

-- players.birthdate is Gregorian days (same formula as LE's DATE:FromGregorianDays).
local function from_greg_days(days)
    if type(days) ~= "number" or days <= 0 then return nil end
    local a = days + 2331205
    local b = math.floor((4 * a + 3) / 146097)
    local c = math.floor((-b * 146097 / 4) + a)
    local d = math.floor((4 * c + 3) / 1461)
    local e = math.floor(-1461 * d / 4 + c)
    local m = math.floor((5 * e + 2) / 153)
    return { day = math.ceil(-(153 * m + 2) / 5) + e + 1, month = math.ceil(-m / 10) * 12 + m + 3,
        year = b * 100 + d - 4800 + math.floor(m / 10) }
end

local function age_from_birthdate(days)
    local now, b = current_date(), from_greg_days(days)
    if not now or not b then return nil end
    local age = now.year - b.year
    if now.month < b.month or (now.month == b.month and now.day < b.day) then age = age - 1 end
    if age < 10 or age > 60 then return nil end
    return age
end

local function position_name(pos)
    if type(pos) ~= "number" or type(GetPlayerPrimaryPositionName) ~= "function" then return nil end
    local n = safe(function() return GetPlayerPrimaryPositionName(pos) end, nil)
    return n ~= "INVALID" and n or nil
end

local function date_str(d) return d and string.format("%04d-%02d-%02d", d.year, d.month, d.day) or nil end

local function game_date() return date_str((current_date())) end

local function today_int()
    local d = current_date()
    return d and (d.year * 10000 + d.month * 100 + d.day) or 0
end

local function fixture_date_str(v)
    if v and v > 19000000 then
        return string.format("%04d-%02d-%02d", v // 10000, (v // 100) % 100, v % 100)
    end
    return tostring(v)
end

local function career_ready()
    local ok, cm = pcall(IsInCM)
    if not ok or not cm then return false, "not in career mode" end
    local ok2, tid = pcall(GetUserTeamID)
    if not ok2 or type(tid) ~= "number" or tid <= 0 then return false, "no user team yet" end
    return true
end

local function heartbeat(reason, passive)
    local now = os.time()
    if FC26CD.last_beat and now - FC26CD.last_beat < 5 and (passive or reason == "event") then return end
    FC26CD.last_beat = now
    local ready, why, d, derr
    if passive then
        why = reason -- activity only: no game API, no claim that snapshot data is current
    else
        ready, why = career_ready()
        if ready then d, derr = current_date() end
    end
    write_atomic(HEARTBEAT_PATH, json.encode({
        product = PRODUCT, bridge = VERSION, wall = os.date("!%Y-%m-%dT%H:%M:%SZ"), unix = now, reason = reason,
        career_ready = ready, why = why, mode = FC26CD.cache.mode, compat = compat_level(),
        game_date = date_str(d), date_error = derr,
    }))
end

---------------------------------------------------------------- fixtures + standings (memory)

-- ro15 crash fix (2026-11-07). A snapshot memory walk ran in the DAY_PASSED tick while the game was rebuilding its
-- lists: LE's ReadShort got a non-integer address, then a native read hit 0xFFFFFFFFFFFFFFFF and the game crashed.
-- Every memory read now goes through MEM: the address must be an integer inside user space (checked BEFORE the
-- native call), each read is pcall'd, the first failure stops memory reads for the rest of the tick, ReadPointer
-- returns 0 for anything that is not a sane aligned pointer, and MEM is open only when memory_gate() allows it.
local ADDR_LO, ADDR_HI = 0x10000, 0x7FFFFFFEFFFF
local function addr_ok(a) return math.type(a) == "integer" and a >= ADDR_LO and a <= ADDR_HI end
local function valid_ptr(p) return addr_ok(p) and p % 8 == 0 end
local MEM = { open = false, failed = nil, why = "not started" }
local function mem_stop(why)
    MEM.failed = MEM.failed or why
    error("memory read stopped: " .. MEM.failed, 0)
end
local function mem_read(fname, a, width, count, ungated)
    if not ungated and not MEM.open then error("memory closed (deferred: " .. tostring(MEM.why) .. ")", 0) end
    if MEM.failed then error("memory read stopped: " .. MEM.failed, 0) end
    if not addr_ok(a) or not addr_ok(a + width - 1) then
        mem_stop(string.format("%s bad address %s (%s)", fname, tostring(a), type(a)))
    end
    local ok, v
    if count then ok, v = pcall(MEMORY[fname], MEMORY, a, count) else ok, v = pcall(MEMORY[fname], MEMORY, a) end
    if not ok then mem_stop(fname .. ": " .. tostring(v)) end
    if fname == "ReadBytes" then
        if type(v) ~= "table" then mem_stop("ReadBytes returned " .. type(v)) end
        return v
    end
    if fname == "ReadBool" then
        if type(v) == "boolean" then return v end
        if math.type(v) == "integer" then return v ~= 0 end
        mem_stop("ReadBool returned " .. type(v))
    end
    if math.type(v) ~= "integer" then mem_stop(fname .. " returned " .. type(v)) end
    if fname == "ReadPointer" and not valid_ptr(v) then return 0 end
    return v
end
function MEM:ReadPointer(a) return mem_read("ReadPointer", a, 8) end
function MEM:ReadInt(a) return mem_read("ReadInt", a, 4) end
function MEM:ReadShort(a) return mem_read("ReadShort", a, 2) end
function MEM:ReadChar(a) return mem_read("ReadChar", a, 1) end
function MEM:ReadBool(a) return mem_read("ReadBool", a, 1) end
function MEM:ReadBytes(a, n) return mem_read("ReadBytes", a, n, n) end
-- Same walk as LE's ReadMultilevelPointer (p = [p + off] per offset), every level checked.
function MEM:ReadMultilevelPointer(base, offs)
    local p = base
    for _, o in ipairs(offs) do
        if not valid_ptr(p) then return 0 end
        p = mem_read("ReadPointer", p + o, 8)
    end
    return p
end
-- Event payload bytes: the event object handed to this callback, read in its own tick (no pointer walk). Never
-- read unless the opt-in and settle gate are open; failures stop the memory tick.
local function payload_bytes(a, n)
    if not MEM.open then return nil end -- 1.0.3: opt-in + settle gate like every other memory read
    -- LE hands some payload addresses as floats: skip those, and never let a payload miss set MEM.failed
    -- (it would stop the deferred memory refresh that runs later in the same tick).
    if not addr_ok(a) or not addr_ok(a + n - 1) then return nil end
    local ok, v = pcall(mem_read, "ReadBytes", a, n, n)
    return ok and v or nil
end

-- Memory gate: never in the tick of DAY_PASSED / match / sim / season / load events, only MEM_SETTLE_S after the
-- last such event; rapid DAY_PASSED (multi-day sim) keeps it closed; never during a match; never while
-- <data folder>\disable_memory exists (kill switch). DB-table reads and documented getters are not affected.
local MEM_SETTLE_S = 10
local SIM_GAP_S = 8 -- two DAY_PASSED closer than this = a multi-day sim
local BUSY_EVENT_PARTS = { "DAY_PASSED", "MATCH", "SIM", "SEASON", "FIXTURE", "TRANSITION", "END_OF", "KICK_OFF",
    "ENTERED_HUB", "TOURNAMENT", "CALENDAR", "ADVANCE" }
local function kill_switch_on()
    local f = ROOT and io.open(ROOT .. "\\disable_memory", "r")
    if f then f:close(); return true end
    return false
end
local function is_busy_event(name)
    for _, part in ipairs(BUSY_EVENT_PARTS) do
        if name:find(part, 1, true) then return true end
    end
    return false
end
local function note_busy(ename)
    local now = os.time()
    if ename == "DAY_PASSED" then
        if FC26CD.last_day_at and now - FC26CD.last_day_at < SIM_GAP_S then FC26CD.sim_until = now + MEM_SETTLE_S end
        FC26CD.last_day_at = now
    end
    FC26CD.busy_at = now
    FC26CD.risky_at = now
    FC26CD.mem_due = true
end
-- 1.0.3: quiet gate for ANY game call on events (not only memory): match flow, multi-day sim, settle time.
local function quiet_gate()
    local now = os.time()
    if FC26CD.in_match then return "match" end
    if FC26CD.sim_until and now < FC26CD.sim_until then return "multi_day_sim" end
    if FC26CD.busy_at and now - FC26CD.busy_at < MEM_SETTLE_S then return "settling" end
    return nil
end
local function memory_gate()
    local now = os.time()
    if kill_switch_on() then return "kill_switch" end
    if not mem_opt_in() then return "off_by_default" end
    if OFFS == nil or FC26CD.mem_disabled then return "unsupported_or_disabled" end
    if FC26CD.in_match then return "match" end
    if FC26CD.sim_until and now < FC26CD.sim_until then return "multi_day_sim" end
    if FC26CD.busy_at and now - FC26CD.busy_at < MEM_SETTLE_S then return "settling" end
    return nil
end
-- Start of every tick (career event, Run Code): clears the per-tick failure and opens MEM only if the gate allows.
local function mem_tick()
    MEM.failed = nil
    MEM.standings = nil
    MEM.why = memory_gate()
    MEM.open = MEM.why == nil
    return MEM.open
end
-- Names for compobjids LE's id_map does not know come from GetPlayersStats (learn_comp_name) or stay "COBJ<id>".
local KNOWN_COMPS = {}
local LEAGUE_COMPS = {} -- no hard-coded league ids: a league group is recognised by its size (16-24 teams)
local comp_names = {}

local function is_code(n) return n == nil or n == "" or n:match("^C%d+$") or n:match("^COBJ%d+$") or n:match("^%d+$") end

local function comp_name(cid)
    if comp_names[cid] == nil then
        local n = safe(function() return GetCompetitionNameByObjID(cid) end, nil)
        if is_code(n) then n = KNOWN_COMPS[cid] or n or ("COBJ" .. tostring(cid)) end
        comp_names[cid] = n
    end
    return comp_names[cid]
end

-- Learn names from GetPlayersStats (compobjid + compname) for ids still shown as codes.
local function learn_comp_name(cid, name)
    if type(cid) == "number" and type(name) == "string" and not is_code(name) and is_code(comp_name(cid)) then
        comp_names[cid] = name
    end
end

local function fce_data_manager()
    assert(MEM.open and not MEM.failed, "memory closed (not ready)")
    local iface = GetPlugin(ENUM_djb2IFCEInterface_CLSS)
    assert(valid_ptr(iface), "FCE interface not ready")
    local dm = MEM:ReadMultilevelPointer(iface, OFFS.fce_dm)
    assert(valid_ptr(dm), "FCE data manager not ready")
    return dm
end

local function read_standings()
    assert(mem_ok(), "memory reads disabled (not ready)")
    assert(MEM.open and not MEM.failed, "memory closed (not ready)")
    if MEM.standings then return MEM.standings end
    local res = {}
    local list = MEM:ReadPointer(fce_data_manager() + OFFS.standings)
    assert(valid_ptr(list), "list not ready")
    local base = MEM:ReadPointer(list + 0x28)
    local n = MEM:ReadInt(list + 0x1C)
    assert(valid_ptr(base), "list base not ready")
    assert(n >= 0 and n <= 8000, "list size out of range (" .. tostring(n) .. "): layout mismatch?")
    for i = 0, n - 1 do
        local p = base + 0x18 * i
        res[i] = {
            used = MEM:ReadBool(p + 0x16),
            compobjid = MEM:ReadShort(p + 0x02),
            teamid = MEM:ReadInt(p + 0x04),
            w = MEM:ReadChar(p + 0x09) + MEM:ReadChar(p + 0x0E),
            d = MEM:ReadChar(p + 0x0A) + MEM:ReadChar(p + 0x0F),
            l = MEM:ReadChar(p + 0x0B) + MEM:ReadChar(p + 0x10),
            gf = MEM:ReadChar(p + 0x0C) + MEM:ReadChar(p + 0x11),
            ga = MEM:ReadChar(p + 0x0D) + MEM:ReadChar(p + 0x12),
            pts = MEM:ReadShort(p + 0x14),
        }
    end
    MEM.standings = res
    return res
end

local function read_fixtures(standings)
    local res = {}
    local list = MEM:ReadPointer(fce_data_manager() + OFFS.fixtures)
    assert(valid_ptr(list), "list not ready")
    local base = MEM:ReadPointer(list + 0x28)
    local n = MEM:ReadInt(list + 0x1C)
    assert(valid_ptr(base), "list base not ready")
    assert(n >= 0 and n <= 8000, "list size out of range (" .. tostring(n) .. "): layout mismatch?")
    for i = 0, n - 1 do
        local p = base + 0x18 * i
        if MEM:ReadBool(p + 0x14) then
            local hs = standings[MEM:ReadShort(p + 0x0A)]
            local as = standings[MEM:ReadShort(p + 0x0C)]
            if hs and as then
                table.insert(res, {
                    date = MEM:ReadInt(p + 0x00),
                    compobjid = MEM:ReadShort(p + 0x08),
                    home_id = hs.teamid, away_id = as.teamid,
                    home_score = MEM:ReadChar(p + 0x0F), away_score = MEM:ReadChar(p + 0x11),
                    played = MEM:ReadBool(p + 0x13),
                })
            end
        end
    end
    return res
end

local function fixture_out(f)
    return {
        date = fixture_date_str(f.date), competition = comp_name(f.compobjid), compobjid = f.compobjid,
        home = safe(function() return GetTeamName(f.home_id) end, nil),
        away = safe(function() return GetTeamName(f.away_id) end, nil),
        home_id = f.home_id, away_id = f.away_id,
        score = f.played and string.format("%d-%d", f.home_score, f.away_score) or nil,
    }
end

-- Fixtures of one team: last `n_last` results and next `n_next` matches.
local function team_fixtures(standings, teamid, n_last, n_next)
    local mine, today = {}, today_int()
    for _, f in ipairs(read_fixtures(standings)) do
        if f.home_id == teamid or f.away_id == teamid then table.insert(mine, f) end
    end
    table.sort(mine, function(a, b) return a.date < b.date end)
    local played, upcoming = {}, {}
    for _, f in ipairs(mine) do
        if f.played then table.insert(played, f)
        elseif f.date >= today and #upcoming < n_next then table.insert(upcoming, fixture_out(f)) end
    end
    local last = {}
    for i = #played, math.max(1, #played - n_last + 1), -1 do table.insert(last, fixture_out(played[i])) end
    return { last_results = last, next_fixtures = upcoming }
end

-- Standings groups by competition. Default (ro7) = the group that contains `teamid` with games played by the team
-- (its own row P > 0): league-sized groups (16-24 teams, not friendlies) first, then most games played. A group in
-- which the team has played no game is never picked by default (ro6 returned the empty compobjid 635: 6th, 0 pts).
local function comp_table(standings, teamid, compobjid)
    local comps = {}
    for _, s in pairs(standings) do
        if s.used and s.teamid > 0 then
            local c = comps[s.compobjid] or { rows = {}, has_team = false, played = 0, team_played = 0 }
            comps[s.compobjid] = c
            table.insert(c.rows, s)
            c.played = c.played + s.w + s.d + s.l
            if s.teamid == teamid then c.has_team = true; c.team_played = s.w + s.d + s.l end
        end
    end
    local pick = compobjid and comps[compobjid] and compobjid or nil
    local team_comps = {}
    local function score(id, c)
        local n = #c.rows
        local league = (LEAGUE_COMPS[id] or (n >= 16 and n <= 24)) and id ~= 1901 and 1 or 0
        return league, c.team_played, c.played, -math.abs(n - 20)
    end
    local function better(id, c, bid, b)
        local x, y = { score(id, c) }, { score(bid, b) }
        for i = 1, 4 do if x[i] ~= y[i] then return x[i] > y[i] end end
        return id > bid
    end
    for id, c in pairs(comps) do
        if c.has_team then
            table.insert(team_comps, { compobjid = id, competition = comp_name(id), teams = #c.rows,
                team_played = c.team_played, games = c.played })
            if not compobjid and c.team_played > 0 and (pick == nil or better(id, c, pick, comps[pick])) then pick = id end
        end
    end
    table.sort(team_comps, function(a, b) return a.compobjid < b.compobjid end)
    if not pick then
        return { error = "no standings group with games played by the team yet", team_competitions = team_comps }
    end
    local rows = comps[pick].rows
    table.sort(rows, function(a, b)
        if a.pts ~= b.pts then return a.pts > b.pts end
        if (a.gf - a.ga) ~= (b.gf - b.ga) then return (a.gf - a.ga) > (b.gf - b.ga) end
        return a.gf > b.gf
    end)
    local out = {}
    for i, s in ipairs(rows) do
        table.insert(out, { pos = i, team = safe(function() return GetTeamName(s.teamid) end, nil), teamid = s.teamid,
            p = s.w + s.d + s.l, w = s.w, d = s.d, l = s.l, gf = s.gf, ga = s.ga, pts = s.pts })
    end
    return { competition = comp_name(pick), compobjid = pick, rows = out, team_competitions = team_comps }
end

---------------------------------------------------------------- season stats (GetPlayersStats, no per-player call)

local function season_stats(teamid, playerid)
    local res = {}
    for _, s in ipairs(GetPlayersStats()) do
        learn_comp_name(s.compobjid, s.compname)
        if (teamid == nil or s.teamid == teamid) and (playerid == nil or s.playerid == playerid) then
            local r = res[s.playerid] or { playerid = s.playerid, app = 0, goals = 0, assists = 0, yellow = 0, red = 0,
                by_competition = {} }
            r.app = r.app + s.app; r.goals = r.goals + s.goals; r.assists = r.assists + s.assists
            r.yellow = r.yellow + s.yellow; r.red = r.red + s.red
            -- ro7: per competition for squads too (totals include friendlies; avg is the game's decimal rating)
            table.insert(r.by_competition, { competition = s.compname or comp_name(s.compobjid), compobjid = s.compobjid,
                app = s.app, goals = s.goals, assists = s.assists, avg = s.avg })
            res[s.playerid] = r
        end
    end
    local out = {}
    for pid, r in pairs(res) do
        r.player = safe(function() return GetPlayerName(pid) end, nil)
        table.insert(out, r)
    end
    table.sort(out, function(a, b) return a.goals > b.goals end)
    return out
end

---------------------------------------------------------------- DB reads (only tables v3/v5 read safely)

local SAFE_TABLES = {
    career_users = true, career_playasplayer = true, career_playercontract = true,
    career_playermatchratinghistory = true, career_playerlastgrowth = true, players = true,
    career_playerlastmatchhistory = true, career_playasplayerhistory = true,
}
-- (tables read without problems in the internal bridges v3/v5 and ro1-ro14; never add a table that was not)

local function field_set(name)
    local has = {}
    for _, f in ipairs(safe(function() return GetDBTableFields(name) end, nil) or {}) do has[f.name] = true end
    return has
end

-- Key-only scan of a safe table. `match(v)` tests the key field value (nil keyfield = every row).
-- Keeps the last `limit` matches (or stops at `limit` when stop_at_limit); `stop_after` = stop after that many matches.
local function scan_rows(name, keyfield, match, limit, want, stop_at_limit)
    if not SAFE_TABLES[name] then return nil end
    local ok, t = pcall(function() return LE.db:GetTable(name) end)
    if not ok or t == nil then return nil end
    local has = field_set(name)
    if keyfield and not has[keyfield] then return nil end
    local fields = {}
    for _, f in ipairs(want) do if has[f] then table.insert(fields, f) end end
    local out = {}
    step("scan " .. name)
    local ok2, err = pcall(function()
        local rec = t:GetFirstRecord()
        local visited = 0
        while rec and rec > 0 and visited < MAX_SCAN do
            visited = visited + 1
            if keyfield == nil or match(t:GetRecordFieldValue(rec, keyfield)) then
                local row = {}
                for _, f in ipairs(fields) do
                    local okv, v = pcall(function() return t:GetRecordFieldValue(rec, f) end)
                    if okv then row[f] = v end
                end
                table.insert(out, row)
                if #out > limit then table.remove(out, 1) end
                if #out >= limit and stop_at_limit then break end
            end
            rec = t:GetNextValidRecord()
        end
    end)
    step("scan " .. name .. " done")
    if not ok2 then log("scan " .. name .. " failed: " .. tostring(err)) end
    return out
end

local function find_rows(name, keyfield, keyval, limit, want)
    limit = math.min(limit or MAX_ROWS, MAX_ROWS)
    return scan_rows(name, keyfield, function(v) return v == keyval end, limit, want,
        keyfield == nil or limit == 1)
end

local PLAYER_KEYS = { "overallrating", "potential", "preferredposition1", "birthdate", "playerjointeamdate",
    "contractvaliduntil", "height", "weight", "preferredfoot", "skillmoves", "weakfootabilitytypecode",
    "acceleration", "sprintspeed", "finishing", "shotpower", "longshots", "positioning", "volleys", "penalties",
    "shortpassing", "longpassing", "vision", "crossing", "curve", "freekickaccuracy",
    "dribbling", "ballcontrol", "agility", "balance", "reactions", "composure",
    "stamina", "strength", "jumping", "aggression", "interceptions", "defensiveawareness",
    "standingtackle", "slidingtackle", "headingaccuracy" }

-- Player career: the user's row of career_playasplayer (requests / playing time) and career_playasplayerhistory
-- (one row per season). Both are key-filtered reads of tables in SAFE_TABLES.
local PAP_FIELDS = { "playerid", "userid", "position", "playerrequest", "numtransferrequests", "numloanrequests",
    "numwithdrawrequests", "requestactiondays", "lastwithdrawdate", "playedlastmatch" }
local PAPH_FIELDS = { "season", "teamid", "leagueid", "position", "appearances", "goals", "assists", "motm",
    "matchratings", "cleansheets", "wins", "draws", "loses", "wage", "overall", "value", "tableposition",
    "leaguetrophies", "domesticcuptrophies", "continentalcuptrophies", "clublevel", "totalyellows", "totalreds" }
local function player_career_row(uid)
    local r = find_rows("career_playasplayer", "userid", uid, 1, PAP_FIELDS)
    return r and r[1]
end
local function player_history(uid)
    local rows = scan_rows("career_playasplayerhistory", "userid", function(v) return v == uid end, MAX_HISTORY,
        PAPH_FIELDS, false) or {}
    table.sort(rows, function(a, b) return (a.season or 0) < (b.season or 0) end)
    return rows
end

-- Career mode + user: career_users row, then career_playasplayer row for that user => player career;
-- readable table without such a row => manager career. Cached until the next Run Code.
local function detect_user()
    local c = FC26CD.cache
    if c.mode then return c.mode end
    local users = find_rows("career_users", nil, nil, 1,
        { "userid", "firstname", "surname", "wage", "clubteamid", "seasoncount", "usertype", "playertype" })
    local u = users and users[1]
    c.who = u or {}
    local rows
    if u and u.userid ~= nil then
        rows = find_rows("career_playasplayer", "userid", u.userid, 1, PAP_FIELDS)
    end
    local pid = rows and rows[1] and rows[1].playerid
    if pid and pid > 0 then
        c.mode, c.pid, c.pid_source = "player", pid, "career_playasplayer"
        c.career = rows[1]
    elseif rows ~= nil then
        c.mode = "manager"
    else
        c.mode = "unknown" -- tables unreadable: ro1 fallback (expected player id) + squad, both flagged
    end
    return c.mode
end

local function user_player_id()
    local c = FC26CD.cache
    detect_user()
    if c.mode == "player" then return c.pid, c.who end
    return nil, c.who
end

local function player_attributes(pid)
    local r = find_rows("players", "playerid", pid, 1, PLAYER_KEYS)
    return r and r[1]
end

local function player_ratings(pid)
    local r = find_rows("career_playermatchratinghistory", "playerid", pid, MAX_ROWS,
        { "date", "rating", "minsplayed", "position" }) or {}
    table.sort(r, function(x, y) return (x.date or 0) < (y.date or 0) end)
    return r
end

local function player_basic(pid)
    local tid = safe(function() return GetTeamIdFromPlayerId(pid) end, nil)
    return {
        playerid = pid, name = safe(function() return GetPlayerName(pid) end, nil),
        team_id = tid, team = tid and safe(function() return GetTeamName(tid) end, nil) or nil,
    }
end

-- User player info from cache; refreshes attributes/ratings when flagged (one scan per call).
local function user_player_info(allow_scan)
    local pid, who = user_player_id()
    local c, need = FC26CD.cache, FC26CD.need
    if not pid then return { error = "user player id unknown (career tables unreadable)" } end
    if allow_scan then
        if need.ratings or c.ratings == nil then c.ratings = safe(function() return player_ratings(pid) end, c.ratings); need.ratings = false
        elseif need.attributes or c.attributes == nil then c.attributes = safe(function() return player_attributes(pid) end, c.attributes); need.attributes = false end
    end
    local info = player_basic(pid)
    if c.attributes then
        info.position = position_name(c.attributes.preferredposition1)
        info.age = age_from_birthdate(c.attributes.birthdate)
    end
    info.id_source = c.pid_source
    info.wage = who.wage; info.season_count = who.seasoncount
    info.career = c.career                       -- career_playasplayer: requests, playing-time counters
    info.history = c.history                     -- career_playasplayerhistory: one row per season
    info.last_match = c.last_match and c.last_match[pid] or nil
    info.squad_role = "not_available"            -- starter/sub/reserve status is not in a safe table
    info.rating_unit = "recent_ratings[].rating is an integer 5-10 (the game shows one decimal)"
    info.attributes = c.attributes
    if c.attributes then info.overall = c.attributes.overallrating; info.potential = c.attributes.potential end
    if c.ratings and #c.ratings > 0 then
        info.recent_ratings = c.ratings
        local sum = 0
        for _, r in ipairs(c.ratings) do sum = sum + (r.rating or 0) end
        info.avg_rating_recent = sum / #c.ratings
    end
    info.season = safe(function() return season_stats(nil, pid)[1] end, nil)
    return info
end

---------------------------------------------------------------- squad (manager career)

-- Contracts of the user team: one key-only scan of career_playercontract (teamid), capped at MAX_SQUAD rows.
local function squad_contracts(teamid)
    return scan_rows("career_playercontract", "teamid", function(v) return v == teamid end, MAX_SQUAD,
        { "playerid", "wage", "playerrole", "contract_date", "duration_months", "contract_status", "loan_wage_split",
          "signon_bonus", "performancebonustype", "performancebonusvalue", "performancebonuscount",
          "performancebonuscountachieved", "isperformancebonusachieved", "last_status_change_date" },
        true)
end

-- Overall/potential of a set of players: one key-only scan of players, stops when all ids are found.
local function squad_ratings(ids)
    local want, n = {}, 0
    for _, pid in ipairs(ids) do if not want[pid] then want[pid] = true; n = n + 1 end end
    if n == 0 then return {} end
    local rows = scan_rows("players", "playerid", function(v) return want[v] == true end, n,
        { "playerid", "overallrating", "potential", "contractvaliduntil", "preferredposition1", "birthdate",
          "internationalrep", "isretiring" }, true) or {}
    local by = {}
    for _, r in ipairs(rows) do if r.playerid then by[r.playerid] = r end end
    return by
end

local function contract_until(r)
    local d, m = r.contract_date, r.duration_months
    if type(d) ~= "number" or d < 19000000 or type(m) ~= "number" or m < 0 then return nil end
    local y, mo = d // 10000, (d // 100) % 100 + m
    y = y + (mo - 1) // 12; mo = (mo - 1) % 12 + 1
    return string.format("%04d-%02d", y, mo)
end

-- Squad from cache; refreshes contracts, then ratings, when flagged (one scan per call).
local run_job -- defined below (ro4 jobs)

-- career_playercontract.contract_status codes confirmed in game (Test Round 1: Maitland-Niles, list date 08/26).
-- 0/4/1/3/5 = Live Editor convention (docs/FC26_DATA_REGISTRY.md); 7 confirmed in game; others stay raw.
local CONTRACT_STATUS = { [0] = "normal", [4] = "loaned_out", [1] = "loaned_in", [3] = "loaned_in", [5] = "loaned_in",
    [7] = "transfer_listed" }

-- ro7: why a contract row of the user team is not a squad player (nil = in the squad). club_of = the player's club
-- from GetTeamIdFromPlayerId (flags job); transfers = this season's moves (memory). Loaned-out players stay in.
local function squad_exclusion(pid, teamid, r, c)
    local fl = c.flags and c.flags[pid] or {}
    if fl.loaned_out or (r.loan_wage_split or -1) >= 0 or r.contract_status == 4 then return nil end
    local club = c.club_of and c.club_of[pid]
    if type(club) == "number" and club > 0 and club ~= teamid then return "club is " .. tostring(club) end
    for _, t in ipairs(c.transfers or {}) do
        if t.playerid == pid and t.type == "transfer" and t.from_id == teamid and t.to_id ~= teamid
            and not (type(club) == "number" and club == teamid) then
            return "transferred to " .. tostring(t.to or t.to_id)
        end
    end
    return nil
end

local function squad_info(teamid, allow_scan)
    local c = FC26CD.cache
    if allow_scan then run_job(teamid) end
    local inj, lm, rh, fl = c.injuries or {}, c.last_match or {}, c.match_ratings or {}, c.flags or {}
    local stats = {}
    for _, s in ipairs(safe(function() return season_stats(teamid, nil) end, nil) or {}) do stats[s.playerid] = s end
    local out, seen, excluded = {}, {}, {}
    for _, r in ipairs(c.squad_rows or {}) do
        local pid = r.playerid
        local why = type(pid) == "number" and pid > 0 and not seen[pid] and squad_exclusion(pid, teamid, r, c) or nil
        if why then
            seen[pid] = true
            table.insert(excluded, { playerid = pid, name = safe(function() return GetPlayerName(pid) end, nil),
                reason = why, contract_status = r.contract_status })
        elseif type(pid) == "number" and pid > 0 and not seen[pid] then
            seen[pid] = true
            local rt = c.squad_ratings and c.squad_ratings[pid] or {}
            local st = stats[pid] or {}
            table.insert(out, {
                playerid = pid, name = safe(function() return GetPlayerName(pid) end, nil),
                position = position_name(rt.preferredposition1) or (lm[pid] and lm[pid].pos),
                age = age_from_birthdate(rt.birthdate),
                overall = rt.overallrating or (lm[pid] and lm[pid].ovr), potential = rt.potential,
                no_player_row = c.squad_ratings ~= nil and c.squad_ratings[pid] == nil or nil,
                wage = r.wage, role = r.playerrole, contract_until = contract_until(r),
                on_loan = (r.loan_wage_split or -1) >= 0 or nil,
                loaned_out = ((r.loan_wage_split or -1) >= 0 or r.contract_status == 4 or (fl[pid] and fl[pid].loaned_out)) or nil,
                app = st.app, goals = st.goals, assists = st.assists, yellow = st.yellow, red = st.red,
                -- ro4
                contract_status = r.contract_status, contract_status_label = CONTRACT_STATUS[r.contract_status],
                by_competition = st.by_competition, status_changed = r.last_status_change_date,
                signon_bonus = (r.signon_bonus or 0) > 0 and r.signon_bonus or nil,
                bonus = (r.performancebonustype or 0) > 0 and { type = r.performancebonustype,
                    value = r.performancebonusvalue, count = r.performancebonuscount,
                    achieved = r.performancebonuscountachieved, done = r.isperformancebonusachieved == 1 } or nil,
                intl_rep = rt.internationalrep, retiring = rt.isretiring == 1 or nil,
                injury = inj[pid], last_match = lm[pid], ratings = rh[pid], flags = fl[pid],
            })
        end
    end
    -- players with season stats for the club but no contract row (e.g. loan arrivals)
    for pid, st in pairs(stats) do
        if not seen[pid] then
            table.insert(out, { playerid = pid, name = st.player, no_contract_row = true,
                app = st.app, goals = st.goals, assists = st.assists, yellow = st.yellow, red = st.red,
                by_competition = st.by_competition })
        end
    end
    table.sort(out, function(a, b) return (a.overall or 0) > (b.overall or 0) end)
    local wages = 0
    for _, p in ipairs(out) do wages = wages + (p.wage or 0) end
    return { count = #out, total_wages = wages, players = out, excluded = excluded,
        membership = "club = user team (GetTeamIdFromPlayerId) or loaned out; sold players in excluded",
        club_check_read = c.club_of ~= nil,
        notes = { ratings = "ratings[].rating are rounded integers; the game shows one decimal (by_competition[].avg)",
            stats = "app/goals/assists = all competitions incl. friendlies; split in by_competition",
            potential = "raw potential (hidden true value); the game shows youth POT only as a scouted range" },
        contracts_read = c.squad_rows ~= nil, ratings_read = c.squad_ratings ~= nil,
        injuries_read = c.injuries ~= nil, last_match_read = c.last_match ~= nil,
        match_ratings_read = c.match_ratings ~= nil, flags_read = c.flags ~= nil }
end

local function wage_budget()
    if type(GetUserWageBudget) ~= "function" then return nil end
    return safe(function() return GetUserWageBudget() end, nil)
end

---------------------------------------------------------------- ro4 jobs (one heavy read per event tick)

-- Key-only scan calling on_row(row) for each match (no row cap here: callers keep small per-player caps).
local function scan_each(name, keyfield, match, want, on_row)
    if not SAFE_TABLES[name] then return false end
    local ok, t = pcall(function() return LE.db:GetTable(name) end)
    if not ok or t == nil then return false end
    local has = field_set(name)
    if not has[keyfield] then return false end
    local fields = {}
    for _, f in ipairs(want) do if has[f] then table.insert(fields, f) end end
    step("scan " .. name)
    local ok2, err = pcall(function()
        local rec, visited = t:GetFirstRecord(), 0
        while rec and rec > 0 and visited < MAX_SCAN do
            visited = visited + 1
            if match(t:GetRecordFieldValue(rec, keyfield)) then
                local row = {}
                for _, f in ipairs(fields) do
                    local okv, v = pcall(function() return t:GetRecordFieldValue(rec, f) end)
                    if okv then row[f] = v end
                end
                if on_row(row) == false then break end
            end
            rec = t:GetNextValidRecord()
        end
    end)
    step("scan " .. name .. " done")
    if not ok2 then log("scan " .. name .. " failed: " .. tostring(err)) end
    return ok2
end

local function squad_id_set()
    local set, n = {}, 0
    for _, r in ipairs(FC26CD.cache.squad_rows or {}) do
        if type(r.playerid) == "number" and r.playerid > 0 and not set[r.playerid] then set[r.playerid] = true; n = n + 1 end
    end
    return set, n
end

-- career_playerlastgrowth: one row per player; keep only injured squad players. Stops when every id was seen.
local function read_injuries()
    local set, n = squad_id_set()
    if n == 0 then return nil end
    local out, seen = {}, 0
    local ok = scan_each("career_playerlastgrowth", "playerid", function(v) return set[v] == true end,
        { "playerid", "injuryduration", "injurydate" }, function(r)
            seen = seen + 1
            if (r.injuryduration or 0) > 0 then out[r.playerid] = { days = r.injuryduration, date = r.injurydate } end
            if seen >= n then return false end
        end)
    return ok and out or nil
end

-- career_playerlastmatchhistory: the user team's last line-up (minutes, position, overall on the day).
local function read_last_match(teamid)
    local out, rows = {}, 0
    local ok = scan_each("career_playerlastmatchhistory", "teamid", function(v) return v == teamid end,
        { "playerid", "minsplayed", "position", "playeroverall" }, function(r)
            if r.playerid then out[r.playerid] = { mins = r.minsplayed, pos = position_name(r.position), ovr = r.playeroverall } end
            rows = rows + 1
            if rows >= MAX_SQUAD then return false end
        end)
    return ok and out or nil
end

-- career_playermatchratinghistory: newest MAX_RATINGS_PER_PLAYER rows per squad player.
local function read_match_ratings()
    local set, n = squad_id_set()
    if n == 0 then return nil end
    local by = {}
    local ok = scan_each("career_playermatchratinghistory", "playerid", function(v) return set[v] == true end,
        { "playerid", "date", "rating", "minsplayed" }, function(r)
            local l = by[r.playerid] or {}
            by[r.playerid] = l
            table.insert(l, { date = r.date, rating = r.rating, mins = r.minsplayed })
            table.sort(l, function(a, b) return (a.date or 0) > (b.date or 0) end)
            while #l > MAX_RATINGS_PER_PLAYER do table.remove(l) end
        end)
    return ok and by or nil
end

-- Documented getters (no table scan): transfer/loan listed, loaned out, presigned.
local function getter(name, cname)
    local f = _G[name]
    if type(f) == "function" then return f end
    f = _G[cname]
    if type(f) == "function" then return f end
    return nil
end
local function read_flags(teamid)
    local tl = getter("IsPlayerTransferListed", "cIsPlayerTransferListed")
    local ll = getter("IsPlayerLoanListed", "cIsPlayerLoanListed")
    local lo = getter("IsPlayerLoanedOut", nil)
    local ps = getter("IsPlayerPresigned", nil)
    local club = getter("GetTeamIdFromPlayerId", nil) -- documented (DOC.MD): the player's club
    local out, clubs = {}, {}
    step("flags")
    for pid in pairs((squad_id_set())) do
        local f = {}
        if club then clubs[pid] = safe(function() return club(pid) end, nil) end
        if tl and safe(function() return tl(pid, teamid) end, false) == true then f.transfer_listed = true end
        if ll and safe(function() return ll(pid, teamid) end, false) == true then f.loan_listed = true end
        if lo and safe(function() return lo(pid) end, false) == true then f.loaned_out = true end
        if ps and safe(function() return ps(pid) end, false) == true then f.presigned = true end
        if next(f) then out[pid] = f end
    end
    step("flags done")
    if club then FC26CD.cache.club_of = clubs end
    return out
end

-- This season's transfers/loans of the user club: LE's export_transfer_history.lua reader (TransferManager
-- negotiation storage, v26.3.5 offsets), every pointer and vector size checked, entries filtered to the user club.
local function vec_entries(storage, off, size)
    local vec = MEM:ReadPointer(storage + off)
    if not valid_ptr(vec) then return nil end
    local b, e = MEM:ReadPointer(vec + 0x0), MEM:ReadPointer(vec + 0x8)
    if not valid_ptr(b) or not valid_ptr(e) or e < b or (e - b) % size ~= 0 or (e - b) // size > MAX_VEC then return nil end
    return b, e
end
local function actions_last(cur, off)
    local ab, ae = MEM:ReadPointer(cur + off), MEM:ReadPointer(cur + off + 0x8)
    if not valid_ptr(ab) or not valid_ptr(ae) or ae <= ab or (ae - ab) > 0xC * 64 then return nil end
    return MEM:ReadChar(ae - 0xC + 0x8), MEM:ReadInt(ae - 0xC + 0x0)
end
-- ro7: yyyymmdd-looking words (2000-2100, valid month/day) among `ints`, latest not after today.
local function date_word(ints)
    local today, best = today_int(), nil
    for _, v in ipairs(ints) do
        if type(v) == "number" and v >= 20000101 and v <= 21001231 then
            local m, d = (v // 100) % 100, v % 100
            if m >= 1 and m <= 12 and d >= 1 and d <= 31 and (today == 0 or v <= today) and (best == nil or v > best) then
                best = v
            end
        end
    end
    return best
end
local function ints_at(p, n)
    local t = {}
    for i = 0, n - 1 do t[#t + 1] = MEM:ReadInt(p + 4 * i) end
    return t
end
-- Last element of a vector at cur+off (begin) / cur+off+8 (end) of element size `size`: its words and the count.
local function last_elem(cur, off, size, max_n)
    local b, e = MEM:ReadPointer(cur + off), MEM:ReadPointer(cur + off + 0x8)
    if not valid_ptr(b) or not valid_ptr(e) or e <= b or (e - b) % size ~= 0 or (e - b) // size > max_n then return nil, 0 end
    return ints_at(e - size, size // 4), (e - b) // size
end
-- Vector at cur+off (begin) / cur+off+8 (end) of element size `size`: begin, end, count (nil when not sane).
local function sub_vec(cur, off, size, max_n)
    local b, e = MEM:ReadPointer(cur + off), MEM:ReadPointer(cur + off + 0x8)
    if not valid_ptr(b) or not valid_ptr(e) or e < b or (e - b) % size ~= 0 or (e - b) // size > max_n then return nil end
    return b, e, (e - b) // size
end
local function status_of(sa, ba, pending)
    return (sa and ba) and "accepted" or sa and "accepted by us (seller)"
        or ba and "buyer accepted our asking price" or pending
end
-- Offers received. ro6/ro7 only walked the AI-club vectors (+0x8 transfers, +0x18 loans): those hold negotiations
-- between two AI clubs. A negotiation in which the user club takes part (buying OR selling) lives in the user-club
-- vectors (LE's export_transfer_history.lua): transfers +0x28 (0xA0: offers 0x18/0x20 of 0x28 elements, fee at +0xC,
-- exchange player/value at +0x0/+0x4; requests 0x38/0x40; actions 0x58/0x60 of 0xC elements: date +0x0, code +0x8,
-- 0 = seller accepted, 4 = buyer accepted) and loans +0x30 (0xF8: actions 0x50/0x58). ro8 reads all four with
-- sell == user club. Received date = first action date, status from the last action (raw codes kept).
local MAX_NEGOTIATIONS = 40 -- user-club negotiation entries kept (received + sent)
local MAX_TALKS = 20        -- player-contract talks kept
-- Negotiation action codes (registry round 3, mapped on 2026-08-29..09-01 against the Transfer Hub screens). Codes are
-- by role, not by club: buyer = the club that bids, seller = the club that owns the player.
local OFFER_CODES = { [6] = "buyer offer", [2] = "seller counter (asking terms)", [3] = "seller on hold",
    [0] = "seller accepted", [4] = "buyer accepted seller's terms", [1] = "seller rejected (or auto-rejected at window close)",
    [5] = "ended by buyer / expired" }
local function read_offers(teamid)
    if not mem_ok() then return nil end
    if not MEM.open or MEM.failed then return nil end
    local mgr = GetManagerObjByTypeId(ENUM_FCEGameModesFCECareerModeTransferManager) -- never cached (ro7)
    if not valid_ptr(mgr) then return nil end
    local storage = MEM:ReadPointer(mgr + OFFS.tm_storage)
    if not valid_ptr(storage) then return nil end
    step("offers")
    local out = {}
    -- User club transfers (+0x28) and loans (+0x30): the AI club bids for our player.
    for _, v in ipairs({ { 0x28, 0xA0, "transfer", 0x58 }, { 0x30, 0xF8, "loan", 0x50 } }) do
        local b, e = vec_entries(storage, v[1], v[2])
        local cur = b
        while b and cur < e and #out < MAX_NEGOTIATIONS do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            local received = sell == teamid and buy ~= teamid
            local sent = buy == teamid and sell ~= teamid -- ro11: our own bids live in the same vectors
            if pid > 0 and buy > 0 and sell > 0 and (received or sent) then
                local o = { type = v[3], playerid = pid, club_id = received and buy or sell, buy_id = buy, sell_id = sell,
                    direction = received and "received" or "sent", source = string.format("user_club +0x%X", v[1]) }
                local ab, ae, n_act = sub_vec(cur, v[4], 0xC, 64)
                local first_date, last_code
                if ab and n_act > 0 then
                    first_date = MEM:ReadInt(ab + 0x0)
                    last_code = MEM:ReadChar(ae - 0xC + 0x8)
                    o.actions = {}
                    for i = 0, n_act - 1 do
                        table.insert(o.actions, { date = MEM:ReadInt(ab + 0xC * i), code = MEM:ReadChar(ab + 0xC * i + 0x8) })
                    end
                end
                if v[3] == "transfer" then
                    local ob, oe, n_off = sub_vec(cur, 0x18, 0x28, 64)
                    if ob and n_off > 0 then
                        local fee = MEM:ReadInt(oe - 0x28 + 0xC)
                        o.amount = fee > 0 and fee or nil
                        local xp = MEM:ReadInt(oe - 0x28 + 0x0)
                        if xp > 0 then o.exchange_playerid = xp; o.exchange_value = MEM:ReadInt(oe - 0x28 + 0x4) end
                    end
                    local rb, re, n_req = sub_vec(cur, 0x38, 0x28, 64)
                    if rb and n_req > 0 then
                        local ask = MEM:ReadInt(re - 0x28 + 0xC)
                        o.asking = ask > 0 and ask or nil
                    end
                    o.offers_count, o.requests_count = n_off or 0, n_req or 0
                else
                    -- loan offers: vector at +0x10 (one 0x24-byte element seen); words kept to decode the option to buy
                    o.option_to_buy = "undecoded"
                end
                o.date = first_date and fixture_date_str(first_date) or nil
                o.date_source = first_date and "first negotiation action" or nil
                o.last_action = last_code
                o.status = OFFER_CODES[last_code] or ("code " .. tostring(last_code))
                o.open = last_code == 6 or last_code == 2 or last_code == 3
                table.insert(out, o)
            end
            cur = cur + v[2]
        end
    end
    -- AI club vectors (ro6/ro7 path, kept): only hit if the game ever stores a bid for our player there.
    for _, v in ipairs({ { 0x8, "transfer", 0x6E, 0x6F }, { 0x18, "loan", 0x72, 0x73 } }) do
        local b, e = vec_entries(storage, v[1], 0xB8)
        local cur = b
        while b and cur < e and #out < MAX_TRANSFERS do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            if pid > 0 and buy > 0 and sell == teamid and buy ~= teamid then
                local sa, ba = MEM:ReadBool(cur + v[3]), MEM:ReadBool(cur + v[4])
                local entry = ints_at(cur, 0xB8 // 4)
                local off, n_off = last_elem(cur, 0x20, 0xC, 64)
                local req, n_req = last_elem(cur, 0x40, 0x14, 64)
                local o = { type = v[2], playerid = pid, club_id = buy, seller_accepted = sa, buyer_accepted = ba,
                    offers_count = n_off, requests_count = n_req, source = string.format("ai_club +0x%X", v[1]) }
                local fee = off and off[1] or nil
                o.amount = (fee and fee > 0) and fee or nil          -- nil = fee not set
                o.asking = req and req[1] and req[1] > 0 and req[1] or nil
                if v[2] == "loan" then o.option_to_buy = "undecoded" end
                local dw = date_word(off or {}) or date_word(entry)
                o.date = dw and fixture_date_str(dw) or nil
                o.date_source = dw and "heuristic: yyyymmdd word in the negotiation" or nil
                o.status = status_of(sa, ba, n_req > 0 and "negotiating" or "pending")
                table.insert(out, o)
            end
            cur = cur + 0xB8
        end
    end
    step("offers done")
    for _, o in ipairs(out) do
        o.player = safe(function() return GetPlayerName(o.playerid) end, nil)
        o.club = safe(function() return GetTeamName(o.club_id) end, nil)
    end
    table.sort(out, function(a, b) return (a.date or "") > (b.date or "") end)
    return out
end
-- ro11: player-contract talks of deals that involve the user club (LE export_transfer_history.lua layouts).
-- user_player transfers +0x38 / exchanges +0x48 (0x98): actions 0x50/0x58 (0xC: date +0x0, code +0x8);
-- ai_player transfers +0x10 (0xB0): buyer_accepted 0x64, seller_accepted 0x67, action idx 0x6C, dates 0x70 + 0xC*i;
-- ai_player loans +0x20 (0x98): seller_accepted 0x52, action idx 0x57, dates 0x58 + 0xC*(i-1).
local function read_player_talks(teamid)
    if not mem_ok() then return nil end
    if not MEM.open or MEM.failed then return nil end
    local mgr = GetManagerObjByTypeId(ENUM_FCEGameModesFCECareerModeTransferManager)
    if not valid_ptr(mgr) then return nil end
    local storage = MEM:ReadPointer(mgr + OFFS.tm_storage)
    if not valid_ptr(storage) then return nil end
    local out = {}
    local function base(cur, name, size)
        local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
        if pid <= 0 or buy <= 0 or sell <= 0 or (buy ~= teamid and sell ~= teamid) then return nil end
        return { vector = name, playerid = pid, buy_id = buy, sell_id = sell }
    end
    for _, v in ipairs({ { 0x38, "user_player_transfer" }, { 0x48, "user_player_exchange" } }) do
        local b, e = vec_entries(storage, v[1], 0x98)
        local cur = b
        while b and cur < e and #out < MAX_TALKS do
            local t = base(cur, v[2], 0x98)
            if t then
                local ab, ae, n = sub_vec(cur, 0x50, 0xC, 64)
                if ab and n > 0 then
                    t.actions = {}
                    for i = 0, n - 1 do
                        table.insert(t.actions, { date = MEM:ReadInt(ab + 0xC * i), code = MEM:ReadChar(ab + 0xC * i + 0x8) })
                    end
                end
                table.insert(out, t)
            end
            cur = cur + 0x98
        end
    end
    for _, v in ipairs({ { 0x10, 0xB0, "ai_player_transfer", 0x64, 0x67, 0x6C, 0x70, 0 },
                         { 0x20, 0x98, "ai_player_loan", nil, 0x52, 0x57, 0x58, -1 } }) do
        local b, e = vec_entries(storage, v[1], v[2])
        local cur = b
        while b and cur < e and #out < MAX_TALKS do
            local t = base(cur, v[3], v[2])
            if t then
                t.buyer_accepted = v[4] and MEM:ReadBool(cur + v[4]) or nil
                t.seller_accepted = MEM:ReadBool(cur + v[5])
                t.action_idx = MEM:ReadChar(cur + v[6])
                local idx = t.action_idx + v[8]
                if idx >= 0 and idx < 16 then t.last_action_date = MEM:ReadInt(cur + v[7] + 0xC * idx) end
                table.insert(out, t)
            end
            cur = cur + v[2]
        end
    end
    for _, t in ipairs(out) do t.player = safe(function() return GetPlayerName(t.playerid) end, nil) end
    return out
end
local function read_transfers(teamid, all)
    if not mem_ok() then return nil end
    if not MEM.open or MEM.failed then return nil end
    local mgr = GetManagerObjByTypeId(ENUM_FCEGameModesFCECareerModeTransferManager)
    if not valid_ptr(mgr) then return nil end
    local storage = MEM:ReadPointer(mgr + OFFS.tm_storage)
    if not valid_ptr(storage) then return nil end
    step("transfers")
    local found, fees = {}, {}
    local function add(kind, pid, buy, sell, date)
        if not all and buy ~= teamid and sell ~= teamid then return end
        found[string.format("%s%d-%d-%d", kind, pid, buy, sell)] = { type = kind == "T" and "transfer" or "loan",
            playerid = pid, to_id = buy, from_id = sell, date = date }
    end
    -- AI player transfers / exchanges / loans: seller_accepted flag + action date by index.
    for _, v in ipairs({ { 0x10, 0xB0, 0x6C, 0x70, 0, "T" }, { 0x40, 0xA8, 0x6B, 0x6C, -1, "T" }, { 0x20, 0x98, 0x57, 0x58, -1, "L" } }) do
        local b, e = vec_entries(storage, v[1], v[2])
        local acc = v[6] == "L" and 0x52 or 0x67
        local cur = b
        while b and cur < e do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            if pid > 0 and buy > 0 and sell > 0 and MEM:ReadBool(cur + acc) then
                local idx = MEM:ReadChar(cur + v[3]) + v[5]
                local date = (idx >= 0 and idx < 16) and MEM:ReadInt(cur + v[4] + 0xC * idx) or nil
                add(v[6], pid, buy, sell, date)
            end
            cur = cur + v[2]
        end
    end
    -- User player transfers / exchanges: last action 0 (seller accepted) or 4 (buyer accepted).
    for _, off in ipairs({ 0x38, 0x48 }) do
        local b, e = vec_entries(storage, off, 0x98)
        local cur = b
        while b and cur < e do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            if pid > 0 and buy > 0 and sell > 0 then
                local last, date = actions_last(cur, 0x50)
                if last == 0 or last == 4 then add("T", pid, buy, sell, date) end
            end
            cur = cur + 0x98
        end
    end
    -- Fees: AI club negotiations (0xB8) and user club negotiations (0xA0).
    do
        local b, e = vec_entries(storage, 0x8, 0xB8)
        local cur = b
        while b and cur < e do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            if pid > 0 and (all or buy == teamid or sell == teamid) then
                local sa, ba = MEM:ReadBool(cur + 0x6E), MEM:ReadBool(cur + 0x6F)
                local fee
                if sa then
                    local p = MEM:ReadPointer(cur + 0x28)
                    if valid_ptr(p) then fee = MEM:ReadInt(p - 0xC) end
                elseif ba then
                    local p = MEM:ReadPointer(cur + 0x48)
                    if valid_ptr(p) then fee = MEM:ReadInt(p - 0x14) end
                end
                if fee then fees[string.format("T%d-%d-%d", pid, buy, sell)] = fee end
            end
            cur = cur + 0xB8
        end
        b, e = vec_entries(storage, 0x28, 0xA0)
        cur = b
        while b and cur < e do
            local pid, buy, sell = MEM:ReadInt(cur), MEM:ReadInt(cur + 0x4), MEM:ReadInt(cur + 0x8)
            if pid > 0 and (all or buy == teamid or sell == teamid) then
                local last = actions_last(cur, 0x58)
                local p = (last == 0 and MEM:ReadPointer(cur + 0x20)) or (last == 4 and MEM:ReadPointer(cur + 0x40)) or nil
                if valid_ptr(p) then fees[string.format("T%d-%d-%d", pid, buy, sell)] = MEM:ReadInt(p - 0x28 + 0xC) end
            end
            cur = cur + 0xA0
        end
    end
    step("transfers done")
    local out = {}
    for k, t in pairs(found) do
        t.fee = fees[k]
        t.date = t.date and fixture_date_str(t.date) or nil
        table.insert(out, t)
    end
    table.sort(out, function(a, b) return (a.date or "") > (b.date or "") end)
    local cap = all and MAX_WORLD_TRANSFERS or MAX_TRANSFERS
    while #out > cap do table.remove(out) end
    if not all then
        for _, t in ipairs(out) do
            t.player = safe(function() return GetPlayerName(t.playerid) end, nil)
            t.to = safe(function() return GetTeamName(t.to_id) end, nil)
            t.from = safe(function() return GetTeamName(t.from_id) end, nil)
        end
    end
    return out
end

local MEM_JOBS = { transfers = true, offers = true } -- TransferManager pointer walks
local JOB_ORDER = { "squad", "squad_ratings", "injuries", "last_match", "match_ratings", "flags", "transfers", "offers",
    "targets", "career", "history" }

-- Players referenced by open offers, our bids and contract talks that are not squad players: one key-only
-- players scan (same reader as squad_ratings), MAX_TARGETS ids, cached until the id set changes.
local function target_ids()
    local c, squad = FC26CD.cache, (squad_id_set())
    local ids, seen, n = {}, {}, 0
    for _, list in ipairs({ c.offers or {}, c.bids or {}, c.player_talks or {} }) do
        for _, o in ipairs(list) do
            local pid = o.playerid
            if type(pid) == "number" and pid > 0 and not squad[pid] and not seen[pid] and n < MAX_TARGETS then
                seen[pid] = true; n = n + 1; table.insert(ids, pid)
            end
        end
    end
    table.sort(ids)
    return ids
end
local function read_targets()
    local ids = target_ids()
    if #ids == 0 then return {} end
    local want = {}
    for _, pid in ipairs(ids) do want[pid] = true end
    local fields = { "playerid" }
    for _, f in ipairs(PLAYER_KEYS) do table.insert(fields, f) end
    local rows = scan_rows("players", "playerid", function(v) return want[v] == true end, #ids, fields, true) or {}
    local by = {}
    for _, r in ipairs(rows) do if r.playerid then by[r.playerid] = r end end
    FC26CD.cache.targets_key = table.concat(ids, ",")
    return by
end
local function targets_list()
    local out = {}
    for pid, r in pairs(FC26CD.cache.targets or {}) do
        local info = player_basic(pid)
        info.position = position_name(r.preferredposition1)
        info.age = age_from_birthdate(r.birthdate)
        info.overall, info.potential = r.overallrating, r.potential
        info.attributes = r
        info.why = "referenced by an offer, bid or contract talk; not a squad player"
        table.insert(out, info)
    end
    table.sort(out, function(a, b) return (a.overall or 0) > (b.overall or 0) end)
    return out
end

-- Runs the next pending job round-robin (one heavy read), so a job that is re-flagged often cannot starve the
-- others. A failed job is not retried until it is flagged again. Returns the job name or nil.
run_job = function(teamid)
    local c, need = FC26CD.cache, FC26CD.need
    if c.squad_rows == nil then need.squad = true end
    local start = FC26CD.job_next or 1
    for k = 0, #JOB_ORDER - 1 do
        local idx = (start - 1 + k) % #JOB_ORDER + 1
        local j = JOB_ORDER[idx]
        if need[j] and (MEM.open or not MEM_JOBS[j]) then
            need[j] = false
            local ok = true
            step("job " .. j)
            if j == "squad" then
                local rows = safe(function() return squad_contracts(teamid) end, nil)
                if rows then c.squad_rows = rows else ok = false end
                FC26CD.squad_day = today_int()
                local have = c.squad_ratings or {}
                for _, r in ipairs(c.squad_rows or {}) do
                    if have[r.playerid] == nil then need.squad_ratings = true; break end
                end
            elseif j == "squad_ratings" then
                -- full refresh monthly (or first read), else only players missing from the cache (new signings)
                local full, have, ids = FC26CD.full_ratings or c.squad_ratings == nil, c.squad_ratings or {}, {}
                for _, r in ipairs(c.squad_rows or {}) do
                    if full or have[r.playerid] == nil then table.insert(ids, r.playerid) end
                end
                local got = safe(function() return squad_ratings(ids) end, nil)
                if got then
                    local merged = full and {} or have
                    for pid, row in pairs(got) do merged[pid] = row end
                    c.squad_ratings = merged
                    FC26CD.full_ratings = false
                else ok = false end
            else
                local fn = ({ injuries = read_injuries, match_ratings = read_match_ratings,
                    last_match = function() return read_last_match(teamid) end,
                    flags = function() return read_flags(teamid) end,
                    transfers = function() return read_transfers(teamid) end,
                    offers = function() return read_offers(teamid) end,
                    targets = read_targets,
                    career = function() local w = c.who or {}; return w.userid ~= nil and player_career_row(w.userid) or nil end,
                    history = function() local w = c.who or {}; return w.userid ~= nil and player_history(w.userid) or nil end })[j]
                local res = safe(fn, nil)
                if res ~= nil then c[j] = res else ok = false end -- nil = failed/unreadable: keep old value
            end
            step("job " .. j .. " done")
            FC26CD.job_ran = true
            FC26CD.job_next = idx % #JOB_ORDER + 1
            FC26CD.last_job = { name = j, ok = ok, game_date = game_date(), wall = os.date("!%Y-%m-%dT%H:%M:%SZ") }
            return j
        end
    end
    return nil
end

local function pending_reads()
    local out = {}
    for _, j in ipairs(JOB_ORDER) do if FC26CD.need[j] then table.insert(out, j) end end
    return out
end

---------------------------------------------------------------- filtered career event log (names only, no payload)

local EVENT_PATTERNS = { "TRANSFER", "OFFER", "BID", "CONTRACT", "RENEW", "LOAN", "UNHAPPY", "MORALE", "FORM_CHANGE",
    "INJUR", "SUSPENSION", "BOARD", "OBJECTIVE", "EMAIL", "DEADLINE", "SACK", "FIRED", "WAGE", "SCOUT", "RELEASE",
    "YOUTH", "PROMOT" }
local EVENT_NOISE = { CPU_MATCH_COMPLETED = true, YOUTH_SCOUT_IS_SENT_ON_MISSION = true }
-- ro5: real roster moves only (ro4 also matched bids, counter offers and contract-renewal talks: dozens a day).
local REFRESH_SQUAD_EVENTS = { "TRANSFER_MOVE", "LOAN_MOVE", "COMPLETE", "SIGNED", "SIGNING", "RELEASE", "RETIRE",
    "PRESIGN", "LOAN_RETURN", "PROMOT", "YOUTH_PLAYER", "CALL_UP", "CALLED_UP" }
local NOT_ROSTER_MOVE = { "BID", "OFFER", "NEGOTIAT", "RENEWAL", "UNHAPPY", "MORALE", "REJECT", "CPU_" }

local function name_has(name, list)
    for _, pat in ipairs(list) do if name:find(pat, 1, true) then return true end end
    return false
end

-- One entry per event name per game day (count increments), last MAX_EVENTS entries.
local function log_event(name)
    if name_has(name, REFRESH_SQUAD_EVENTS) and not name_has(name, NOT_ROSTER_MOVE)
        and FC26CD.squad_day ~= today_int() then FC26CD.need.squad = true end -- at most one contract re-read a day
    if EVENT_NOISE[name] or not name_has(name, EVENT_PATTERNS) then return end
    local ev, day = FC26CD.events, game_date() or "?"
    for i = #ev, math.max(1, #ev - 15), -1 do
        if ev[i].name == name and ev[i].date == day then ev[i].count = ev[i].count + 1; return end
    end
    table.insert(ev, { date = day, name = name, count = 1 })
    while #ev > MAX_EVENTS do table.remove(ev, 1) end
end

---------------------------------------------------------------- snapshot

-- ro7: offers are a cheap pointer walk: read on Run Code and on every snapshot outside the match flow.
local function refresh_offers(teamid)
    local c = FC26CD.cache
    local ok, res = pcall(read_offers, teamid)
    if ok and res then
        local open, hist, bids = {}, {}, {}
        for _, o in ipairs(res) do
            if o.direction == "sent" then table.insert(bids, o)
            elseif o.open ~= false then table.insert(open, o)
            elseif #hist < MAX_TRANSFERS then table.insert(hist, o) end
        end
        c.offers = open; c.offers_history = hist; c.bids = bids
        c.player_talks = safe(function() return read_player_talks(teamid) end, nil)
        if table.concat(target_ids(), ",") ~= (c.targets_key or "") then FC26CD.need.targets = true end
        c.offers_read_at = game_date(); c.offers_error = nil; FC26CD.need.offers = false
    else
        c.offers_error = ok and "transfer manager not ready" or tostring(res)
    end
end

local function build_snapshot(reason, allow_scan)
    local user_id = GetUserTeamID()
    local d, derr = current_date()
    local snap = {
        schema = 4, product = PRODUCT, bridge = VERSION, reason = reason, le_version = LE_VER,
        compat = compat_level(), memory_reads = mem_ok(), compat_reason = compat_reason(),
        written = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        game_date = date_str(d), date_error = derr,
        game_date_source = "live GetCurrentDate (preferred; the save-file date is only an estimate)",
        user_team = { id = user_id, name = safe(function() return GetTeamName(user_id) end, nil) },
        transfer_budget = safe(function() return GetUserTransferBudget() end, nil),
        wage_budget = wage_budget(),
        wage_budget_note = "no documented getter in LE v26.3.5 (null = unreadable live; the game shows it in Finance)",
        board = "unreadable live: no documented board/objective getter (Test Round 1: take it from Board Objectives)",
    }
    step("snapshot user")
    local mode = safe(detect_user, "unknown")
    local who = FC26CD.cache.who or {}
    snap.mode = mode
    snap.user = { name = who.surname, firstname = who.firstname, seasons = who.seasoncount,
        club_teamid = who.clubteamid, usertype = who.usertype, playertype = who.playertype }
    step("snapshot memory")
    local mc = FC26CD.cache
    if MEM.open then
        mem_walk("snapshot memory", function()
            local standings = read_standings()
            local fx = team_fixtures(standings, user_id, 5, 3)
            snap.last_results = fx.last_results
            snap.next_fixtures = fx.next_fixtures
            snap.next_fixture = fx.next_fixtures[1]
            snap.league_table = comp_table(standings, user_id, nil)
        end)
        if not MEM.failed and snap.next_fixtures then
            mc.mem_snap = { last_results = snap.last_results, next_fixtures = snap.next_fixtures,
                next_fixture = snap.next_fixture, league_table = snap.league_table, read_at = date_str(d),
                wall = os.date("!%Y-%m-%dT%H:%M:%SZ"), source = "opt_in" }
        end
    elseif mc.mem_snap then -- deferred: last good memory read, marked by snap.memory
        snap.last_results = mc.mem_snap.last_results; snap.next_fixtures = mc.mem_snap.next_fixtures
        snap.next_fixture = mc.mem_snap.next_fixture; snap.league_table = mc.mem_snap.league_table
    end
    snap.memory = { state = (not MEM.open and "deferred") or (MEM.failed and "failed") or "fresh",
        reason = MEM.why or MEM.failed, read_at = mc.mem_snap and mc.mem_snap.read_at,
        read_wall = mc.mem_snap and mc.mem_snap.wall, source = mc.mem_snap and mc.mem_snap.source }
    if mode == "player" then
        step("snapshot player")
        local c, need = FC26CD.cache, FC26CD.need
        local player_scan = allow_scan and (need.ratings or need.attributes or c.ratings == nil or c.attributes == nil)
        snap.user_player = safe(function() return user_player_info(player_scan) end, nil)
        if player_scan then allow_scan = false end -- one scan per snapshot; the squad jobs use the other ticks
    end
    step("snapshot squad") -- both modes: the user's club squad (player career: club context)
    snap.squad = safe(function() return squad_info(user_id, allow_scan) end, nil)
    snap.memory.enabled = MEM.open
    snap.memory.cached = not MEM.open and mc.mem_snap ~= nil
    snap.events = FC26CD.events
    snap.events_note = "GLOBAL event names (no payload): never attribute them to the user's club or a player"
    snap.raw_events = FC26CD.raw_events
    snap.event_payloads = FC26CD.payloads
    snap.pending_reads = pending_reads()
    snap.last_job = FC26CD.last_job
    if mode == "manager" then
        if MEM.open and not FC26CD.in_match then step("snapshot offers"); refresh_offers(user_id) end
        snap.transfers = FC26CD.cache.transfers
        snap.offers = FC26CD.cache.offers
        snap.offers_history = FC26CD.cache.offers_history
        snap.bids = FC26CD.cache.bids
        snap.player_talks = FC26CD.cache.player_talks
        snap.targets = targets_list()
        snap.offers_read_at = FC26CD.cache.offers_read_at
        snap.offers_error = FC26CD.cache.offers_error
    end
    step("snapshot done")
    return snap
end

local function write_snapshot(reason, allow_scan)
    local snap = build_snapshot(reason, allow_scan)
    local ok, text = pcall(json.encode, snap)
    if ok and #text < 300000 then
        write_atomic(SNAPSHOT_PATH, text)
    else
        log("snapshot not written: " .. (ok and "size limit" or tostring(text)))
    end
    return snap
end

---------------------------------------------------------------- event payloads

-- LE's id -> name table (consts.lua) does not match FC 26 v1.6.3 past id ~35 (id 157 "USER_TRANSFER_OFFER_RECEIVED"
-- carries injury payloads), so names cannot select events: every id is dumped (per id/day cap) except DAY_PASSED.
local PAYLOAD_SKIP = { [15] = true } -- DAY_PASSED only; the per id/day cap bounds the rest
-- per id/day caps above the default (league-wide bursts that may contain our league)
local PAYLOAD_CAP_BY_ID = { [53] = 40, [65] = 20, [167] = 60, [169] = 60, [175] = 20 }
-- field maps by event id (byte offset -> field), registry round 6 (ingame_round6.json). Ids are FC 26 v1.6.3 runtime
-- ids; LE's names for them are wrong past ~35, so each map carries its own label. "verified" = matched on a screen.
local PAYLOAD_DECODE = {
    [157] = { label = "injury (global)", [0x1C] = "teamid", [0x24] = "playerid", [0x28] = "days_out",
        [0x2C] = "return_date", [0x30] = "severity?", [0x34] = "injury_type?" },
    [65] = { label = "back_from_injury", [0x1C] = "teamid", [0x20] = "playerid" },
    [53] = { label = "user_squad_player_ovr", [0x18] = "playerid", [0x1C] = "ovr", [0x20] = "ovr_b" },
    [175] = { label = "league_stat_leaders_story", [0x18] = "stat_kind?", [0x1C] = "compobjid",
        [0x20] = "p1_playerid", [0x24] = "p1_teamid", [0x28] = "p1_value",
        [0x2C] = "p2_playerid", [0x30] = "p2_teamid", [0x34] = "p2_value" },
    [20] = { label = "suspension", [0x1C] = "teamid", [0x20] = "playerid", [0x24] = "matches" },
    [21] = { label = "back_from_suspension", [0x1C] = "teamid", [0x20] = "playerid" },
    [129] = { label = "transfer_offer_received", [0x18] = "bidder_teamid", [0x1C] = "playerid",
        [0x20] = "owner_teamid" },
    [130] = { label = "loan_offer_received", [0x18] = "bidder_teamid", [0x1C] = "playerid", [0x20] = "owner_teamid" },
    [159] = { label = "transfer_completed", [0x1C] = "to_teamid", [0x20] = "from_teamid", [0x24] = "playerid",
        [0x28] = "wage", [0x2C] = "fee", [0x30] = "contract_years" },
}

local function payload_addr(ev)
    if type(ev) == "number" then return ev end
    local s = tostring(ev)
    local h = s:match("0x(%x+)") or s:match("(%x%x%x%x%x%x%x%x+)")
    return h and tonumber(h, 16) or nil
end

local function dump_payload(event_id, ename, ev)
    if not MEM.open or MEM.failed then return end
    if PAYLOAD_SKIP[event_id] then return end
    local day = today_int()
    if FC26CD.payload_day ~= day then FC26CD.payload_count = {}; FC26CD.payload_day = day end
    local cnt = FC26CD.payload_count or {}
    FC26CD.payload_count = cnt
    local key = day .. ":" .. tostring(event_id)
    if (cnt[key] or 0) >= (PAYLOAD_CAP_BY_ID[event_id] or PAYLOAD_PER_NAME_DAY) then return end
    local addr = payload_addr(ev)
    local rec = { t = os.time(), date = day, id = event_id, name = ename, ev_type = type(ev) }
    if type(ev) ~= "number" then rec.ev_str = tostring(ev):sub(1, 60) end
    if type(addr) == "number" and addr >= 0x10000 and addr < 0x7FFFFFFFFFFF then
        rec.addr = string.format("0x%X", addr)
        local bytes = payload_bytes(addr, PAYLOAD_BYTES)
        if type(bytes) == "table" and #bytes >= PAYLOAD_BYTES then
            local hx, w = {}, {}
            for i = 1, PAYLOAD_BYTES do hx[i] = string.format("%02X", bytes[i] & 0xFF) end
            rec.hex = table.concat(hx)
            local squad = {}
            for _, r in ipairs(FC26CD.cache.squad_rows or {}) do squad[r.playerid] = true end
            local team = FC26CD.cache.user_team or safe(GetUserTeamID, nil)
            local m = {}
            for o = 0, PAYLOAD_BYTES - 4, 4 do
                local v = string.unpack("<i4", string.char(bytes[o + 1] & 0xFF, bytes[o + 2] & 0xFF,
                    bytes[o + 3] & 0xFF, bytes[o + 4] & 0xFF))
                w[#w + 1] = v
                local tag
                if squad[v] then tag = "squad_pid"
                elseif v == team and v ~= 0 then tag = "user_team"
                elseif v > 0 and comp_names[v] ~= nil then tag = "comp:" .. tostring(comp_names[v])
                elseif v >= 20200101 and v <= 20351231 and v % 100 >= 1 and v % 100 <= 31 then tag = "date_ymd"
                elseif v >= 160000 and v <= 165000 then tag = "date_greg?" end
                if tag then m[#m + 1] = { off = string.format("0x%X", o), v = v, tag = tag } end
            end
            rec.w = w
            rec.matches = m
            local ours = false
            local dec = PAYLOAD_DECODE[event_id]
            for offset, field in pairs(dec or {}) do
                if type(offset) == "number" then
                    local value = w[offset // 4 + 1]
                    if (field:find("playerid", 1, true) and squad[value])
                        or (field:find("teamid", 1, true) and team and team > 0 and value == team)
                        or (field == "playerid" and value == FC26CD.cache.pid) then ours = true end
                end
            end
            rec.ours = ours or nil
            -- Bounded per-id/day payload sampling, including user-related events.
            if not ours and (cnt[key] or 0) >= (PAYLOAD_CAP_BY_ID[event_id] or PAYLOAD_PER_NAME_DAY) then return end
            cnt[key] = (cnt[key] or 0) + 1
            local dec = PAYLOAD_DECODE[event_id]
            if dec then
                rec.fields = { kind = dec.label }
                for o, f in pairs(dec) do if type(o) == "number" then rec.fields[f] = w[o // 4 + 1] end end
            end
        else
            rec.err = "short read"
        end
    else
        rec.err = "no address"
    end
    if rec.err then
        if (cnt[key] or 0) >= PAYLOAD_PER_NAME_DAY then return end
        cnt[key] = (cnt[key] or 0) + 1
    end
    local f = io.open(PAYLOAD_PATH, "a")
    if f then
        local size = f:seek("end") or 0
        if size < PAYLOAD_MAX_FILE then
            local okj, txt = pcall(json.encode, rec)
            if okj then f:write(txt, "\n") end
        end
        f:close()
    end
    local keep = FC26CD.payloads or {}
    FC26CD.payloads = keep
    if not rec.fields and not rec.ours then return end -- snapshot keeps decoded or squad-related payloads only
    keep[#keep + 1] = { date = rec.date, id = event_id, scope = rec.ours and "ours" or "global",
        fields = rec.fields, matches = rec.ours and rec.matches or nil, err = rec.err }
    if #keep > MAX_PAYLOADS then table.remove(keep, 1) end
end

---------------------------------------------------------------- events

local PRE_MATCH = {
    [ENUM_CM_EVENT_MSG_ABOUT_TO_ENTER_PREMATCH] = true,
    [ENUM_CM_EVENT_MSG_ABOUT_TO_SIM_OVER_A_MATCH] = true,
}
local POST_MATCH = {
    [ENUM_CM_EVENT_MSG_USER_MATCH_COMPLETED] = true,
    [ENUM_CM_EVENT_MSG_POST_MATCH_REPORTS_DISPLAYED] = true,
}

-- ro7 load guard (same crash class as the experimental write bridge: a game call while a career was loading).
local function reset_state()
    FC26CD.booted = nil -- autorun: bootstrap again after a career (re)load
    FC26CD.cache = {}
    FC26CD.raw_events = {}; FC26CD.payloads = {}; FC26CD.payload_count = {}
    FC26CD.names = nil; FC26CD.world_day = nil; FC26CD.world_due = true
    FC26CD.mem_due = true; FC26CD.payload_day = nil
    comp_names = {}
    FC26CD.events = {}
    FC26CD.need = { ratings = true, attributes = true, squad = true, squad_ratings = true, injuries = true,
        last_match = true, match_ratings = true, flags = true, transfers = true, offers = true, targets = false,
        career = false, history = false }
    FC26CD.mem_disabled = nil -- a new career load gets a fresh chance (the LE version did not change)
    FC26CD.mem_reason = nil
    FC26CD.in_match = false
    FC26CD.day_due = nil; FC26CD.post_match_due = nil
    FC26CD.job_next = 1
    FC26CD.full_ratings = true
    FC26CD.squad_day = nil
    FC26CD.last_job = nil
    FC26CD.last_month = nil
    FC26CD.last_week = nil
end
---------------------------------------------------------------- 1.0.5: on-demand world read (read_now)

local function file_exists(path)
    local f = io.open(path, "r")
    if f then f:close(); return true end
    return false
end
local function read_now_result(res)
    res.bridge = VERSION
    res.wall = os.date("!%Y-%m-%dT%H:%M:%SZ")
    res.unix = os.time()
    write_atomic(ROOT .. "\\read_now_result.json", json.encode(res))
end
-- Refuses (result + delete) or postpones a pending read_now request; true only when it may run now.
read_now_ready = function()
    local req = ROOT .. "\\read_now"
    local f = io.open(req, "r")
    if not f then return false end
    local body = f:read("*a") or ""
    f:close()
    local id, ts = body:match("^%s*(%S+)%s+(%d+)")
    local refuse
    if file_exists(ROOT .. "\\disable_read_now") then refuse = "disabled (disable_read_now exists)"
    elseif FC26CD.read_now_blocked then refuse = "blocked until the game restarts: " .. FC26CD.read_now_blocked
    elseif OFFS == nil then refuse = "Live Editor " .. LE_VER .. " is not supported for memory reads"
    elseif FC26CD.mem_disabled then refuse = "memory reads disabled this session: " .. tostring(FC26CD.mem_reason)
    elseif file_exists(ROOT .. "\\read_now_inflight") then
        refuse = "a previous read_now did not finish (game closed or crashed mid-read?); delete read_now_inflight to allow"
    elseif not ts or os.time() - tonumber(ts) > READ_NOW_MAX_AGE_S then refuse = "expired or malformed request" end
    if refuse then
        os.remove(req)
        read_now_result({ id = id, ok = false, error = refuse })
        return false
    end
    local now, why = os.time(), nil
    if FC26CD.in_match then why = "match"
    elseif FC26CD.sim_until and now < FC26CD.sim_until then why = "multi_day_sim"
    elseif FC26CD.day_due or FC26CD.post_match_due then why = "day_or_match_work_pending"
    elseif FC26CD.risky_at and now - FC26CD.risky_at < READ_NOW_QUIET_S then why = "settling"
    elseif not FC26CD.hub_at or now - FC26CD.hub_at < READ_NOW_QUIET_S then why = "hub_settling" end
    if why then
        FC26CD.read_now_wait = why
        heartbeat("read_now_waiting_" .. why, true)
        return false
    end
    FC26CD.read_now_id = id
    return true
end

do_read_now = function(ename)
    local id, t0 = FC26CD.read_now_id, os.clock()
    local inflight = ROOT .. "\\read_now_inflight"
    os.remove(ROOT .. "\\read_now") -- consumed before the first read: never repeated, even if the game dies below
    write_atomic(inflight, tostring(id) .. " " .. os.time())
    step("read_now start")
    local res = { id = id, event = ename }
    MEM.failed = nil; MEM.standings = nil; MEM.why = nil; MEM.open = true
    FC26CD.read_now_on = true
    local ok, err = pcall(function()
        local user_id = GetUserTeamID()
        local standings = read_standings()
        local fx = team_fixtures(standings, user_id, 5, 3)
        local tbl = comp_table(standings, user_id, nil)
        assert(not MEM.failed, MEM.failed)
        local w = write_world("read_now", true)
        assert(not MEM.failed and not FC26CD.mem_disabled, MEM.failed or FC26CD.mem_reason)
        res.world_written = w.written == true
        res.tables = w.tables and #w.tables or 0
        res.fixtures_played = w.fixtures_played_total
        res.fixtures_upcoming = w.fixtures_upcoming_total
        local d = current_date()
        FC26CD.cache.mem_snap = { last_results = fx.last_results, next_fixtures = fx.next_fixtures,
            next_fixture = fx.next_fixtures[1], league_table = tbl, read_at = date_str(d),
            wall = os.date("!%Y-%m-%dT%H:%M:%SZ"), source = "read_now" }
        res.game_date = date_str(d)
    end)
    FC26CD.read_now_on = nil
    MEM.open = false; MEM.standings = nil
    if ok and not MEM.failed then
        MEM.why = "off (last read: read_now)"
        local okw = pcall(write_snapshot, "read_now", false)
        res.snapshot_written = okw
        res.ok = res.world_written == true
        if not res.ok then res.error = "world.json not written" end
    else
        res.ok = false
        res.error = tostring(MEM.failed or err)
        FC26CD.read_now_blocked = res.error -- abort: no further read_now in this game session
    end
    MEM.failed = nil
    res.seconds = os.clock() - t0
    os.remove(inflight)
    read_now_result(res)
    step("read_now done ok=" .. tostring(res.ok))
    log("read_now " .. tostring(res.ok) .. " " .. tostring(res.error or ""))
end

local function is_career_load(name)
    return name_has(name, { "POST_LOAD", "PRE_LOAD", "LOAD_CAREER", "CAREER_LOAD", "LOAD_SAVE", "LOADING_CAREER" })
end

local CAREER_EXIT_PARTS = { "PREPARE_FOR_SAVE", "PREPARE_FOR_FIRST_SAVE", "EXIT", "QUIT", "MAIN_MENU",
    "NEW_CAREER", "CREATE", "INIT_CAREER", "CAREER_START", "START_CAREER", "SHUTDOWN", "DESTROY",
    "ABOUT_TO_INIT_MODE", "CAREER_TYPE_SELECTED", "INITIAL_USER_ADDED" }

function FC26CD_OnEvent(events_manager, event_id, event)
    if FC26CD.handling then return end
    FC26CD.handling = true
    tick_date_read = false
    local ok, err = pcall(function()
        local ename = ""
        pcall(function() ename = GetCMEventNameByID(event_id) or "" end)
        local ring = FC26CD.raw_events or {}
        FC26CD.raw_events = ring
        ring[#ring + 1] = { t = os.time(), id = event_id, name = ename }
        if #ring > 300 then table.remove(ring, 1) end
        if is_busy_event(ename) or PRE_MATCH[event_id] or POST_MATCH[event_id] then note_busy(ename) end
        if is_career_load(ename) or ename:find("SAVE", 1, true) or name_has(ename, CAREER_EXIT_PARTS) then
            FC26CD.risky_at = os.time() -- 1.0.5: read_now waits READ_NOW_QUIET_S after these
        end
        if is_career_load(ename) or (name_has(ename, CAREER_EXIT_PARTS) and not ename:find("CPU_", 1, true)) then
            reset_state()
            MEM.open = false; MEM.standings = nil
            FC26CD.hub_at = nil; FC26CD.loaded = nil -- wait for a new hub event
            heartbeat("waiting_hub", true)
            return
        end
        -- ro9 (rw-exp5 rule): before the hub is reached no event makes a game call (LOAD events included); once
        -- ENTERED_HUB_FIRST_TIME + HUB_SETTLE_S has passed every event counts, SCREEN_HAS_DONE_LOADING included
        -- (ro7/ro8 skipped every *LOAD* event, so in the hub only DAY_PASSED could start the autorun read).
        -- 1.0.3: match / day flags need no game call; set them here, act on the next quiet event.
        if PRE_MATCH[event_id] then FC26CD.in_match = true end
        if POST_MATCH[event_id] then FC26CD.in_match = false; FC26CD.post_match_due = true end
        if ename == "DAY_PASSED" then FC26CD.in_match = false; FC26CD.day_due = true end
        if not FC26CD.hub_at then
            if ename:find("ENTERED_HUB", 1, true) then
                FC26CD.hub_at = os.time() -- flags only; IsInCM runs after settling
            end
            heartbeat("waiting_hub", true)
            return
        end
        -- 1.0.3: no game call during a match flow, a multi-day sim or the settle time after a busy event.
        if is_busy_event(ename) or PRE_MATCH[event_id] or POST_MATCH[event_id] or quiet_gate() then
            heartbeat(quiet_gate() or "busy_event", true)
            return
        end
        local okcm, incm = pcall(IsInCM)
        if not okcm or not incm then FC26CD.hub_at = nil; FC26CD.loaded = nil; return end
        if os.time() - FC26CD.hub_at < HUB_SETTLE_S then return end
        if not FC26CD.loaded then reset_state(); FC26CD.loaded = true end -- fresh state for this career load
        if not career_ready() then return end
        -- 1.0.5: one-shot standings/fixtures read on request, in a tick of its own (no payload, no other work)
        if FC26CD.booted and read_now_ready() then do_read_now(ename); return end
        mem_tick() -- ro15: memory open only if the gate allows it this tick
        local okp, errp = pcall(dump_payload, event_id, ename, event)
        if not okp then log("payload error: " .. tostring(errp)) end
        if FC26CD.autorun and not FC26CD.booted then
            local okb, errb = pcall(bootstrap, "autorun_hub") -- calls reset_state itself
            FC26CD.booted = true -- once per career load, even on error (reset_state on a new load clears it)
            if not okb then log("bootstrap error: " .. tostring(errb)) end
            return
        end
        heartbeat("event")
        log_event(ename)
        local need = FC26CD.need
        if ename:find("INJUR", 1, true) or ename:find("UNFIT", 1, true) then need.injuries = true end
        if not ename:find("CPU_", 1, true) and name_has(ename, { "TRANSFER_MOVE", "LOAN_MOVE", "COMPLETE", "SIGN", "RELEASE", "LISTED", "PRESIGN" })
            and name_has(ename, { "TRANSFER", "LOAN", "SIGN", "RELEASE", "LIST" }) then
            need.flags = true; need.transfers = true
        end
        if not ename:find("CPU_", 1, true) and name_has(ename, { "OFFER", "BID" }) and not ename:find("JOB_", 1, true) then
            need.offers = true
        end
        FC26CD.job_ran = false
        if FC26CD.post_match_due then
            FC26CD.post_match_due = false
            FC26CD.need.ratings = true
            need.last_match = true; need.match_ratings = true; need.injuries = true -- read on the next game days
            write_snapshot("post_match", false)
        elseif FC26CD.day_due then -- 1.0.3: deferred DAY_PASSED work, on a quiet hub event after the settle time
            FC26CD.day_due = false
            local d = current_date()
            local month = d and (d.year * 100 + d.month) or nil
            if month and FC26CD.last_month ~= month then
                FC26CD.last_month = month; FC26CD.need.attributes = true; FC26CD.need.squad_ratings = true
                FC26CD.full_ratings = true; need.transfers = true
                if FC26CD.cache.mode == "player" then need.history = true end
            end
            local week = d and (os.time({ year = d.year, month = d.month, day = d.day, hour = 12 }) // (7 * 86400)) or nil
            if week and FC26CD.last_week ~= week then
                FC26CD.last_week = week; need.injuries = true; need.flags = true; need.offers = true
                if FC26CD.cache.mode == "player" then need.career = true end
                local have = FC26CD.cache.squad_ratings or {}
                for _, r in ipairs(FC26CD.cache.squad_rows or {}) do
                    if have[r.playerid] == nil then need.squad_ratings = true; break end -- missing-only lookup
                end
            end
            write_snapshot("day_passed", true) -- at most one DB scan per game day
            if FC26CD.world_day ~= today_int() then -- both modes: world.json is global data
                FC26CD.world_due = true -- written by the memory refresh once the game has settled
            end
        end
        -- ro15: deferred memory refresh, on a harmless event at least MEM_SETTLE_S after the last busy event
        if MEM.open and (FC26CD.mem_due or FC26CD.world_due) then
            write_snapshot("memory_refresh", false)
            FC26CD.mem_due = MEM.failed ~= nil
            if FC26CD.world_due and not MEM.failed then
                FC26CD.world_due = false
                safe(function() write_world("memory_refresh") end)
            end
        end
    end)
    MEM.open = false; MEM.standings = nil
    FC26CD.handling = false
    if not ok then log("handler error: " .. tostring(err)) end
end

---------------------------------------------------------------- world (ro10: P1 / P2 / P3 / P3b)

local function season_label()
    local d = current_date()
    if not d then return nil end
    local y = d.month >= 7 and d.year or d.year - 1
    return string.format("%d/%02d", y, (y + 1) % 100)
end

-- Some game names are not valid UTF-8 (seen: "FCV Farul Constan\x9ba"): replace stray bytes so world.json parses.
local function clean(n)
    if type(n) ~= "string" then return n end
    local ok, len = pcall(utf8.len, n)
    if ok and len then return n end
    return (n:gsub("[\128-\255]", "?"))
end

-- Names cached for the game session (team and player names do not change); calls bounded per write.
local function make_namer(budget)
    FC26CD.names = FC26CD.names or { team = {}, player = {} }
    return function(kind, id)
        if type(id) ~= "number" or id <= 0 then return nil end
        local c = FC26CD.names[kind]
        if c[id] == nil then
            if budget.n <= 0 then return nil end
            budget.n = budget.n - 1
            c[id] = safe(function()
                if kind == "team" then return GetTeamName(id) end
                return GetPlayerName(id)
            end, false) or false
            if c[id] then c[id] = clean(c[id]) end
        end
        return c[id] or nil
    end
end

-- P1: every standings group (one per compobjid), sorted by pts, goal difference, goals for.
local function world_tables(standings, season, namer, stage_of)
    local groups = {}
    for _, s in pairs(standings) do
        if s.used and s.teamid > 0 then
            groups[s.compobjid] = groups[s.compobjid] or {}
            table.insert(groups[s.compobjid], s)
        end
    end
    local out = {}
    for cid, rows in pairs(groups) do
        local played_any = false
        for _, s in ipairs(rows) do if s.w + s.d + s.l > 0 then played_any = true end end
        if played_any then for _, s in ipairs(rows) do s.name = namer("team", s.teamid) end end
        -- full ties (pts, GD, GF) ordered by team name: matches the PL screen of 2026-08-26 (Bournemouth above
        -- Crystal Palace); the game's real rule (head-to-head?) is unconfirmed, rows carry tied = true
        table.sort(rows, function(a, b)
            if a.pts ~= b.pts then return a.pts > b.pts end
            if (a.gf - a.ga) ~= (b.gf - b.ga) then return (a.gf - a.ga) > (b.gf - b.ga) end
            if a.gf ~= b.gf then return a.gf > b.gf end
            if (a.name or "") ~= (b.name or "") then return (a.name or "") < (b.name or "") end
            return a.teamid < b.teamid
        end)
        local games, rs = 0, {}
        for i, s in ipairs(rows) do
            local p = s.w + s.d + s.l
            games = games + p
            local prev = rows[i - 1]
            table.insert(rs, { pos = i, teamid = s.teamid, team = s.name, p = p, w = s.w, d = s.d, l = s.l, gf = s.gf,
                ga = s.ga, gd = s.gf - s.ga, pts = s.pts, tied = (prev and prev.pts == s.pts and prev.gf == s.gf
                and prev.ga == s.ga) or nil })
        end
        -- standings ids are stage ids (PL table 823); the competition id (821) comes from fixtures that point at
        -- these standings rows (no guessing from id ranges)
        local parent = stage_of[cid]
        table.insert(out, { compobjid = cid, stage_compobjid = cid, competition_compobjid = parent,
            competition = clean(parent and comp_name(parent) or comp_name(cid)), stage_name = clean(comp_name(cid)),
            kind = #rs >= 10 and "league" or (#rs > 2 and "group" or "tie"), season = season, teams = #rs,
            games_played = games // 2, rows = rs })
    end
    table.sort(out, function(a, b) return a.compobjid < b.compobjid end)
    return out
end

-- P3: every fixture (all clubs, all competitions) with match group id and penalties.
local function world_fixtures(standings, season, namer)
    local list = MEM:ReadPointer(fce_data_manager() + OFFS.fixtures)
    assert(valid_ptr(list), "fixture list not ready")
    local base, n = MEM:ReadPointer(list + 0x28), MEM:ReadInt(list + 0x1C)
    assert(valid_ptr(base), "fixture list base not ready")
    assert(n >= 0 and n <= 8000, "fixture list size out of range (" .. tostring(n) .. "): layout mismatch?")
    local played, upcoming, per_comp, stage_of = {}, {}, {}, {}
    for i = 0, n - 1 do
        local p = base + 0x18 * i
        if MEM:ReadBool(p + 0x14) then
            local hs, as = standings[MEM:ReadShort(p + 0x0A)], standings[MEM:ReadShort(p + 0x0C)]
            if hs and as then
                local cid, is_played = MEM:ReadShort(p + 0x08), MEM:ReadBool(p + 0x13)
                stage_of[hs.compobjid] = stage_of[hs.compobjid] or cid
                stage_of[as.compobjid] = stage_of[as.compobjid] or cid
                -- stage_compobjid = the standings group / cup tie this fixture belongs to (PL 823, a Carabao tie 696)
                local f = { date = MEM:ReadInt(p + 0x00), compobjid = cid, stage_compobjid = hs.compobjid,
                    group = MEM:ReadChar(p + 0x0E),
                    home_id = hs.teamid, away_id = as.teamid, played = is_played }
                if is_played then
                    f.home_score, f.away_score = MEM:ReadChar(p + 0x0F), MEM:ReadChar(p + 0x11)
                    local hp, ap = MEM:ReadChar(p + 0x10), MEM:ReadChar(p + 0x12)
                    if hp ~= 255 and ap ~= 255 and (hp > 0 or ap > 0) then f.home_pens, f.away_pens = hp, ap end -- 255 = none
                    table.insert(played, f)
                else
                    table.insert(upcoming, f)
                end
                local pc = per_comp[cid] or { compobjid = cid, fixtures = 0, played = 0, groups = {} }
                per_comp[cid] = pc
                pc.fixtures = pc.fixtures + 1
                if is_played then pc.played = pc.played + 1 end
                pc.groups[tostring(f.group)] = (pc.groups[tostring(f.group)] or 0) + 1
            end
        end
    end
    table.sort(played, function(a, b) return a.date > b.date end)
    table.sort(upcoming, function(a, b) return a.date < b.date end)
    local out = {}
    for i = 1, math.min(#played, MAX_WORLD_PLAYED) do table.insert(out, played[i]) end
    for i = 1, math.min(#upcoming, MAX_WORLD_UPCOMING) do table.insert(out, upcoming[i]) end
    for _, f in ipairs(out) do
        f.season = season
        f.date = fixture_date_str(f.date)
        f.competition = clean(comp_name(f.compobjid))
        f.home, f.away = namer("team", f.home_id), namer("team", f.away_id)
        if f.played then f.score = string.format("%d-%d", f.home_score, f.away_score) end
    end
    local pcs = {}
    for _, pc in pairs(per_comp) do pc.competition = clean(comp_name(pc.compobjid)); table.insert(pcs, pc) end
    table.sort(pcs, function(a, b) return a.compobjid < b.compobjid end)
    return out, pcs, #played, #upcoming, stage_of
end

-- Primary positions (players.preferredposition1, 0 = GK) for clean-sheet candidates: one key-only players scan
-- (same reader as squad_ratings), cached for the game session, capped at MAX_WORLD_POS_IDS new ids per write.
local MAX_WORLD_POS_IDS = 600
local function positions_for(ids)
    FC26CD.positions = FC26CD.positions or {}
    local cache, want, n = FC26CD.positions, {}, 0
    for pid in pairs(ids) do
        if cache[pid] == nil and n < MAX_WORLD_POS_IDS then want[pid] = true; n = n + 1 end
    end
    if n > 0 then
        local rows = scan_rows("players", "playerid", function(v) return want[v] == true end, n,
            { "playerid", "preferredposition1" }, true) or {}
        for _, r in ipairs(rows) do if r.playerid then cache[r.playerid] = r.preferredposition1 end end
    end
    return cache
end

-- P2: top 10 per competition (goals, assists, clean sheets, average rating with app >= 3).
local function world_top(season, namer)
    local by_comp = {}
    for _, s in ipairs(GetPlayersStats()) do
        learn_comp_name(s.compobjid, s.compname)
        if s.playerid and s.playerid > 0 and s.playerid < 4294967295 and (s.app or 0) > 0 then
            by_comp[s.compobjid] = by_comp[s.compobjid] or {}
            table.insert(by_comp[s.compobjid], s)
        end
    end
    local cs_ids = {}
    for _, rows in pairs(by_comp) do
        for _, s in ipairs(rows) do if (s.clean_sheets or 0) > 0 then cs_ids[s.playerid] = true end end
    end
    local pos = safe(function() return positions_for(cs_ids) end, {}) or {}
    local function top(rows, key, filter)
        local r = {}
        for _, s in ipairs(rows) do if (not filter or filter(s)) and (s[key] or 0) > 0 then table.insert(r, s) end end
        table.sort(r, function(a, b)
            if (a[key] or 0) ~= (b[key] or 0) then return (a[key] or 0) > (b[key] or 0) end
            if (a.app or 0) ~= (b.app or 0) then return (a.app or 0) < (b.app or 0) end
            return a.playerid < b.playerid
        end)
        local o = {}
        for i = 1, math.min(10, #r) do
            local s = r[i]
            table.insert(o, { rank = i, playerid = s.playerid, teamid = s.teamid, player = namer("player", s.playerid),
                team = namer("team", s.teamid), value = s[key], app = s.app, goals = s.goals, assists = s.assists,
                clean_sheets = s.clean_sheets })
        end
        return o
    end
    local out = {}
    for cid, rows in pairs(by_comp) do
        for _, s in ipairs(rows) do s.avg_rating = math.floor((s.avg or 0) / s.app + 0.5) / 10 end -- LE: avg / app / 10
        table.insert(out, { compobjid = cid, competition = clean(comp_name(cid)), season = season, players = #rows,
            goals = top(rows, "goals"), assists = top(rows, "assists"),
            -- the game's clean sheet table lists goalkeepers only; GetPlayersStats counts them for outfield players
            -- too (ro10 first build: saves/goals_conceded did not separate them). GK = players.preferredposition1 0
            clean_sheets = top(rows, "clean_sheets", function(s) return pos[s.playerid] == 0 end),
            clean_sheets_all_positions = top(rows, "clean_sheets"),
            avg_rating = top(rows, "avg_rating", function(s) return (s.app or 0) >= WORLD_MIN_APPS_AVG end) })
    end
    table.sort(out, function(a, b) return a.compobjid < b.compobjid end)
    return out
end

-- P3b: every club's transfers this season (cap 400) and transfer income/expense per club (known fees only).
local function world_transfers(season, namer)
    local list = read_transfers(0, true) or {}
    local clubs = {}
    local function club(id)
        clubs[id] = clubs[id] or { teamid = id, season = season, income = 0, expense = 0, sold = 0, bought = 0,
            loans_in = 0, loans_out = 0, fee_unknown = 0 }
        return clubs[id]
    end
    for _, t in ipairs(list) do
        t.season = season
        t.player = namer("player", t.playerid)
        t.to, t.from = namer("team", t.to_id), namer("team", t.from_id)
        local buyer, seller = club(t.to_id), club(t.from_id)
        if t.type == "loan" then
            buyer.loans_in = buyer.loans_in + 1; seller.loans_out = seller.loans_out + 1
        else
            buyer.bought = buyer.bought + 1; seller.sold = seller.sold + 1
            if t.fee and t.fee > 0 then
                buyer.expense = buyer.expense + t.fee; seller.income = seller.income + t.fee
            else
                buyer.fee_unknown = buyer.fee_unknown + 1; seller.fee_unknown = seller.fee_unknown + 1
            end
        end
    end
    local fin = {}
    for _, c in pairs(clubs) do c.team = namer("team", c.teamid); table.insert(fin, c) end
    table.sort(fin, function(a, b) return (a.income + a.expense) > (b.income + b.expense) end)
    return list, fin
end

write_world = function(reason, no_transfers)
    step("world")
    local season = season_label()
    local namer = make_namer({ n = MAX_WORLD_NAMES })
    local w = { schema = "fc26-world-1", product = PRODUCT, bridge = VERSION, reason = reason, game_date = game_date(), season = season,
        user_team = GetUserTeamID(),
        keys = "tables: stage_compobjid (+competition_compobjid from fixtures)+season+teamid; top: compobjid+season+"
            .. "playerid+teamid; fixtures: compobjid+stage_compobjid+season+group (league matchday; cups: undecoded)+home_id+"
            .. "away_id+date; "
            .. "transfers: playerid+from_id+to_id+season; club_transfer_finance: teamid+season (known fees only)" }
    w.compat, w.memory_reads = compat_level(), mem_ok()
    local standings = mem_walk("world standings", read_standings, nil)
    if standings then
        step("world fixtures")
        local stage_of = {}
        mem_walk("world fixtures", function()
            w.fixtures, w.fixtures_per_competition, w.fixtures_played_total, w.fixtures_upcoming_total, stage_of =
                world_fixtures(standings, season, namer)
        end)
        step("world tables")
        w.tables = safe(function() return world_tables(standings, season, namer, stage_of or {}) end, nil)
    end
    step("world top"); w.top = safe(function() return world_top(season, namer) end, nil)
    if no_transfers then
        w.transfers_note = "not read (" .. reason .. ": standings / fixtures / leaders only)"
    else
        step("world transfers")
        safe(function() w.transfers, w.club_transfer_finance = world_transfers(season, namer) end)
    end
    local ok, text = pcall(json.encode, w)
    if ok and not MEM.failed and not (no_transfers and FC26CD.mem_disabled) and write_atomic(WORLD_PATH, text) then
        FC26CD.world_day = today_int()
        w.written = true
    else
        FC26CD.world_due = true
        log("world not written: " .. tostring(MEM.failed or (not ok and text) or "file error"))
    end
    step("world done")
    return w
end

---------------------------------------------------------------- main

-- Full first read (squad scan, ratings, transfers, offers). Execute: right away. Autorun: on the first
-- career event once the hub has settled (see FC26CD_OnEvent), never at load time.
bootstrap = function(reason)
    step(reason .. " start")
    ensure_dirs()
    mem_tick() -- resolve opt-in after config redirects the root
    reset_state()
    FC26CD.loaded = true
    local mode = safe(detect_user, "unknown")                -- career_users + career_playasplayer (1 row each)
    write_status(reason)
    heartbeat(reason)
    build_snapshot(reason, true)                       -- player: ratings scan / manager: squad contracts scan
    if mode == "manager" then
        local ids = {}
        for _, r in ipairs(FC26CD.cache.squad_rows or {}) do table.insert(ids, r.playerid) end
        FC26CD.cache.squad_ratings = safe(function() return squad_ratings(ids) end, nil)
        FC26CD.need.squad_ratings = false
        FC26CD.full_ratings = false
        FC26CD.squad_day = today_int()
        -- ro7: memory walks + documented getters right away (ro6 queued them as jobs that never ran)
        local tid = GetUserTeamID()
        FC26CD.cache.transfers = safe(function() return read_transfers(tid) end, nil)
        FC26CD.cache.flags = safe(function() return read_flags(tid) end, nil) -- also club_of (squad membership)
        FC26CD.need.transfers = FC26CD.cache.transfers == nil
        FC26CD.need.flags = FC26CD.cache.flags == nil
        refresh_offers(tid)
        if FC26CD.need.targets then FC26CD.cache.targets = safe(read_targets, nil); FC26CD.need.targets = false end
        FC26CD.job_next = 3 -- squad + squad_ratings done above: injuries next
        -- the other jobs (injuries, last match, ratings, flags, transfers) follow, one per DAY_PASSED
    elseif mode == "player" then
        FC26CD.cache.attributes = safe(function() return player_attributes(user_player_id()) end, nil)
        FC26CD.need.attributes = false
        local uid = (FC26CD.cache.who or {}).userid
        if uid ~= nil then FC26CD.cache.history = safe(function() return player_history(uid) end, nil) end
        -- the club squad (contracts, ratings, injuries...) fills in over the next game days (snapshot.pending_reads)
    end
    if MEM.open then safe(function() write_world(reason) end) -- both modes
    else FC26CD.world_due = true end
    FC26CD.mem_due = not MEM.open or MEM.failed ~= nil
    local snap = write_snapshot(reason, false)
    step(reason .. " done")
    return snap, mode
end

local function install_handler()
    if FC26CD.handler_installed then return end
    local ok = pcall(AddEventHandler, "post__CareerModeEvent", function(m, id, ev) FC26CD_OnEvent(m, id, ev) end)
    FC26CD.handler_installed = ok
    log("event handler installed: " .. tostring(ok))
end

if AUTORUN then
    -- Autorun at injection (game start): define + register only. No game API call and no file IO here;
    -- FC26CD_OnEvent waits for ENTERED_HUB_FIRST_TIME + HUB_SETTLE_S, then runs bootstrap once per career load.
    FC26CD.autorun = true
    install_handler()
    return
end

if quiet_gate() then
    MessageBox("FC26 bridge", "Bridge is settling or in match flow. Return to the career hub and wait 10 seconds.")
    return
end
ensure_dirs()
local ready, why = career_ready()
if not ready then
    heartbeat("manual_run")
    MessageBox("FC26 Career MCP bridge " .. VERSION, "Career not ready (" .. why .. ").\nOpen the career hub, then run again.")
    return
end
FC26CD.hub_at = os.time() - HUB_SETTLE_S -- Run Code is pressed in the career hub: settled
mem_tick()
local snap, mode = bootstrap("manual_run")
FC26CD.booted = true
install_handler()

local summary = "Mode: " .. tostring(mode) .. "\nDate: " .. tostring(snap.game_date or snap.date_error)
    .. "\nLive Editor: " .. LE_VER .. " (" .. compat_level() .. (mem_ok() and "" or ": " .. tostring(compat_reason())) .. ")"
if snap.squad then summary = summary .. "\nSquad: " .. tostring(snap.squad.count) .. " players" end
if snap.pending_reads and #snap.pending_reads > 0 then
    summary = summary .. "\nMore data (" .. table.concat(snap.pending_reads, ", ") .. ") fills in over the next game days."
end
MessageBox("FC26 Career MCP bridge " .. VERSION, "Bridge ready (read-only).\n" .. summary .. "\nData folder: " .. ROOT ..
    "\nFiles refresh on career events (day passed, matches) until the game closes.")
