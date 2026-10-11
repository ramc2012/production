"""Task runner that works the same on Windows, macOS and Linux (no make).

    python scripts/dev.py test          engine + app tests
    python scripts/dev.py golden        behaviour lock only
    python scripts/dev.py perf          F2 timing: 60 wells x 30 days, validation + twin, < 60 s
    python scripts/dev.py lint          ruff check
    python scripts/dev.py run-sample    CLI validation run on the sample data
    python scripts/dev.py twin-sample   CLI twin run on the sample data
    python scripts/dev.py seed          create the database and load the sample GGS
    python scripts/dev.py serve         start the FlowTwin web app (http://127.0.0.1:8400)
    python scripts/dev.py frontend      build the React frontend into app/backend/static
    python scripts/dev.py verify        everything: lint, engine + app tests, frontend build
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SAMPLE = ["--network", "data/sample/network.yaml", "--data", "data/sample", "--config", "data/sample/config.yaml"]


def sh(*cmd: str, cwd: Path = ROOT) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(list(cmd), cwd=cwd)


def npm(*args: str) -> int:
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if not exe:
        print("npm not found: install Node.js 18+ to build the frontend")
        return 1
    return sh(exe, *args, cwd=ROOT / "app" / "frontend")


TASKS = {
    "test": lambda: sh(PY, "-m", "unittest", "discover", "-s", "tests") or sh(PY, "-m", "pytest", "-q", "tests/app"),
    "golden": lambda: sh(PY, "-m", "unittest", "tests.test_golden"),
    "perf": lambda: (os.environ.__setitem__("FLOWTWIN_PERF", "1"), sh(PY, "-m", "unittest", "-v", "tests.test_performance"))[1],
    "lint": lambda: sh(PY, "-m", "ruff", "check", "flowval", "app", "scripts", "tests"),
    "run-sample": lambda: sh(PY, "-m", "flowval", "run", *SAMPLE, "--out", "output"),
    "twin-sample": lambda: sh(PY, "-m", "flowval", "twin", *SAMPLE, "--out", "output"),
    "seed": lambda: sh(PY, "-m", "app.backend.cli", "seed-sample"),
    "serve": lambda: sh(PY, "-m", "app.backend.cli", "serve"),
    "frontend": lambda: npm("install") or npm("run", "build"),
    "verify": lambda: (TASKS["lint"]() or TASKS["test"]() or TASKS["frontend"]()),
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        print(__doc__)
        sys.exit(2)
    sys.exit(TASKS[sys.argv[1]]())
