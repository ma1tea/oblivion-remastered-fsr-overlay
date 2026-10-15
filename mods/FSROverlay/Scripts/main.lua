-- FSROverlay v10
--
-- Ground truth: r.ScreenPercentage == 100 when the upscaler is off/fell back
-- to native, ~66.7 when FSR Quality is actively upscaling (confirmed
-- manually in-game).
--
-- v5 switched the on-screen indicator to the game's own native notification
-- popup (VHUDSubtitleViewModel:AddNotification, the same mechanism other
-- already-working installed mods use), since the raw Unreal debug-draw
-- mechanisms (PrintString/AddOnScreenDebugMessage, DrawDebugString) executed
-- without error but never actually drew anything in this Shipping build.
--
-- v6 made the hotkey actively enforce FSR instead of only reporting status.
--
-- v7 makes the forced preset configurable via config.txt instead of
-- hardcoding Quality, using the AMD FSR3 enum confirmed directly by the user
-- for Altar.FSR3.Quality on this build:
--   0 = Native AA (no upscaling), 1 = Quality, 2 = Balanced,
--   3 = Performance, 4 = UltraPerformance.
-- Native AA (0) is intentionally not offered as a config choice - it
-- defeats the point of a mod whose job is forcing FSR on.
--
-- config.txt is re-read on every hotkey press (not just at mod load), so
-- edits apply without restarting the game - Scripts/main.lua itself doesn't
-- hot-reload in this build (EnableHotReloadSystem = 0), but a plain
-- io.open() read is a runtime operation and isn't affected by that.
--
-- v8 fixes a real bug from v7: when Options > Display has Upscaling Method
-- set fully to "Off" (not FSR-at-NativeAA, a genuinely different state),
-- v7 still unconditionally ran Altar.FSR3.Quality <preset> every press. That
-- cvar write apparently isn't a no-op even when the method is off - it was
-- observed nudging things toward Native AA with no real upscaling benefit.
-- v8 gated ALL of Altar.UpscalingMethod/Altar.FSR3.Quality writes behind
-- Altar.FSR3.Enabled (a real boolean cvar off the FSR3 plugin itself,
-- confirmed live in-game: 0 or 1) - skip forcing entirely if it read 0.
--
-- v9 reverses that: Altar.FSR3.Enabled only reflects the *setting*, not the
-- live render state, and Oblivion frequently bugs into rendering native
-- while this cvar still happily reports 1. Gating on it meant the mod could
-- see r.ScreenPercentage stuck at 100 and still do nothing. r.ScreenPercentage
-- is now the only gate for whether to force - Altar.FSR3.Enabled is purely
-- informational (logged, and included in the failure message). Forcing
-- also now bounces Altar.UpscalingMethod off FSR and back on (0 -> 2) before
-- setting quality when the render was stuck at native, since writing the
-- same value it's already at can be a no-op that doesn't re-initialize the
-- FSR3 backend.
--
-- v10 is hardening only, the three status messages and the sequence itself
-- are unchanged:
--   * RegisterKeyBind and ExecuteWithDelay callbacks don't run on the game
--     thread, so every engine access now goes through ExecuteInGameThread.
--   * r.ScreenPercentage outside (0, 100] (0 is what an unknown cvar name
--     reads as), a non-number or a failed read is "could not be read", with
--     its own notification, instead of being reported as FSR ON or raising.
--   * a UTF-8 BOM at the start of config.txt no longer hides the value.
--   * a press while the previous sequence is still running is ignored.
--   * failed console writes are logged, and a failed "on" write after the
--     bounce-off gets a notification that the upscaler may be left off.
--   * a sequence that never finished stops blocking the hotkey after 5 s.
--   * without ExecuteInGameThread the mod still works, as v9 did.

local UEHelpers = require("UEHelpers")

local PRESETS = {
    Quality = 1,
    Balanced = 2,
    Performance = 3,
    UltraPerformance = 4,
}

local PRESET_NAMES_BY_VALUE = {}
for name, value in pairs(PRESETS) do
    PRESET_NAMES_BY_VALUE[value] = name
end

local DEFAULT_PRESET = "Quality"

