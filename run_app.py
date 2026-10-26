"""Launcher for the Watermark Remover app (also the PyInstaller entry point)."""

import multiprocessing
import sys

from app.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
