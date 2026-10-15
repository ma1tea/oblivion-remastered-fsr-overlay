"""Tests for mods/FSROverlay/Scripts/main.lua against a fake UE4SS API.

Run: python tests/run_tests.py   (needs lupa with Lua 5.4)

Every test loads the real main.lua into a fresh Lua 5.4 state prepared by
tests/fake_ue4ss.lua. Test names carry the acceptance criterion they cover.
"""

import os
import sys
import unittest

from lupa import lua54

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FAKE_API = os.path.join(TESTS_DIR, "fake_ue4ss.lua")
MAIN_LUA = os.path.join(TESTS_DIR, "..", "mods", "FSROverlay", "Scripts", "main.lua")

BOM = "﻿"
PRESETS = {"Quality": 1, "Balanced": 2, "Performance": 3, "UltraPerformance": 4}

OFF = "Altar.UpscalingMethod 0"
ON = "Altar.UpscalingMethod 2"
QUALITY_1 = "Altar.FSR3.Quality 1"

TEXT_REFORCED = "FSR silently fell back to native (Altar.FSR3.Enabled=1.0 but no upscaling!) - re-forced (Quality, 66.7%)"
TEXT_ON = "FSR ON (Quality, 66.7%)"
TEXT_STAYED = "FSR stayed at native (100.0%) even after forcing Quality (Altar.FSR3.Enabled=1.0) - try toggling Upscaling Method in Options manually."

IGNORED = "still being processed"


class Mod:
    """A fresh Lua state with the fake API and the real main.lua loaded."""

    def __init__(self, config="Quality\n", setup=""):
        self.lua = lua54.LuaRuntime()
        self.lua.globals().dofile(FAKE_API)
        self.H = self.lua.globals().H
        self.H.config = config
        if setup:
            self.lua.execute(setup)
        self.lua.globals().dofile(MAIN_LUA)

    def do(self, code):
        return self.lua.execute(code)

    def press(self):
        self.H.pressHotkey()

    def pump(self):
        self.H.pump()

    def press_and_pump(self):
        self.press()
        self.pump()

    def _list(self, name):
        return list(self.H[name].values())

    @property
    def log(self):
        return self._list("log")

    @property
    def notifications(self):
        return self._list("notifications")

    @property
    def commands(self):
        return self._list("commands")

    @property
    def delays(self):
        return self._list("delays")

    @property
    def violations(self):
        return self._list("violations")

    def log_lines(self, needle):
        return [line for line in self.log if needle in line]


class ModTestCase(unittest.TestCase):
    def assert_unreadable(self, mod, log_needle):
        """C2/C3: one 'could not be read' notification, failure named in the log."""
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        text = mod.notifications[0]
        self.assertIn("could not be read", text)
        self.assertNotIn("FSR ON", text)
        self.assertNotIn("re-forced", text)
        self.assertTrue(mod.log_lines(log_needle), mod.log)

    def assert_next_press_works(self, mod, pct_reads="{ 100.0, 66.7 }"):
        """C5: the in-flight flag is clear, a new press runs a full sequence."""
        mod.do("H.raiseIn = {}; H.failCommands = {}; H.engineReady = true")
        mod.do("H.pctReads = %s; H.pctReadCount = 0" % pct_reads)
        before = len(mod.commands)
        ignored = len(mod.log_lines(IGNORED))
        mod.press_and_pump()
        self.assertEqual(len(mod.log_lines(IGNORED)), ignored, mod.log)
        self.assertEqual(mod.commands[before:], [OFF, ON, QUALITY_1])
        self.assertEqual(mod.notifications[-1], TEXT_REFORCED)