-- The process CWD when Lua runs io.open() turned out not to be the ue4ss
-- root (Mods/FSROverlay/config.txt: No such file or directory) despite
-- UE4SS.log claiming "working directory: .../ue4ss" at startup - that log
-- line describes where UE4SS itself found things, not the OS process CWD
-- when io.open() actually runs. Try a few plausible bases instead of
-- hardcoding one assumption.
local CONFIG_PATH_CANDIDATES = {
    "Mods/FSROverlay/config.txt",
    "ue4ss/Mods/FSROverlay/config.txt",
    "../ue4ss/Mods/FSROverlay/config.txt",
}

local function ResolveHotkey()
    local candidates = { "OEM_PLUS", "EQUALS", "PLUS", "F9" }
    for _, name in ipairs(candidates) do
        local code = Key[name]
        if type(code) == "number" then
            return code, name
        end
    end
    return nil, nil
end

local HOTKEY, HOTKEY_NAME = ResolveHotkey()

local function ReadConfiguredPreset()
    local file, usedPath = nil, nil
    for _, path in ipairs(CONFIG_PATH_CANDIDATES) do
        file = io.open(path, "r")
        if file then
            usedPath = path
            break
        end
    end

    if not file then
        print("[FSROverlay] config.txt not found under any known path (tried: " .. table.concat(CONFIG_PATH_CANDIDATES, ", ") .. "), falling back to " .. DEFAULT_PRESET .. "\n")
        return DEFAULT_PRESET
    end

    -- Editors like Windows Notepad can save the file as UTF-8 with a BOM.
    -- Those three bytes sit in front of the first line, so its "--" no
    -- longer matched as a comment and the whole comment line was taken as
    -- the (unrecognized) value.
    local rawValue = nil
    local isFirstLine = true
    for line in file:lines() do
        if isFirstLine then
            line = line:gsub("^\239\187\191", "")
            isFirstLine = false
        end
        local trimmed = line:match("^%s*(.-)%s*$")
        if trimmed ~= "" and not trimmed:match("^%-%-") and not trimmed:match("^#") then
            rawValue = trimmed
            break
        end
    end
    file:close()

    if not rawValue then
        print("[FSROverlay] " .. usedPath .. " has no value, falling back to " .. DEFAULT_PRESET .. "\n")
        return DEFAULT_PRESET
    end

    for name, _ in pairs(PRESETS) do
        if name:lower() == rawValue:lower() then
            return name
        end
    end

    print("[FSROverlay] " .. usedPath .. " value '" .. rawValue .. "' not recognized (valid: Quality, Balanced, Performance, UltraPerformance), falling back to " .. DEFAULT_PRESET .. "\n")
    return DEFAULT_PRESET
end

local function ShowNotification(text)
    local model = FindFirstOf("VHUDSubtitleViewModel")
    if not model or not model:IsValid() then
        print("[FSROverlay] VHUDSubtitleViewModel not found, try again\n")
        return
    end
    local ok_notif, err_notif = pcall(function()
        model:AddNotification({
            Text = FText(text),
            ShowSeconds = 3.0,
            Icon = nil,
            bIsQuest = false,
        })
    end)
    if not ok_notif then
        print("[FSROverlay] AddNotification failed: " .. tostring(err_notif) .. "\n")
    end
end

local UPSCALER_LEFT_OFF_TEXT = "FSR could not be switched back on after the upscaler was turned off - it may have been left off. Set Upscaling Method in Options manually."

-- True from an accepted hotkey press / fsrstatus call until its sequence
-- (up to 150 + 300 ms of delays) has finished, so a second press can't
-- interleave its own console writes with the first one's.
local sequenceInFlight = false
local sequenceStartedAt = 0
-- If UE4SS never runs one of the queued callbacks, nothing would ever clear
-- sequenceInFlight and the hotkey would stay dead until the game restarts.
-- A real sequence takes under half a second, so after this many seconds
-- (os.time() only counts whole ones) the flag is treated as stale.
local SEQUENCE_STALE_SECONDS = 5
-- True between a successful bounce-off write and a successful "on" write.
local upscalerMayBeOff = false

-- ExecuteInGameThread is what keeps engine access off UE4SS's async thread
-- (see RunInGameThread below). On a build without it, run the callbacks
-- directly, as v9 did, rather than failing on every press.
local RunOnGameThread = ExecuteInGameThread
if type(RunOnGameThread) ~= "function" then
    print("[FSROverlay] WARNING: ExecuteInGameThread is not available in this UE4SS build, engine calls will run outside the game thread\n")
    RunOnGameThread = function(callback)
        callback()
    end
end

