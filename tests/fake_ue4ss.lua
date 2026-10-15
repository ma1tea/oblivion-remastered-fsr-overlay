-- Fake of the slice of the UE4SS 3.0.1 Lua API that main.lua uses, for
-- tests/run_tests.py. Nothing here runs in the game.
--
-- The fake has a notion of "the game thread": ExecuteInGameThread callbacks
-- run with H.inGameThread == true, RegisterKeyBind and ExecuteWithDelay
-- callbacks with it false (as in the real UE4SS). Every engine access made
-- with it false is recorded in H.violations.

H = {
    log = {},            -- every print() line
    notifications = {},  -- AddNotification texts
    commands = {},       -- ExecuteConsoleCommand command strings, in order
    delays = {},         -- ExecuteWithDelay delays, in order
    violations = {},     -- engine accesses made off the game thread
    inGameThread = false,
    gameQueue = {},      -- pending ExecuteInGameThread callbacks
    timers = {},         -- pending ExecuteWithDelay callbacks
    keyBinds = {},
    consoleHandlers = {},

    -- Knobs the tests turn.
    config = nil,        -- config.txt content, nil = file does not exist
    openedPaths = {},
    cvars = { ["Altar.FSR3.Enabled"] = 1.0, ["Altar.FSR3.Quality"] = 1.0 },
    -- Consecutive r.ScreenPercentage reads; an entry is a value to return
    -- or a function to call (to raise). The last entry repeats.
    pctReads = { 100.0, 66.7 },
    pctReadCount = 0,
    failCommands = {},   -- command string -> error message to raise
    engineReady = true,
    raiseIn = {},        -- API name -> error message to raise
    now = 1000,          -- what os.time() returns, in seconds
}

-- The tests move the clock themselves instead of sleeping.
os.time = function()
    return H.now
end

local function engineAccess(name)
    if not H.inGameThread then
        table.insert(H.violations, name)
    end
    if H.raiseIn[name] then
        error(H.raiseIn[name])
    end
end

print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[#parts + 1] = tostring((select(i, ...)))
    end
    table.insert(H.log, table.concat(parts, "\t"))
end

Key = { OEM_PLUS = 187, F9 = 120 }

function RegisterKeyBind(key, callback)
    assert(type(key) == "number", "No overload found... integer key")
    H.keyBinds[key] = callback
end

function RegisterConsoleCommandHandler(name, callback)
    H.consoleHandlers[name] = callback
end

function ExecuteInGameThread(callback)
    if H.raiseIn.ExecuteInGameThread then
        error(H.raiseIn.ExecuteInGameThread)
    end
    table.insert(H.gameQueue, callback)
end

function ExecuteWithDelay(ms, callback)
    if H.raiseIn.ExecuteWithDelay then
        error(H.raiseIn.ExecuteWithDelay)
    end
    table.insert(H.delays, ms)
    table.insert(H.timers, callback)
end

function FText(text)
    return { text = text }
end

local viewModel = {}
function viewModel:IsValid() return true end
function viewModel:AddNotification(args)
    engineAccess("AddNotification")
    table.insert(H.notifications, args.Text.text)
end

function FindFirstOf(className)
    engineAccess("FindFirstOf")
    if className == "VHUDSubtitleViewModel" then
        return viewModel
    end
    return nil
end

local kismet = {}
function kismet:IsValid() return H.engineReady end
function kismet:ExecuteConsoleCommand(worldContext, command, player, writeToLog)
    engineAccess("ExecuteConsoleCommand")
    -- Gotcha #2: all four parameters are required.
    assert(worldContext ~= nil and writeToLog ~= nil, "UFunction expected 4 parameters")
    if H.failCommands[command] then
        error(H.failCommands[command])
    end
    table.insert(H.commands, command)
end
function kismet:GetConsoleVariableFloatValue(name)
    engineAccess("GetConsoleVariableFloatValue")
    if name == "r.ScreenPercentage" then
        H.pctReadCount = H.pctReadCount + 1
        local value = H.pctReads[math.min(H.pctReadCount, #H.pctReads)]
        if type(value) == "function" then
            return value()
        end
        return value
    end
    local value = H.cvars[name]
    if type(value) == "function" then
        return value()
    end
    -- Gotcha #5: an unknown name reads as 0, without an error.
    return value or 0
end

local engine = {}
function engine:IsValid() return H.engineReady end

package.preload["UEHelpers"] = function()
    return {
        GetKismetSystemLibrary = function()
            engineAccess("GetKismetSystemLibrary")
            return kismet
        end,
        GetEngine = function()
            engineAccess("GetEngine")
            return engine
        end,
    }
end

-- config.txt comes from H.config instead of the disk.
io.open = function(path, mode)
    table.insert(H.openedPaths, path)
    if H.config == nil then
        return nil, path .. ": No such file or directory"
    end
    local content = H.config
    local file = {}
    function file:lines()
        local pos = 1
        return function()
            if pos > #content then
                return nil
            end
            local stop = content:find("\n", pos, true)
            local line
            if stop then
                line = content:sub(pos, stop - 1)
                pos = stop + 1
            else
                line = content:sub(pos)
                pos = #content + 1
            end
            return line
        end
    end
    function file:close() end
    return file
end

-- Drivers.

function H.pressHotkey()
    H.inGameThread = false
    H.keyBinds[Key.OEM_PLUS]()
end

function H.runConsoleCommand(name)
    -- The real handler is called from the game thread.
    H.inGameThread = true
    local handled = H.consoleHandlers[name](name, {}, nil)
    H.inGameThread = false
    return handled
end

function H.runGameQueue()
    while #H.gameQueue > 0 do
        local callback = table.remove(H.gameQueue, 1)
        H.inGameThread = true
        callback()
        H.inGameThread = false
    end
end

function H.fireNextTimer()
    local callback = table.remove(H.timers, 1)
    if callback then
        H.inGameThread = false
        callback()
    end
    return callback ~= nil
end

function H.pump()
    repeat
        H.runGameQueue()
    until not H.fireNextTimer() and #H.gameQueue == 0
end

function H.raiser(message)
    return function() error(message) end
end
