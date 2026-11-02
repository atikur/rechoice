"""Read-only YouCam unit costs for every feature the expansion plan uses. Costs no units.

Uses only GET requests to the balance and feature-cost endpoints, never a task endpoint, and never
prints credentials or raw responses. --save PATH keeps the full cost list privately (mode 600).

Run: .venv/bin/python scripts/check_feature_costs.py [--save private_media/reviews/feature-costs.json]
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_youcam_budget import ROOT, get, key_from_env  # noqa: E402

PLANNED = {
    "Clothes V4": "/s2s/v2.0/task/cloth-v4",
    "Shoes": "/s2s/v2.0/task/shoes-transfer",
    "Bag": "/s2s/v2.0/task/bag",
    "Hat": "/s2s/v2.0/task/hat",
    "Scarf": "/s2s/v2.0/task/scarf",
    "Earrings": "/s2s/v2.0/task/2d-vto/earring",
    "Necklace": "/s2s/v2.0/task/2d-vto/necklace",
    "Watch": "/s2s/v2.0/task/2d-vto/watch",
    "Ring": "/s2s/v2.0/task/2d-vto/ring",
    "Bracelet": "/s2s/v2.0/task/2d-vto/bracelet",
    "Makeup VTO": "/s2s/v2.0/task/makeup-vto",
    "Nail VTO": "/s2s/v2.0/task/nail-vto",
    "Hair colour": "/s2s/v2.0/task/hair-color",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", metavar="PATH", help="Also save the full feature-cost list to PATH.")
    args = parser.parse_args(argv)
    key = key_from_env()
    info = get("/s2s/v1.0/client/credit", key)
    if info.get("status") != 200 or not isinstance(info.get("results"), list):
        raise RuntimeError("Balance response shape was unexpected.")
    total = sum(float(r["amount_dec"]) for r in info["results"] if r.get("type") in ("ApiSubsToken", "ApiPaygToken"))
    print(f"Total visible units: {total:.2f}")
    rows, token = [], None
    for _ in range(40):
        params = {"page_size": 20}
        if token:
            params["starting_token"] = token
        data = get("/s2s/v2.0/credit/feature-cost", key, params)
        result = data.get("result")
        if data.get("status") != 200 or not isinstance(result, dict):
            raise RuntimeError("Feature-cost response shape was unexpected.")
        rows += [{k: row.get(k) for k in ("description", "amount", "proc_unit", "unit", "run_task_url")}
                 for row in result.get("skus", [])]
        token = result.get("next_token")
        if not token:
            break
    else:
        raise RuntimeError("Feature-cost pagination exceeded a safe bound.")
    print(f"Feature-cost entries listed: {len(rows)}")
    for name, path in PLANNED.items():
        found = [r for r in rows if (r.get("run_task_url") or "").startswith(ROOT + path)]
        if not found:
            print(f"{name}: no cost entry listed for {path}")
        for r in found:
            print(f"{name}: {r['amount']} {r.get('unit') or ''} per {r.get('proc_unit') or 'task'} "
                  f"({r.get('description')}) at {r['run_task_url'].removeprefix(ROOT)}")
    if args.save:
        target = Path(args.save)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        print(f"Saved {len(rows)} entries to {target}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print("Check incomplete:", exc)
        raise SystemExit(1)