-- RegisterKeyBind and ExecuteWithDelay callbacks run on UE4SS's own async
-- thread, not the game thread, and touching engine objects from there is a
-- race. Every step of the sequence goes through here instead. A step
-- returns true when it has scheduled the next one; any other outcome
-- (finished, bailed out early, raised) ends the sequence.
local function RunInGameThread(stepName, step)
    local ok_sched, err_sched = pcall(RunOnGameThread, function()
        local ok_step, pending = pcall(step)
        if ok_step and pending == true then
            return
        end
        sequenceInFlight = false
        if not ok_step then
            print("[FSROverlay] " .. stepName .. " failed: " .. tostring(pending) .. "\n")
            local text = "FSR status could not be read (internal error, see UE4SS.log)."
            if upscalerMayBeOff then
                text = UPSCALER_LEFT_OFF_TEXT
            end
            local ok_notif, err_notif = pcall(ShowNotification, text)
            if not ok_notif then
                print("[FSROverlay] ShowNotification failed: " .. tostring(err_notif) .. "\n")
            end
        end
    end)
    if not ok_sched then
        sequenceInFlight = false
        print("[FSROverlay] " .. stepName .. " could not be scheduled on the game thread: " .. tostring(err_sched) .. "\n")
        if upscalerMayBeOff then
            print("[FSROverlay] WARNING: the upscaler may have been left off, set Upscaling Method in Options manually\n")
        end
    end
end

local function RunConsoleCommand(KismetSystemLibrary, Engine, command)
    local ok, err = pcall(function()
        KismetSystemLibrary:ExecuteConsoleCommand(Engine, command, nil, true)
    end)
    if not ok then
        print("[FSROverlay] console command '" .. command .. "' failed: " .. tostring(err) .. "\n")
    end
    return ok
end