class C1GameThread(ModTestCase):
    def test_c1_fake_detects_off_thread_engine_access(self):
        # Guards the tests below: the fake must notice a violation.
        mod = Mod()
        mod.do('FindFirstOf("VHUDSubtitleViewModel")')
        self.assertEqual(mod.violations, ["FindFirstOf"])

    def test_c1_hotkey_stuck_at_native_runs_on_game_thread(self):
        mod = Mod()
        mod.press()
        # Nothing touches the engine from the keybind callback itself.
        self.assertEqual(mod.commands, [])
        self.assertEqual(mod.violations, [])
        mod.pump()
        self.assertEqual(mod.violations, [])
        self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_REFORCED])

    def test_c1_hotkey_already_upscaling_runs_on_game_thread(self):
        mod = Mod(setup="H.pctReads = { 66.7 }")
        mod.press_and_pump()
        self.assertEqual(mod.violations, [])
        self.assertEqual(mod.commands, [ON, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_ON])

    def test_c1_every_failure_path_stays_on_game_thread(self):
        setups = [
            "H.pctReads = { 0 }",
            "H.pctReads = { 100.0, 0 }",
            "H.pctReads = { H.raiser('boom') }",
            "H.failCommands['Altar.UpscalingMethod 2'] = 'boom'",
            "H.raiseIn.GetEngine = 'boom'",
            "H.raiseIn.ExecuteWithDelay = 'boom'",
        ]
        for setup in setups:
            with self.subTest(setup=setup):
                mod = Mod(setup=setup)
                mod.press_and_pump()
                self.assertEqual(mod.violations, [])
                self.assertEqual(len(mod.notifications), 1, mod.notifications)

    def test_c1_fsrstatus_runs_the_same_sequence(self):
        hotkey = Mod()
        hotkey.press_and_pump()
        console = Mod()
        self.assertTrue(console.H.runConsoleCommand("fsrstatus"))
        console.pump()
        self.assertEqual(console.violations, [])
        self.assertEqual(console.commands, hotkey.commands)
        self.assertEqual(console.delays, hotkey.delays)
        self.assertEqual(console.notifications, hotkey.notifications)


class C2UnreadableScreenPercentage(ModTestCase):
    def test_c2_read_fails_before_forcing(self):
        mod = Mod(setup="H.pctReads = { H.raiser('cvar read exploded') }")
        mod.press_and_pump()
        self.assert_unreadable(mod, "cvar read exploded")
        self.assertEqual(mod.commands, [])
        self.assert_next_press_works(mod)

    def test_c2_non_number_before_forcing(self):
        for value in ("'not a number'", "nil", "true", "{}"):
            with self.subTest(value=value):
                mod = Mod(setup="H.pctReads = { function() return %s end }" % value)
                mod.press_and_pump()
                self.assert_unreadable(mod, "non-number")
                self.assertEqual(mod.commands, [])
                self.assert_next_press_works(mod)

    def test_c2_read_fails_after_forcing(self):
        # The original defect: pct_after held the error string and
        # `finalPct < 99.5` raised, so no notification at all.
        for first in ("100.0", "66.7"):
            with self.subTest(first=first):
                mod = Mod(setup="H.pctReads = { %s, H.raiser('cvar read exploded') }" % first)
                mod.press_and_pump()
                self.assert_unreadable(mod, "cvar read exploded")
                self.assertFalse(mod.log_lines("status readback failed"), mod.log)
                self.assert_next_press_works(mod)

    def test_c2_non_number_after_forcing(self):
        for value in ("'not a number'", "nil"):
            with self.subTest(value=value):
                mod = Mod(setup="H.pctReads = { 100.0, function() return %s end }" % value)
                mod.press_and_pump()
                self.assert_unreadable(mod, "non-number")
                self.assert_next_press_works(mod)

    def test_c2_non_number_quality_readback_does_not_raise(self):
        mod = Mod(setup="H.cvars['Altar.FSR3.Quality'] = function() return 'junk' end")
        mod.press_and_pump()
        self.assertEqual(mod.notifications, [TEXT_REFORCED])


class C3OutOfRangeScreenPercentage(ModTestCase):
    VALUES = ("0", "0.0", "-5", "100.5", "150", "0/0", "math.huge")

    def test_c3_out_of_range_before_forcing(self):
        for value in self.VALUES:
            with self.subTest(value=value):
                mod = Mod(setup="H.pctReads = { %s }" % value)
                mod.press_and_pump()
                self.assert_unreadable(mod, "outside (0, 100]")
                self.assertEqual(mod.commands, [])

    def test_c3_out_of_range_after_forcing(self):
        for first in ("100.0", "66.7"):
            for value in self.VALUES:
                with self.subTest(first=first, value=value):
                    mod = Mod(setup="H.pctReads = { %s, %s }" % (first, value))
                    mod.press_and_pump()
                    self.assert_unreadable(mod, "outside (0, 100]")

    def test_c3_range_boundaries_are_readable(self):
        mod = Mod(setup="H.pctReads = { 100, 100 }")
        mod.press_and_pump()
        self.assertEqual(mod.notifications, [TEXT_STAYED])
        mod = Mod(setup="H.pctReads = { 0.1 }")
        mod.press_and_pump()
        self.assertEqual(mod.notifications, ["FSR ON (Quality, 0.1%)"])


