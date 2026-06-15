from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

COMMANDS = [
    [sys.executable, "seed.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_app.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_database.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_views.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_permissions.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_workflow.py"],
    [sys.executable, "-m", "pytest", "tests/test_smoke_ai_assistant.py"],
]


def main():
    failures: list[str] = []
    for command in COMMANDS:
        print(f"\n>>> Running: {' '.join(command)}")
        result = subprocess.run(command, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            failures.append(" ".join(command))

    if failures:
        print("\n以下烟测失败：")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print("\n所有烟测通过")


if __name__ == "__main__":
    main()