-- Returns the live r.ScreenPercentage, or nil plus the reason it can't be
-- trusted. 0 is not a real value here - it's what
-- GetConsoleVariableFloatValue returns for a cvar name it doesn't know
-- (gotcha #5) - and neither is anything else outside (0, 100].
local function ReadScreenPercentage(KismetSystemLibrary)
    local ok, value = pcall(function()
        return KismetSystemLibrary:GetConsoleVariableFloatValue("r.ScreenPercentage")
    end)
    if not ok then
        return nil, "read failed: " .. tostring(value)
    end
    if type(value) ~= "number" then
        return nil, "returned a non-number: " .. tostring(value)
    end
    if not (value > 0 and value <= 100) then
        return nil, "returned " .. tostring(value) .. ", outside (0, 100]"
    end
    return value, nil
end

local function EnforceFSR(KismetSystemLibrary, Engine, qualityValue, bounceMethod, onDone)
    -- When the render is stuck at native despite Altar.FSR3.Enabled == 1,
    -- just re-writing UpscalingMethod/Quality to the values they may
    -- already be set to can be a no-op - nothing changes, so nothing
    -- re-initializes the FSR3 backend. Bounce UpscalingMethod off FSR and
    -- back onto it first (mimicking what actually changing the Options
    -- dropdown would do) before setting the real values, to force a change
    -- event instead of hoping a same-value write does something.
    --
    -- The off/on bounce used to be two ExecuteConsoleCommand calls back to
    -- back in the same tick. That stopped recovering the post-alt-tab case
    -- (the DX11 swapchain/device gets torn down and recreated on alt-tab,
    -- and the FSR3 backend apparently needs re-attaching to it) - same
    -- family of bug as r.ScreenPercentage not updating within the same
    -- tick as the cvar write that changes it (gotcha #8): a same-tick
    -- "0 then immediately 2" may not register as two distinct change
    -- events for whatever engine-side callback actually re-initializes the
    -- upscaler. Give the "off" write a frame to land before writing "on".
    local function applyOn()
        local ok1 = RunConsoleCommand(KismetSystemLibrary, Engine, "Altar.UpscalingMethod 2")
        if ok1 then
            upscalerMayBeOff = false
        end
        -- A failed quality write is only logged: the method is back on FSR,
        -- and the readback reports what the render actually does.
        RunConsoleCommand(KismetSystemLibrary, Engine, "Altar.FSR3.Quality " .. tostring(qualityValue))
        if upscalerMayBeOff then
            upscalerMayBeOff = false
            ShowNotification(UPSCALER_LEFT_OFF_TEXT)
            return false
        end
        return onDone()
    end

    if bounceMethod then
        upscalerMayBeOff = RunConsoleCommand(KismetSystemLibrary, Engine, "Altar.UpscalingMethod 0")
        ExecuteWithDelay(150, function()
            RunInGameThread("bounce-on step", applyOn)
        end)
        return true
    end
    return applyOn()
end

local function ShowUpscalerStatus()
    local KismetSystemLibrary = UEHelpers.GetKismetSystemLibrary()
    local Engine = UEHelpers.GetEngine()

    if not KismetSystemLibrary or not KismetSystemLibrary:IsValid()
        or not Engine or not Engine:IsValid() then
        print("[FSROverlay] engine objects not ready yet, try again\n")
        return false
    end

    -- "FSR3.Enabled" alone resolves in the in-game console only because the
    -- console UI does prefix/fuzzy matching - the actual registered cvar
    -- name is "Altar.FSR3.Enabled" (confirmed by the user checking the
    -- console's autocomplete). GetConsoleVariableFloatValue needs the exact
    -- full name and silently returns 0 (no error) for an unmatched one,
    -- which is why the short name always read as "off".
    --
    -- IMPORTANT: Altar.FSR3.Enabled only reflects the *setting*, not the
    -- live render state - Oblivion frequently bugs into rendering native
    -- while this cvar still happily reports 1. r.ScreenPercentage is ground
    -- truth for what's actually on screen, so it - not this cvar - decides
    -- whether we attempt to force. This cvar is now informational only (it
    -- goes in the log and in the failure message if forcing doesn't help),
    -- never a gate that skips forcing.
    local ok_enabled, fsr3Enabled = pcall(function()
        return KismetSystemLibrary:GetConsoleVariableFloatValue("Altar.FSR3.Enabled")
    end)
    if not ok_enabled or type(fsr3Enabled) ~= "number" then
        -- The raw value/error belongs in the log, not in a notification.
        print("[FSROverlay] Altar.FSR3.Enabled could not be read: " .. tostring(fsr3Enabled) .. "\n")
        fsr3Enabled = "unknown"
    end
    print("[FSROverlay] Altar.FSR3.Enabled = " .. tostring(fsr3Enabled) .. "\n")

    local pct_before, err_before = ReadScreenPercentage(KismetSystemLibrary)
    if not pct_before then
        print("[FSROverlay] r.ScreenPercentage before " .. err_before .. " - not forcing anything\n")
        ShowNotification("FSR status could not be read (r.ScreenPercentage " .. err_before .. ") - nothing was changed.")
        return false
    end
    print("[FSROverlay] r.ScreenPercentage before = " .. tostring(pct_before) .. "\n")

    -- r.ScreenPercentage stuck at ~100 is the actual signal to act on, no
    -- matter what Altar.FSR3.Enabled claims - always attempt the bounce-force
    -- (Altar.UpscalingMethod 0 -> 2, then quality) in that case.
    local wasStuckAtNative = pct_before >= 99.5

    local configuredPreset = ReadConfiguredPreset()
    local qualityValue = PRESETS[configuredPreset]

    -- EnforceFSR() itself now delays between the bounce-off and bounce-on
    -- writes when bounceMethod is true, so onDone only fires once the real
    -- "on" + quality writes have actually happened - nest the readback delay
    -- inside it instead of racing it with an independent parallel timer.
    return EnforceFSR(KismetSystemLibrary, Engine, qualityValue, wasStuckAtNative, function()
    -- r.ScreenPercentage does NOT update within the same tick as the cvar
    -- write - it's the resolved output of the next rendered frame(s), not a
    -- plain stored variable. Reading it back immediately after EnforceFSR()
    -- showed "after = 100.0" every time even when Altar.FSR3.Quality's own
    -- readback confirmed the write took (e.g. "1.0 -> Quality") - a pure
    -- timing artifact, not a real failure. Give the renderer a frame or two
    -- before checking, same as the fsrsweep diagnostic did.
    ExecuteWithDelay(300, function()
    RunInGameThread("status readback", function()
        local pct_after, err_after = ReadScreenPercentage(KismetSystemLibrary)
        if pct_after then
            print("[FSROverlay] r.ScreenPercentage after = " .. tostring(pct_after) .. "\n")
        else
            print("[FSROverlay] r.ScreenPercentage after " .. err_after .. "\n")
        end

        -- Don't just trust that our own write took - read back the actual
        -- live Altar.FSR3.Quality value and name the notification after
        -- THAT, the same lesson as the Off-method bug (a console write can
        -- silently not take effect). Falls back to the configured name if
        -- the readback fails or lands on a value with no known preset name.
        local ok_readback, actualQuality = pcall(function()
            return KismetSystemLibrary:GetConsoleVariableFloatValue("Altar.FSR3.Quality")
        end)
        local presetName = configuredPreset
        if ok_readback and type(actualQuality) == "number" then
            local actualName = PRESET_NAMES_BY_VALUE[math.floor(actualQuality + 0.5)]
            if actualName then
                presetName = actualName
            else
                presetName = "quality=" .. tostring(actualQuality)
            end
        end
        print("[FSROverlay] Altar.FSR3.Quality after = " .. tostring(actualQuality) .. " -> " .. presetName .. "\n")

        -- The preset was written, but without a trustworthy
        -- r.ScreenPercentage there is no telling whether it's upscaling -
        -- say exactly that instead of guessing either way.
        if not pct_after then
            ShowNotification("FSR status could not be read after forcing " .. presetName .. " (r.ScreenPercentage " .. err_after .. ").")
            return false
        end

        -- r.ScreenPercentage, not Altar.FSR3.Enabled, decided whether we
        -- forced above - Oblivion can bug into native rendering while that
        -- cvar still says 1. That silent fallback is the whole reason this
        -- mod exists, so EnforceFSR() always re-applies the preset
        -- unconditionally, not just when pct_before looked wrong, with a
        -- bounce when it was stuck.
        --
        -- NOTE: Native AA handling was deliberately removed - detecting it
        -- reliably turned out flaky on this build. If Upscaling Method is
        -- set to Native AA, this mod's behavior around it is unsupported
        -- (documented in the README instead of half-handled in code).
        --
        -- IMPORTANT: never claim "FSR ON" unless pct_after actually shows
        -- upscaling. An earlier version fell into an "else" branch that
        -- reported "FSR ON (Quality, 100%)" whenever the force didn't work
        -- either - a self-contradictory message (ON implies <100%). All
        -- three real outcomes get their own branch now.
        local finalPct = pct_after
        local isNowUpscaling = finalPct < 99.5

        local text
        if isNowUpscaling and wasStuckAtNative then
            text = string.format("FSR silently fell back to native (Altar.FSR3.Enabled=%s but no upscaling!) - re-forced (%s, %.1f%%)", tostring(fsr3Enabled), presetName, finalPct)
        elseif isNowUpscaling then
            text = string.format("FSR ON (%s, %.1f%%)", presetName, finalPct)
        else
            text = string.format("FSR stayed at native (%.1f%%) even after forcing %s (Altar.FSR3.Enabled=%s) - try toggling Upscaling Method in Options manually.", finalPct, presetName, tostring(fsr3Enabled))
        end
        ShowNotification(text)
        return false
    end)
    end)
    return true
    end)
end

-- Shared entry point of the hotkey and the fsrstatus console command.
local function RequestUpscalerStatus()
    local now = os.time()
    if sequenceInFlight then
        if now - sequenceStartedAt <= SEQUENCE_STALE_SECONDS then
            print("[FSROverlay] previous press is still being processed, ignoring this one\n")
            return
        end
        print("[FSROverlay] previous press never finished (started " .. tostring(now - sequenceStartedAt) .. " s ago), starting over\n")
    end
    sequenceInFlight = true
    sequenceStartedAt = now
    upscalerMayBeOff = false
    RunInGameThread("status check", ShowUpscalerStatus)
end

if HOTKEY then
    RegisterKeyBind(HOTKEY, RequestUpscalerStatus)
    print("[FSROverlay] loaded - press " .. HOTKEY_NAME .. " to force the configured FSR preset and show status\n")
else
    print("[FSROverlay] WARNING: no candidate hotkey name matched this build's Key table, hotkey not bound - use the 'fsrstatus' console command instead\n")
end

local ok_cmd, err_cmd = pcall(function()
    RegisterConsoleCommandHandler("fsrstatus", function(_, _, _)
        RequestUpscalerStatus()
        return true
    end)
end)

if ok_cmd then
    print("[FSROverlay] also usable via console command: fsrstatus\n")
else
    print("[FSROverlay] WARNING: could not register 'fsrstatus' console command: " .. tostring(err_cmd) .. "\n")
end