class C4ConfigBom(ModTestCase):
    HEADER = "-- FSROverlay config\n--\n--   Quality\n# other comment style\n\n"

    def forced_quality(self, config):
        mod = Mod(config=config)
        mod.press_and_pump()
        self.assertEqual(len(mod.commands), 3, mod.commands)
        return mod.commands[-1], mod

    def test_c4_bom_does_not_change_the_preset(self):
        for name, value in PRESETS.items():
            for body in (name + "\n", name, self.HEADER + name + "\n",
                         self.HEADER + name.upper() + "\r\n", "  " + name.lower() + "  \n"):
                with self.subTest(preset=name, body=body):
                    plain, plain_mod = self.forced_quality(body)
                    with_bom, bom_mod = self.forced_quality(BOM + body)
                    self.assertEqual(plain, "Altar.FSR3.Quality %d" % value)
                    self.assertEqual(with_bom, plain)
                    self.assertFalse(bom_mod.log_lines("falling back"), bom_mod.log)
                    self.assertFalse(plain_mod.log_lines("falling back"), plain_mod.log)

    def test_c4_shipped_config_with_bom(self):
        shipped = os.path.join(TESTS_DIR, "..", "mods", "FSROverlay", "config.txt")
        with open(shipped, encoding="utf-8") as handle:
            content = handle.read()
        self.assertTrue(content.rstrip().endswith("Quality"))
        content = content.rstrip()[: -len("Quality")] + "Performance\n"
        command, mod = self.forced_quality(BOM + content)
        self.assertEqual(command, "Altar.FSR3.Quality 3")
        self.assertFalse(mod.log_lines("falling back"), mod.log)

    def test_c4_missing_file_falls_back_to_quality(self):
        command, mod = self.forced_quality(None)
        self.assertEqual(command, QUALITY_1)
        self.assertEqual(len(mod.log_lines("config.txt not found under any known path")), 1)
        self.assertEqual(mod._list("openedPaths"), [
            "Mods/FSROverlay/config.txt",
            "ue4ss/Mods/FSROverlay/config.txt",
            "../ue4ss/Mods/FSROverlay/config.txt",
        ])

    def test_c4_empty_file_falls_back_to_quality(self):
        for config in ("", "\n\n", "-- only comments\n# here\n", BOM, BOM + "-- only comments\n"):
            with self.subTest(config=config):
                command, mod = self.forced_quality(config)
                self.assertEqual(command, QUALITY_1)
                self.assertEqual(len(mod.log_lines("has no value, falling back to Quality")), 1)

    def test_c4_unknown_value_falls_back_to_quality(self):
        for config in ("NativeAA\n", BOM + "NativeAA\n", "Quality -- inline comment\n"):
            with self.subTest(config=config):
                command, mod = self.forced_quality(config)
                self.assertEqual(command, QUALITY_1)
                self.assertEqual(len(mod.log_lines("not recognized")), 1)
        _, mod = self.forced_quality(BOM + "NativeAA\n")
        self.assertTrue(mod.log_lines("value 'NativeAA' not recognized"), mod.log)


