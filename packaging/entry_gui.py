"""PyInstaller entry point for the windowed executable (``labnexus-pyprobe-gui``)."""

import sys

from labnexus_pyprobe.cli import main_gui

if __name__ == "__main__":
    sys.exit(main_gui())
