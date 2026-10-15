# FSROverlay

A UE4SS Lua mod for The Elder Scrolls IV: Oblivion Remastered.

Oblivion Remastered sometimes stops upscaling with AMD FSR while its settings still say FSR is on: the game quietly renders at native resolution. One reliable way to get there is alt-tabbing out of DX11 exclusive fullscreen and back. Changing the preset in Options does not bring FSR back. This mod adds a hotkey that shows whether FSR is actually upscaling and turns it back on at the preset you choose.

The mod is made for FSR. Every press switches the upscaler to FSR, so if you play with DLSS or XeSS, do not install it.

## Requirements

- The Elder Scrolls IV: Oblivion Remastered (Steam).
- [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS). Tested with v3.0.1 Beta.
- Upscaling Method set to FSR in the game's Options.

## Install

1. Extract the release archive into the game folder (the one that contains `OblivionRemastered`). The mod ends up in `OblivionRemastered\Binaries\Win64\ue4ss\Mods\FSROverlay`.
2. Start the game. The mod is enabled by the `enabled.txt` file in its folder; no entry in `mods.txt` is needed.

To check that it loaded, look for `[FSROverlay] loaded - press OEM_PLUS ...` in `ue4ss\UE4SS.log`. `OEM_PLUS` is the `=` key on the main keyboard row.

To uninstall, delete the `FSROverlay` folder.

## Usage

Press **`=`** in game, or type **`fsrstatus`** in the console (`~`). The mod applies the preset from `config.txt` and shows one of these notifications:

| Notification | What it means |
|---|---|
| `FSR ON (Quality, 66.7%)` | FSR was already upscaling; the preset was applied again. |
| `FSR silently fell back to native (Altar.FSR3.Enabled=1.0 but no upscaling!) - re-forced (Quality, 66.7%)` | The game was rendering at native resolution and the mod brought FSR back. This is the case the mod exists for. |
| `FSR stayed at native (100.0%) even after forcing Quality ...` | Forcing did not help, for example because Upscaling Method is Off in Options. Set it to FSR there. |
| `FSR status could not be read ...` | The render scale could not be read, so the mod does not claim anything. Try again in a moment. |
| `FSR could not be switched back on after the upscaler was turned off ...` | Recovery was interrupted halfway. Set Upscaling Method to FSR in Options yourself. |

The percentage is the render scale: 100% means native, about 66.7% is Quality.

The mod writes its steps to `ue4ss\UE4SS.log`, one block of `[FSROverlay]` lines per press. In the main menu the hotkey does nothing apart from a log line.

## Configuration

`config.txt` in the mod folder holds the preset the hotkey applies. Put one of these words on the first line that is not a comment:

```
Quality
Balanced
Performance
UltraPerformance
```

The file is read on every press, so changes apply without restarting the game. If the file is missing or the value is not recognized, the mod uses `Quality` and writes a warning to `UE4SS.log`.

**The preset in `config.txt` wins over the one picked in Options.** If Options say Balanced and `config.txt` says Quality, pressing `=` switches you to Quality. To play on another preset, set it in `config.txt`.

## How it works

The mod treats `r.ScreenPercentage` as the truth about what is on screen: `100` means native rendering, less than `100` means FSR is upscaling. The game's own `Altar.FSR3.Enabled` only mirrors the menu setting and keeps saying `1` while the game renders at native resolution.

On each press the mod reads `r.ScreenPercentage`. If it is at `100`, it switches `Altar.UpscalingMethod` off and, 150 ms later, back to FSR, because writing the value the game already has does not restart FSR. Then it writes `Altar.FSR3.Quality`, waits 300 ms for the next frames and reads everything back to build the notification. A press arriving while this sequence runs is ignored.

All game access runs on the game thread through UE4SS's `ExecuteInGameThread`. The changes last for the current game session only; the game does not save these console variables.

The pitfalls hit while writing the mod (wrong key and console variable names, UE4SS calls that crash the game, timing problems) are written up in [docs/TECHNICAL_NOTES.md](docs/TECHNICAL_NOTES.md).

## What was tested

Tested in the game with UE4SS v3.0.1 Beta on 2026-10-15, about five minutes of play:

- the hotkey and the `fsrstatus` command;
- recovery after alt-tabbing out of DX11 exclusive fullscreen: the render scale went from 100% back to 66.7% on the first press;
- a double press: the second press was ignored and only one notification appeared.

There were no crashes and no mod errors in the log.

Covered only by the automated tests below, not checked in game with this version: changing `config.txt` while the game runs, a `config.txt` saved with a UTF-8 BOM, pressing the hotkey in the main menu.

Not tested: other UE4SS versions, the Game Pass version of the game.

## Known limitations

- The hotkey always applies the preset from `config.txt`, whatever is selected in Options.
- The upscaler is always switched to FSR; DLSS and XeSS users should not use this mod.
- Native AA is not supported. If Options are set to Native AA, set Upscaling Method to FSR first.
- The key is fixed to `=`. If a future UE4SS or game update renames that key, the mod falls back to `F9`; the load line in `UE4SS.log` names the key actually bound.

## Tests

```
pip install lupa
python tests/run_tests.py
```

The tests load the real `Scripts/main.lua` against a fake UE4SS API (`tests/fake_ue4ss.lua`). They check the logic only and cannot tell whether the game behaves like the fake.

## Building the archive

```
python build_release.py
```

writes `dist/FSROverlay-<version>.zip`, laid out to be extracted into the game folder.

## License

[MIT](LICENSE). The mod contains no game assets and no third-party code. It is not affiliated with Bethesda Softworks, AMD or the UE4SS project.