class C5InFlightGuard(ModTestCase):
    def test_c5_press_is_ignored_at_every_stage_of_a_sequence(self):
        mod = Mod()
        mod.press()
        expected_ignored = 0

        def second_press(via_console):
            nonlocal expected_ignored
            commands = mod.commands
            queued = len(mod.H.gameQueue), len(mod.H.timers)
            if via_console:
                mod.H.runConsoleCommand("fsrstatus")
            else:
                mod.press()
            expected_ignored += 1
            self.assertEqual(len(mod.log_lines(IGNORED)), expected_ignored, mod.log)
            self.assertEqual(mod.commands, commands)
            self.assertEqual((len(mod.H.gameQueue), len(mod.H.timers)), queued)

        second_press(False)              # queued for the game thread
        mod.H.runGameQueue()
        self.assertEqual(mod.commands, [OFF])
        second_press(False)              # inside the 150 ms bounce delay
        second_press(True)
        mod.H.fireNextTimer()
        second_press(False)              # bounce-on queued for the game thread
        mod.H.runGameQueue()
        self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])
        second_press(True)               # inside the 300 ms readback delay
        mod.H.fireNextTimer()
        second_press(False)              # readback queued for the game thread
        mod.H.runGameQueue()

        self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_REFORCED])
        self.assertEqual(mod.delays, [150, 300])
        self.assert_next_press_works(mod)

    def test_c5_flag_cleared_after_every_normal_outcome(self):
        for reads in ("{ 100.0, 66.7 }", "{ 66.7 }", "{ 100.0, 100.0 }"):
            with self.subTest(reads=reads):
                mod = Mod(setup="H.pctReads = " + reads)
                mod.press_and_pump()
                self.assert_next_press_works(mod)

    def test_c5_flag_cleared_when_engine_not_ready(self):
        mod = Mod(setup="H.engineReady = false")
        mod.press_and_pump()
        self.assertTrue(mod.log_lines("engine objects not ready yet"), mod.log)
        self.assertEqual(mod.commands, [])
        self.assert_next_press_works(mod)

    def test_c5_flag_cleared_when_a_step_raises(self):
        setups = [
            "H.raiseIn.ExecuteInGameThread = 'boom'",
            "H.raiseIn.GetKismetSystemLibrary = 'boom'",
            "H.raiseIn.GetEngine = 'boom'",
            "H.raiseIn.ExecuteWithDelay = 'boom'",
            "H.raiseIn.FindFirstOf = 'boom'",
            "H.raiseIn.AddNotification = 'boom'",
            "H.pctReads = { 66.7 }; H.raiseIn.ExecuteWithDelay = 'boom'",
            "H.pctReads = { 0 }; H.raiseIn.FindFirstOf = 'boom'",
            "FText = function() error('boom') end",
            "H.fakeOpen = io.open; io.open = function() error('boom') end",
        ]
        for setup in setups:
            with self.subTest(setup=setup):
                mod = Mod(setup=setup)
                mod.press_and_pump()  # must not raise into the caller
                self.assertTrue(mod.log_lines("boom"), mod.log)
                self.assertEqual(mod.violations, [])
                mod.do("FText = function(text) return { text = text } end")
                mod.do("io.open = H.fakeOpen or io.open")
                self.assert_next_press_works(mod)

    def test_c5_flag_cleared_when_a_later_step_cannot_be_scheduled(self):
        # ExecuteInGameThread works for the first step, then starts raising.
        for timers_before_failure in (0, 1):
            with self.subTest(timers_before_failure=timers_before_failure):
                mod = Mod()
                mod.press()
                mod.H.runGameQueue()
                for _ in range(timers_before_failure):
                    mod.H.fireNextTimer()
                    mod.H.runGameQueue()
                mod.do("H.raiseIn.ExecuteInGameThread = 'boom'")
                mod.pump()
                self.assertTrue(mod.log_lines("could not be scheduled on the game thread: "), mod.log)
                self.assertEqual(mod.violations, [])
                self.assert_next_press_works(mod)


class C6ConsoleWriteFailures(ModTestCase):
    def test_c6_every_failed_write_is_logged_with_command_and_error(self):
        for command in (OFF, ON, QUALITY_1):
            for reads in ("{ 100.0, 66.7 }", "{ 66.7 }"):
                if command == OFF and reads == "{ 66.7 }":
                    continue  # no bounce, the command is never written
                with self.subTest(command=command, reads=reads):
                    mod = Mod(setup="H.pctReads = %s; H.failCommands['%s'] = 'write exploded'" % (reads, command))
                    mod.press_and_pump()
                    lines = [line for line in mod.log if command in line and "write exploded" in line]
                    self.assertEqual(len(lines), 1, mod.log)
                    self.assertNotIn(command, mod.commands)
                    self.assert_next_press_works(mod)

    def test_c6_on_phase_failure_after_bounce_off_warns_upscaler_may_be_off(self):
        # Fix round O3 narrowed this to the method-on write; a failed
        # quality write is covered by FixRoundLeftOff below.
        mod = Mod(setup="H.failCommands['%s'] = 'write exploded'" % ON)
        mod.press_and_pump()
        self.assertEqual(mod.commands, [OFF, QUALITY_1])
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        text = mod.notifications[0]
        self.assertIn("may have been left off", text)
        self.assertNotIn("FSR ON", text)
        self.assertNotIn("re-forced", text)

    def test_c6_on_phase_never_reached_after_bounce_off_warns_too(self):
        mod = Mod(setup="H.raiseIn.ExecuteWithDelay = 'timer exploded'")
        mod.press_and_pump()
        self.assertEqual(mod.commands, [OFF])
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        self.assertIn("may have been left off", mod.notifications[0])
        self.assertTrue(mod.log_lines("timer exploded"), mod.log)

    def test_c6_on_phase_unschedulable_after_bounce_off_is_logged(self):
        # Off the game thread there is no notification to show, only the log.
        mod = Mod()
        mod.press()
        mod.H.runGameQueue()
        mod.do("H.raiseIn.ExecuteInGameThread = 'boom'")
        mod.pump()
        self.assertEqual(mod.commands, [OFF])
        self.assertTrue(mod.log_lines("upscaler may have been left off"), mod.log)
        self.assertEqual(mod.notifications, [])

    def test_c6_failed_bounce_off_still_applies_the_preset(self):
        mod = Mod(setup="H.failCommands['%s'] = 'write exploded'" % OFF)
        mod.press_and_pump()
        self.assertEqual(mod.commands, [ON, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_REFORCED])

    def test_c6_failed_write_without_bounce_still_reports_status(self):
        mod = Mod(setup="H.pctReads = { 66.7 }; H.failCommands['%s'] = 'write exploded'" % QUALITY_1)
        mod.press_and_pump()
        self.assertEqual(mod.notifications, [TEXT_ON])


