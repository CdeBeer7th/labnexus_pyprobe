"""PyInstaller entry point for the console executable (``labnexus-pyprobe``)."""

import sys

from labnexus_pyprobe.cli import main

if __name__ == "__main__":
    sys.exit(main())
