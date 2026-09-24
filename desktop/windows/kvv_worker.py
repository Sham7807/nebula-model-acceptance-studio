"""PyInstaller worker used to execute one isolated KVV pytest process."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    integrations = root / "integrations"
    verifier = integrations / "Kimi-Vendor-Verifier"
    sys.path.insert(0, str(integrations))
    os.chdir(verifier)
    import pytest

    return int(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
