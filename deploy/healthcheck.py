"""Probe the whole queue/worker/database path without deployment secrets in output."""
import json
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as response:
        data = json.load(response)
    print(json.dumps(data))
    sys.exit(0 if data.get("status") == "ok" else 1)
except (OSError, ValueError, urllib.error.URLError):
    print("Rechoice health probe failed.")
    sys.exit(1)
