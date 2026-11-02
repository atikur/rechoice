"""Read-only YouCam balance and V4 cost check; never prints credentials or raw responses.

--save-history PATH also saves recent unit history pages (read-only) for
`manage.py run_feasibility --reconcile PATH`. The file stays private (mode 600).
"""
import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = "https://yce-api-01.makeupar.com"
KEY_FILE = Path(__file__).resolve().parents[1] / ".env"
TARGET = ROOT + "/s2s/v2.0/task/cloth-v4"


def key_from_env():
    if not KEY_FILE.is_file():
        raise RuntimeError("Saved .env is missing.")
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("YOUCAM_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise RuntimeError("YOUCAM_API_KEY is not configured.")


def get(path, key, params=None):
    url = ROOT + path + ("?" + urlencode(params) if params else "")
    request = Request(url, headers={"Authorization": "Bearer " + key,
                                    "Accept": "application/json",
                                    "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status != 200:
                raise RuntimeError(f"Unexpected HTTP {response.status}")
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Read-only API request returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Read-only API request could not connect.") from None


def save_history(key, path, max_pages=10):
    pages, token = [], None
    for _ in range(max_pages):
        params = {"page_size": 30}
        if token:
            params["starting_token"] = token
        data = get("/s2s/v1.0/client/credit/history", key, params)
        result = data.get("result")
        if data.get("status") != 200 or not isinstance(result, dict):
            raise RuntimeError("History response shape was unexpected.")
        pages.append({"history": result.get("history", [])})
        token = result.get("next_token")
        if not token:
            break
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pages, indent=1) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    print("History entries saved:", sum(len(page["history"]) for page in pages), "in", len(pages), "pages")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save-history", metavar="PATH", help="Also save recent unit history to PATH.")
    args = parser.parse_args(argv)
    key = key_from_env()
    info = get("/s2s/v1.0/client/credit", key)
    if info.get("status") != 200 or not isinstance(info.get("results"), list):
        raise RuntimeError("Balance response shape was unexpected.")
    balances = []
    for row in info["results"]:
        if row.get("type") in ("ApiSubsToken", "ApiPaygToken"):
            balances.append((row["type"], float(row["amount_dec"])))
    print("Balance categories:", ", ".join(f"{kind}={amount:.2f}" for kind, amount in balances))
    print("Total visible units:", f"{sum(amount for _, amount in balances):.2f}")

    token = None
    matches = []
    for _ in range(20):
        params = {"page_size": 20}
        if token:
            params["starting_token"] = token
        data = get("/s2s/v2.0/credit/feature-cost", key, params)
        result = data.get("result")
        if data.get("status") != 200 or not isinstance(result, dict):
            raise RuntimeError("Feature-cost response shape was unexpected.")
        for row in result.get("skus", []):
            if row.get("run_task_url") == TARGET:
                matches.append({k: row.get(k) for k in ("description", "amount", "proc_unit", "unit")})
        token = result.get("next_token")
        if not token:
            break
    else:
        raise RuntimeError("Feature-cost pagination exceeded a safe bound.")
    print("Clothes V4 cost entries:", json.dumps(matches, ensure_ascii=False))
    if not matches:
        raise RuntimeError("No exact V4 endpoint cost entry was found; paid testing stays paused.")
    if args.save_history:
        save_history(key, args.save_history)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print("Verification incomplete:", exc)
        raise SystemExit(1)