class FixRoundLeftOff(ModTestCase):
    """F1, F2, O3: when the 'may have been left off' warning is shown."""

    LEFT_OFF = "may have been left off"

    def assert_never_left_off(self, mod):
        self.assertFalse([t for t in mod.notifications if self.LEFT_OFF in t], mod.notifications)
        self.assertFalse(mod.log_lines(self.LEFT_OFF), mod.log)

    def test_f1_failed_bounce_off_is_not_reported_as_left_off(self):
        mod = Mod(setup="H.failCommands['%s'] = 'off exploded'; H.failCommands['%s'] = 'on exploded'" % (OFF, ON))
        mod.press_and_pump()
        self.assertEqual(mod.commands, [QUALITY_1])
        self.assertTrue(mod.log_lines("off exploded"), mod.log)
        self.assertTrue(mod.log_lines("on exploded"), mod.log)
        self.assert_never_left_off(mod)
        # The sequence goes on to the normal status readback.
        self.assertEqual(mod.notifications, [TEXT_REFORCED])
        self.assert_next_press_works(mod)

    def test_f1_failed_bounce_off_then_unschedulable_on_phase(self):
        mod = Mod(setup="H.failCommands['%s'] = 'off exploded'" % OFF)
        mod.press()
        mod.H.runGameQueue()
        mod.do("H.raiseIn.ExecuteInGameThread = 'boom'")
        mod.pump()
        self.assertEqual(mod.commands, [])
        self.assert_never_left_off(mod)

    def test_f1_failed_bounce_off_then_step_raises(self):
        mod = Mod(setup="H.failCommands['%s'] = 'off exploded'; H.raiseIn.ExecuteWithDelay = 'boom'" % OFF)
        mod.press_and_pump()
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        self.assertIn("could not be read", mod.notifications[0])
        self.assert_never_left_off(mod)

    def test_f2_m4_flag_cleared_once_method_is_back_on(self):
        # Off and on both written; the step then raises while scheduling the
        # readback. The upscaler is on, so no left-off warning.
        mod = Mod()
        mod.press()
        mod.H.runGameQueue()
        self.assertEqual(mod.commands, [OFF])
        mod.do("H.raiseIn.ExecuteWithDelay = 'timer exploded'")
        mod.pump()
        self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])
        self.assertTrue(mod.log_lines("timer exploded"), mod.log)
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        self.assertIn("could not be read", mod.notifications[0])
        self.assert_never_left_off(mod)

    def leave_flag_set(self):
        """A sequence that dies between the off and the on write."""
        mod = Mod()
        mod.press()
        mod.H.runGameQueue()
        mod.do("H.raiseIn.ExecuteInGameThread = 'boom'")
        mod.pump()
        self.assertEqual(mod.commands, [OFF])
        self.assertEqual(len(mod.log_lines("upscaler may have been left off")), 1, mod.log)
        self.assertEqual(mod.notifications, [])
        mod.do("H.raiseIn = {}; H.pctReadCount = 0")
        return mod

    def test_f2_m5_flag_reset_at_sequence_start_step_raises(self):
        # The next sequence finds FSR upscaling (no bounce) and then raises:
        # that failure has nothing to do with the earlier bounce-off.
        mod = self.leave_flag_set()
        mod.do("H.pctReads = { 66.7 }; H.raiseIn.GetEngine = 'engine exploded'")
        mod.press_and_pump()
        self.assertEqual(mod.commands, [OFF])
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        self.assertIn("could not be read", mod.notifications[0])
        self.assertNotIn(self.LEFT_OFF, mod.notifications[0])

    def test_f2_m5_flag_reset_at_sequence_start_on_write_fails(self):
        mod = self.leave_flag_set()
        mod.do("H.pctReads = { 66.7 }; H.failCommands['%s'] = 'on exploded'" % ON)
        mod.press_and_pump()
        self.assertEqual(mod.commands, [OFF, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_ON])

    def test_f2_m5_flag_reset_at_sequence_start_unschedulable(self):
        mod = self.leave_flag_set()
        mod.do("H.pctReads = { 66.7 }; H.raiseIn.ExecuteInGameThread = 'boom'")
        mod.press_and_pump()
        self.assertEqual(len(mod.log_lines("could not be scheduled")), 2, mod.log)
        self.assertEqual(len(mod.log_lines("upscaler may have been left off")), 1, mod.log)

    def test_o3_failed_quality_write_after_bounce_continues_to_readback(self):
        mod = Mod(setup="H.failCommands['%s'] = 'quality exploded'" % QUALITY_1)
        mod.press_and_pump()
        self.assertEqual(mod.commands, [OFF, ON])
        lines = [line for line in mod.log if QUALITY_1 in line and "quality exploded" in line]
        self.assertEqual(len(lines), 1, mod.log)
        self.assertEqual(mod.delays, [150, 300])
        self.assertEqual(mod.notifications, [TEXT_REFORCED])
        self.assert_never_left_off(mod)

    def test_o3_failed_quality_write_after_bounce_stayed_native(self):
        mod = Mod(setup="H.pctReads = { 100.0, 100.0 }; H.failCommands['%s'] = 'quality exploded'" % QUALITY_1)
        mod.press_and_pump()
        self.assertEqual(mod.notifications, [TEXT_STAYED])

    def test_o3_failed_method_on_after_successful_off_still_warns(self):
        for extra in ("", "H.failCommands['%s'] = 'quality exploded'" % QUALITY_1):
            with self.subTest(extra=extra):
                mod = Mod(setup="H.failCommands['%s'] = 'on exploded'; %s" % (ON, extra))
                mod.press_and_pump()
                self.assertEqual(mod.commands[0], OFF)
                self.assertNotIn(ON, mod.commands)
                self.assertEqual(len(mod.notifications), 1, mod.notifications)
                self.assertIn(self.LEFT_OFF, mod.notifications[0])
                self.assertEqual(mod.delays, [150])
                self.assert_next_press_works(mod)


