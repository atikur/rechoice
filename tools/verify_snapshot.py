#!/usr/bin/env python3
"""Check every recorded snapshot payload hash; no network or application execution."""
import hashlib
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'SOURCE-MANIFEST.json').read_text())
for row in manifest['files']:
    path = root / row['file']
    assert path.is_file() and not path.is_symlink(), row['file']
    content = path.read_bytes()
    assert len(content) == row['bytes'], row['file']
    assert hashlib.sha256(content).hexdigest() == row['sha256'], row['file']
print(f"Verified {len(manifest['files'])} source snapshot payloads.")
