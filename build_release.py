"""Build the release archive for FSROverlay.

The zip extracts into the game root: the mod lands in
OblivionRemastered/Binaries/Win64/ue4ss/Mods/FSROverlay.

Usage: python build_release.py
"""

import re
import sys
import zipfile
from pathlib import Path

MOD_NAME = "FSROverlay"
GAME_MODS_DIR = "OblivionRemastered/Binaries/Win64/ue4ss/Mods"
MOD_FILES = ["Scripts/main.lua", "config.txt", "enabled.txt"]

ROOT = Path(__file__).resolve().parent
MOD_DIR = ROOT / "mods" / MOD_NAME
DIST_DIR = ROOT / "dist"


def read_version() -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^## (\d+\.\d+\.\d+) - ", changelog, re.MULTILINE)
    if not match:
        sys.exit("CHANGELOG.md: no '## <version> - <date>' entry found")
    return match.group(1)


def main() -> None:
    version = read_version()
    missing = [name for name in MOD_FILES if not (MOD_DIR / name).is_file()]
    if missing:
        sys.exit(f"missing mod files: {', '.join(missing)}")

    DIST_DIR.mkdir(exist_ok=True)
    archive = DIST_DIR / f"{MOD_NAME}-{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in MOD_FILES:
            arcname = f"{GAME_MODS_DIR}/{MOD_NAME}/{name}"
            zf.write(MOD_DIR / name, arcname)
            print(f"  {arcname}")
    print(f"{archive.relative_to(ROOT)}  ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