class FixRoundStaleFlag(ModTestCase):
    """O1: a sequence that never finishes must not block the hotkey forever."""

    STALE = "never finished"

    def stuck(self, drop):
        mod = Mod()
        mod.press()
        if drop == "timer":
            mod.H.runGameQueue()
            self.assertEqual(mod.commands, [OFF])
        # UE4SS never runs what was queued.
        mod.do("H.gameQueue = {}; H.timers = {}")
        return mod

    def test_o1_press_within_five_seconds_is_still_ignored(self):
        for drop in ("gameQueue", "timer"):
            for elapsed in (0, 1, 5):
                with self.subTest(drop=drop, elapsed=elapsed):
                    mod = self.stuck(drop)
                    commands = mod.commands
                    mod.H.now = mod.H.now + elapsed
                    mod.press_and_pump()
                    self.assertEqual(len(mod.log_lines(IGNORED)), 1, mod.log)
                    self.assertFalse(mod.log_lines(self.STALE), mod.log)
                    self.assertEqual(mod.commands, commands)
                    self.assertEqual(mod.notifications, [])

    def test_o1_press_after_five_seconds_resets_the_stale_flag(self):
        for drop in ("gameQueue", "timer"):
            for elapsed in (6, 3600):
                for via_console in (False, True):
                    with self.subTest(drop=drop, elapsed=elapsed, via_console=via_console):
                        mod = self.stuck(drop)
                        before = len(mod.commands)
                        mod.H.now = mod.H.now + elapsed
                        mod.do("H.pctReadCount = 0")
                        if via_console:
                            mod.H.runConsoleCommand("fsrstatus")
                        else:
                            mod.press()
                        mod.pump()
                        self.assertEqual(len(mod.log_lines(self.STALE)), 1, mod.log)
                        self.assertFalse(mod.log_lines(IGNORED), mod.log)
                        self.assertEqual(mod.commands[before:], [OFF, ON, QUALITY_1])
                        self.assertEqual(mod.notifications, [TEXT_REFORCED])
                        self.assertEqual(mod.violations, [])

    def test_o1_the_five_seconds_count_from_the_restarted_sequence(self):
        mod = self.stuck("gameQueue")
        mod.H.now = mod.H.now + 6
        mod.press()                      # accepted, restarts the clock
        mod.do("H.gameQueue = {}")       # ... and gets dropped as well
        mod.H.now = mod.H.now + 5
        mod.press_and_pump()
        self.assertEqual(len(mod.log_lines(IGNORED)), 1, mod.log)
        self.assertEqual(len(mod.log_lines(self.STALE)), 1, mod.log)
        self.assertEqual(mod.commands, [])
        mod.H.now = mod.H.now + 1
        self.assert_next_press_works(mod)
        self.assertEqual(len(mod.log_lines(self.STALE)), 2, mod.log)

    def test_o1_finished_sequence_is_never_reported_stale(self):
        mod = Mod()
        mod.press_and_pump()
        mod.H.now = mod.H.now + 3600
        self.assert_next_press_works(mod)
        self.assertFalse(mod.log_lines(self.STALE), mod.log)

    def test_o1_stale_bounce_off_does_not_leak_into_the_new_sequence(self):
        mod = self.stuck("timer")        # off written, on-phase never ran
        mod.H.now = mod.H.now + 6
        mod.do("H.pctReadCount = 0; H.pctReads = { 66.7 }; H.raiseIn.ExecuteWithDelay = 'boom'")
        mod.press_and_pump()
        self.assertEqual(len(mod.notifications), 1, mod.notifications)
        self.assertNotIn("may have been left off", mod.notifications[0])


