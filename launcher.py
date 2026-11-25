"""PyInstaller entry point for the portable Windows build."""

from __future__ import annotations

from meetsub.main import main

if __name__ == "__main__":
    raise SystemExit(main())
