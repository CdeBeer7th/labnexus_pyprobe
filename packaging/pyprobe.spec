# PyInstaller build for pyProbe.  Run from the repo root:
#
#     uv run --group build pyinstaller packaging/pyprobe.spec --noconfirm
#
# Produces in dist/:
#   labnexus-pyprobe[.exe]       console executable - terminal dashboard / plain logs,
#                                and opens the desktop window when run with no arguments
#   labnexus-pyprobe-gui[.exe]   windowed executable - no console window on Windows
#   pyProbe.app                  (macOS only) the windowed executable as an app bundle
#
# All three are single files except the .app, which macOS requires to be a folder.

import sys
from importlib.metadata import version as dist_version

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

HERE = SPECPATH  # noqa: F821 - injected by PyInstaller
APP_NAME = "pyProbe"
BUNDLE_ID = "com.labnexus.pyprobe"

# `importlib.metadata.version()` drives `--version` and the GUI title bar.
datas = copy_metadata("labnexus-pyprobe") + copy_metadata("labnexus-plate-parsers")

# Front ends and parsers are imported lazily inside functions; collect them all
# explicitly so a frozen build never meets an import it did not package.
hiddenimports = collect_submodules("labnexus_pyprobe") + collect_submodules(
    "labnexus_plate_parsers"
)
if sys.platform == "win32":
    hiddenimports += collect_submodules("windows_toasts")

excludes = ["pytest", "ruff"]


def analysis(script):
    return Analysis(  # noqa: F821
        [f"{HERE}/{script}"],
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=excludes,
        noarchive=False,
    )


def onefile(a, name, console):
    pyz = PYZ(a.pure)  # noqa: F821
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name=name,
        console=console,
        strip=False,
        upx=False,
    )


cli = analysis("entry_cli.py")
onefile(cli, "labnexus-pyprobe", console=True)

gui = analysis("entry_gui.py")
onefile(gui, "labnexus-pyprobe-gui", console=False)

if sys.platform == "darwin":
    # A second, folder-based build of the GUI to wrap in a proper .app bundle.
    app_exe = EXE(  # noqa: F821
        PYZ(gui.pure),  # noqa: F821
        gui.scripts,
        exclude_binaries=True,
        name=APP_NAME,
        console=False,
        strip=False,
        upx=False,
    )
    app_dir = COLLECT(  # noqa: F821
        app_exe, gui.binaries, gui.datas, name=f"{APP_NAME}-app", strip=False, upx=False
    )
    BUNDLE(  # noqa: F821
        app_dir,
        name=f"{APP_NAME}.app",
        bundle_identifier=BUNDLE_ID,
        version=dist_version("labnexus-pyprobe"),
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
