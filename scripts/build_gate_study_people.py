"""Publish the gate study v2 people from assets/gate_study_v2_sources.json.

Each Pixabay file is fetched into the ignored cache, checked against its recorded SHA-256, flattened and
re-encoded without metadata exactly as scripts/build_pixabay_assets.py prepares demo people, and written to
exchange/static/exchange/evidence/gate-study-v2/people/<name>.jpg. Only AI-generated images qualify.

Run from the project root:  .venv/bin/python -I scripts/build_gate_study_people.py
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "assets" / "gate_study_v2_sources.json"
PEOPLE = ROOT / "exchange" / "static" / "exchange" / "evidence" / "gate-study-v2" / "people"

spec = importlib.util.spec_from_file_location("build_pixabay_assets", ROOT / "scripts" / "build_pixabay_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def main():
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    PEOPLE.mkdir(parents=True, exist_ok=True)
    for entry in sources["models"]:
        assets.require_ai(entry)
        data, (width, height) = assets.prepare(entry)
        (PEOPLE / f"{entry['name']}.jpg").write_bytes(data)
        print(f"{entry['name']}: {width}x{height} {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    sys.exit(main())
