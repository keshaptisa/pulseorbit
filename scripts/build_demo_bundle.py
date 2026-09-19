from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/pulse-orbit-demo-bundle.zip"

INCLUDE = [
    "app",
    "data/archive",
    "data/validation",
    "docs",
    "tests",
    "README.md",
    "Dockerfile",
    "compose.yaml",
    "pyproject.toml",
    "render.yaml",
    "scripts/build_demo_bundle.py",
    "scripts/run_experiments.py",
    "scripts/fetch_nasa_ssc_orbit.py",
    "outputs/pulse-orbit-presentation-cosmos.pptx",
]


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VERSION.txt", f"commit={commit or 'working-tree'}\n")
        for relative in INCLUDE:
            path = ROOT / relative
            if not path.exists():
                continue
            if path.is_file():
                archive.write(path, path.relative_to(ROOT))
                continue
            for item in path.rglob("*"):
                if item.is_file() and "__pycache__" not in item.parts:
                    archive.write(item, item.relative_to(ROOT))
    print(OUTPUT)


if __name__ == "__main__":
    main()
