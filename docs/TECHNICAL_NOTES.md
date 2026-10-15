# FSROverlay technical notes

Notes for modders and for anyone changing `Scripts/main.lua`. For installing
and using the mod, see the [README](../README.md).

## Ground truth

`r.ScreenPercentage` is the engine's per-frame render/output resolution ratio:

- `100` -> upscaler off, rendering native.
- `< 100` (e.g. `~66.7` at Quality) -> FSR is actively upscaling.

Read live via `UKismetSystemLibrary:GetConsoleVariableFloatValue("r.ScreenPercentage")`.

The console variables that control the upscaler on this game:

| CVar                     | Value | Meaning                   |
|--------------------------|-------|----------------------------|
| `Altar.UpscalingMethod`  | `2`   | FSR                        |
| `Altar.FSR3.Enabled`     | `0`/`1` | Whether FSR3 is enabled at all |
| `Altar.FSR3.Quality`     | `0`   | Native AA (no upscaling)  |
| `Altar.FSR3.Quality`     | `1`   | Quality                   |
| `Altar.FSR3.Quality`     | `2`   | Balanced                  |
| `Altar.FSR3.Quality`     | `3`   | Performance                |
| `Altar.FSR3.Quality`     | `4`   | UltraPerformance            |

The `Altar.FSR3.Quality` mapping is the standard AMD FSR3 quality-mode enum,
confirmed live in-game via the console (`~`) on this install, including via
its autocomplete for the two `FSR3.*` names (see gotcha #5 below for why the
autocomplete-shown short name isn't the one to use from Lua). None of these
cvars appear in `GameUserSettings.ini` or `Engine.ini`, so there is no known
console command to force a disk save of them - setting them via
`ExecuteConsoleCommand` only affects the current running session, the same
as typing them into the console by hand.

**Native AA is not supported by this mod.** Detecting/handling it correctly
turned out unreliable on this build, so it was deliberately left alone
rather than half-implemented. If `Altar.UpscalingMethod`/`Altar.FSR3.Quality`
are set to Native AA when you press the hotkey, behavior here is undefined -
set Upscaling Method to FSR yourself in Options first.

## Debugging history / gotchas hit on this UE4SS build (v3.0.1 Beta, git 437a8ff)

These are recorded because every one of them silently fails in a different
way (crash, no-op, wrong key, wrong cvar name) and cost a full
game-restart cycle each to diagnose:

1. **`Key.EQUALS` doesn't exist in this build's `Key` table.** Caused
   `RegisterKeyBind` to fail with *"No overload found... integer key"*
   because the first argument was `nil`. The correct name for the top-row
   `=`/`+` key here is **`Key.OEM_PLUS`** (the real `VK_OEM_PLUS` code,
   187) - confirmed by dumping the entire `Key` table to the log once.
   `Key.ADD` (107) is Numpad+, a different key entirely.
2. **`PrintString`/`ExecuteConsoleCommand` require every parameter, with no
   Blueprint-style defaults filled in.** `PrintString` has 7 params in this
   engine version (`WorldContextObject, InString, bPrintToScreen,
   bPrintToLog, TextColor, Duration, Key`); `ExecuteConsoleCommand` needs 4
   (`WorldContextObject, Command, SpecificPlayer, bWriteToLog`). Passing
   fewer throws a catchable Lua error ("UFunction expected N parameters,
   received M").
3. **Passing a bare Lua string for an `FName` parameter is a hard engine
   crash, not a catchable Lua error.** `PrintString`'s last param is an
   `FName`, not a `string`. A raw string there corrupted memory badly enough
   to freeze/crash the whole game with no log output at all. Fix: build a
   real `FName` via the global constructor, e.g.
   `FName("None", EFindName.FNAME_Find)` (same pattern used in
   `Mods/shared/UEHelpers/UEHelpers.lua`).
4. **`PrintString`/`AddOnScreenDebugMessage` and `DrawDebugString` both
   execute with zero error and draw nothing on screen** in this Shipping
   build, while the built-in `stat fps` overlay (a different draw path)
   renders fine - so it's not a general "debug canvas is hidden" issue.
   There is no `EnableAllScreenMessages` console command in this build
   either (confirmed by typing it directly into the in-game console: not
   found), so `GAreScreenMessagesEnabled` can't be flipped back on that way.
   **Working fix:** use the game's own native notification system instead,
   the same one other installed mods use for "mod loaded"-style messages:
   ```lua
   local model = FindFirstOf("VHUDSubtitleViewModel")
   model:AddNotification({
       Text = FText("some text"),
       ShowSeconds = 3.0,
       Icon = nil,
       bIsQuest = false,
   })
   ```
   (pattern found in the already-working `MagicSkillLevelingFixed` mod's
   `scripts/helpers.lua`).
5. **A cvar name that resolves in the in-game console isn't necessarily the
   real registered name.** Typing `FSR3.Enabled` into the console works and
   prints a sensible `0`/`1` because the console UI does prefix/fuzzy
   matching - but `GetConsoleVariableFloatValue("FSR3.Enabled")` from Lua
   always silently returned `0` (no error) because that's not the actual
   registered name. The real name, found via the console's own autocomplete,
   is **`Altar.FSR3.Enabled`**. Same story for quality: the initially-assumed
   `Altar.FSR.quality` also silently read back as `0` always - the real name
   is **`Altar.FSR3.Quality`**. `GetConsoleVariableFloatValue` returning `0`
   with `ok == true` (no pcall error) is this mod's signature symptom of a
   wrong cvar name - not "cvar is actually 0".
6. **A console write can silently not take effect**, so don't trust that it
   did: forcing `Altar.UpscalingMethod 2` to the value it's already at (e.g.
   while stuck at native) can be a no-op that doesn't re-initialize the FSR3
   backend, so `r.ScreenPercentage` stays at 100 no matter what
   `Altar.FSR3.Quality` gets set to. Fix: when the render was stuck at
   native, bounce `Altar.UpscalingMethod` off FSR and back on (`0` then `2`)
   before setting quality, to force an actual change event instead of a
   same-value write. Also, read `Altar.FSR3.Quality` back after writing it
   to name the preset in the notification off the real value rather than the
   value it tried to set.
7. **The process's Lua `io.open()` working directory is not what
   `UE4SS.log`'s startup "working directory: .../ue4ss" line implies.**
   `io.open("Mods/FSROverlay/config.txt", "r")` failed with "No such file or
   directory" despite that log line. Fixed by trying a short list of
   plausible relative bases (`Mods/...`, `ue4ss/Mods/...`,
   `../ue4ss/Mods/...`) instead of hardcoding one assumption.
8. **`r.ScreenPercentage` doesn't update within the same tick as the cvar
   write that changes it** - it's the resolved output of the next rendered
   frame(s), not a plain stored variable. Reading it back immediately after
   forcing showed `after = 100.0` every single time, even when
   `Altar.FSR3.Quality`'s own readback confirmed the write took (e.g. `1.0 ->
   Quality`) - a pure timing artifact that looked exactly like the force
   failing. Fix: wrap the post-force read in `ExecuteWithDelay(300, ...)` to
   give the renderer a frame or two first.
9. **`Altar.FSR3.Enabled` reflects the settings-menu state, not the live
   render state.** Gating forcing on it (see gotcha #6's earlier fix)
   backfired: Oblivion can bug into rendering native while this cvar still
   reports `1`, and gating meant the mod would see `r.ScreenPercentage`
   stuck at 100 and do nothing about it. `r.ScreenPercentage` is ground
   truth for what's on screen, so it alone now decides whether to force -
   `Altar.FSR3.Enabled` is logged and shown in messages, never used to skip
   forcing.
10. **The `Altar.UpscalingMethod` bounce (gotcha #6) stopped recovering the
    post-alt-tab case specifically**, even though it kept working for the
    plain "silently fell back to native" case. Alt-tabbing out of and back
    into DX11 exclusive fullscreen tears down and recreates the swapchain/
    device, and the bounce's two `ExecuteConsoleCommand` calls (`0` then
    `2`) were back to back in the same tick - same family of problem as
    gotcha #8 (`r.ScreenPercentage` not updating same-tick). Whatever
    engine-side callback actually re-initializes the FSR3 backend on an
    `Altar.UpscalingMethod` change apparently needs the two writes to land
    in separate ticks to register as two real transitions. Fix: insert
    `ExecuteWithDelay(150, ...)` between the bounce-off and bounce-on/
    quality writes.
11. **`RegisterKeyBind` and `ExecuteWithDelay` callbacks don't run on the
    game thread.** Calling `ExecuteConsoleCommand`,
    `GetConsoleVariableFloatValue`, `FindFirstOf` or `AddNotification`
    straight from them is a race with the engine, even if it happens to
    work most of the time. Since v10 every step of the sequence is wrapped
    in `ExecuteInGameThread(...)`; the delays themselves are unchanged.
    If a UE4SS build has no `ExecuteInGameThread`, the mod logs one warning
    at load and calls the engine directly, as v9 did.
    This one comes from a code audit. Checked in game on 2026-10-15 with
    this UE4SS build: `ExecuteInGameThread` exists (no warning at load), and
    the hotkey, `fsrstatus` and the post-alt-tab recovery all work through
    it with the unchanged delays.