class FixRoundNoGameThreadApi(ModTestCase):
    """O2: no ExecuteInGameThread global at load."""

    WARNING = "ExecuteInGameThread is not available"

    def test_o2_missing_execute_in_game_thread_runs_directly(self):
        for value in ("nil", "false", "'not a function'", "{}"):
            with self.subTest(value=value):
                mod = Mod(setup="ExecuteInGameThread = %s" % value)
                self.assertEqual(len(mod.log_lines(self.WARNING)), 1, mod.log)
                for _ in range(2):
                    mod.do("H.pctReadCount = 0")
                    mod.press_and_pump()
                self.assertEqual(mod.commands, [OFF, ON, QUALITY_1] * 2)
                self.assertEqual(mod.delays, [150, 300] * 2)
                self.assertEqual(mod.notifications, [TEXT_REFORCED] * 2)
                self.assertEqual(len(mod.log_lines(self.WARNING)), 1, mod.log)
                self.assertFalse(mod.log_lines("could not be scheduled"), mod.log)

    def test_o2_fsrstatus_and_guard_work_without_it(self):
        mod = Mod(setup="ExecuteInGameThread = nil")
        mod.H.runConsoleCommand("fsrstatus")
        self.assertEqual(mod.commands, [OFF])
        mod.press()                      # inside the 150 ms delay
        self.assertEqual(len(mod.log_lines(IGNORED)), 1, mod.log)
        mod.pump()
        self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])
        self.assertEqual(mod.notifications, [TEXT_REFORCED])
        self.assert_next_press_works(mod)

    def test_o2_failures_are_still_contained_without_it(self):
        mod = Mod(setup="ExecuteInGameThread = nil; H.raiseIn.GetEngine = 'boom'")
        mod.press_and_pump()
        self.assertTrue(mod.log_lines("boom"), mod.log)
        self.assert_next_press_works(mod)

    def test_o2_no_warning_when_it_exists(self):
        mod = Mod()
        self.assertFalse(mod.log_lines(self.WARNING), mod.log)


class FixRoundFsr3EnabledUnknown(ModTestCase):
    """O4: no raw Lua error text in a notification."""

    def test_o4_failed_read_shows_unknown(self):
        for reads, expected in (
            ("{ 100.0, 66.7 }", TEXT_REFORCED),
            ("{ 100.0, 100.0 }", TEXT_STAYED),
        ):
            with self.subTest(reads=reads):
                mod = Mod(setup="H.pctReads = %s; H.cvars['Altar.FSR3.Enabled'] = H.raiser('enabled exploded')" % reads)
                mod.press_and_pump()
                self.assertEqual(mod.notifications, [expected.replace("Altar.FSR3.Enabled=1.0", "Altar.FSR3.Enabled=unknown")])
                self.assertNotIn("exploded", mod.notifications[0])
                self.assertTrue(mod.log_lines("enabled exploded"), mod.log)
                self.assertEqual(mod.commands, [OFF, ON, QUALITY_1])

    def test_o4_non_number_shows_unknown(self):
        for value in ("'junk'", "nil", "true", "{}"):
            with self.subTest(value=value):
                mod = Mod(setup="H.cvars['Altar.FSR3.Enabled'] = function() return %s end" % value)
                mod.press_and_pump()
                self.assertEqual(mod.notifications, [TEXT_REFORCED.replace("=1.0", "=unknown")])
                self.assertTrue(mod.log_lines("Altar.FSR3.Enabled could not be read"), mod.log)

    def test_o4_readable_values_are_shown_as_before(self):
        for value, shown in (("1.0", "1.0"), ("0.0", "0.0"), ("0", "0")):
            with self.subTest(value=value):
                mod = Mod(setup="H.cvars['Altar.FSR3.Enabled'] = function() return %s end" % value)
                mod.press_and_pump()
                self.assertEqual(mod.notifications, [TEXT_REFORCED.replace("=1.0", "=" + shown)])
                self.assertFalse(mod.log_lines("Altar.FSR3.Enabled could not be read"), mod.log)


class FixRoundCommandLine(unittest.TestCase):
    def test_single_test_can_be_selected_by_name(self):
        import subprocess
        script = os.path.abspath(__file__)
        ok = subprocess.run([sys.executable, script, "FixRoundNoGameThreadApi.test_o2_no_warning_when_it_exists"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("Ran 1 test", ok.stderr)
        missing = subprocess.run([sys.executable, script, "FixRoundNoGameThreadApi.no_such_test"],
                                 capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)


class C7Unchanged(ModTestCase):
    def test_c7_three_notification_texts(self):
        for reads, text, commands, delays in (
            ("{ 100.0, 66.7 }", TEXT_REFORCED, [OFF, ON, QUALITY_1], [150, 300]),
            ("{ 66.7 }", TEXT_ON, [ON, QUALITY_1], [300]),
            ("{ 100.0, 100.0 }", TEXT_STAYED, [OFF, ON, QUALITY_1], [150, 300]),
        ):
            with self.subTest(reads=reads):
                mod = Mod(setup="H.pctReads = " + reads)
                mod.press_and_pump()
                self.assertEqual(mod.notifications, [text])
                self.assertEqual(mod.commands, commands)
                self.assertEqual(mod.delays, delays)

    def test_c7_preset_name_comes_from_quality_readback(self):
        mod = Mod(config="Performance\n", setup="H.cvars['Altar.FSR3.Quality'] = 2.0; H.pctReads = { 58.8 }")
        mod.press_and_pump()
        self.assertEqual(mod.commands, [ON, "Altar.FSR3.Quality 3"])
        self.assertEqual(mod.notifications, ["FSR ON (Balanced, 58.8%)"])

    def test_c7_config_is_reread_on_every_press(self):
        mod = Mod(config="Quality\n", setup="H.pctReads = { 66.7 }")
        mod.press_and_pump()
        mod.H.config = BOM + "UltraPerformance\n"
        mod.press_and_pump()
        self.assertEqual(mod.commands, [ON, QUALITY_1, ON, "Altar.FSR3.Quality 4"])

    def test_c7_hotkey_candidate_order(self):
        for keys, expected in (
            ("{ OEM_PLUS = 187, EQUALS = 1, PLUS = 2, F9 = 120 }", 187),
            ("{ EQUALS = 1, PLUS = 2, F9 = 120 }", 1),
            ("{ PLUS = 2, F9 = 120 }", 2),
            ("{ F9 = 120 }", 120),
        ):
            with self.subTest(keys=keys):
                mod = Mod(setup="Key = " + keys)
                self.assertEqual(list(mod.H.keyBinds.keys()), [expected])

    def test_c7_no_hotkey_still_registers_fsrstatus(self):
        mod = Mod(setup="Key = {}")
        self.assertEqual(list(mod.H.keyBinds.keys()), [])
        mod.H.runConsoleCommand("fsrstatus")
        mod.pump()
        self.assertEqual(mod.notifications, [TEXT_REFORCED])


if __name__ == "__main__":
    # python tests/run_tests.py [TestClass[.test_name] ...]
    unittest.main(verbosity=1)
