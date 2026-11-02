"""Spend a few YouCam units through one judge's own-photo comparison on a LIVE deployment.

Run inside the web container: python scripts/smoke_live_judge.py --confirm-units 4
It enters the judge code, uploads a committed synthetic source photo as the judge's "own photo",
and previews the purchased blouse beside one eligible alternative: two Clothes V4 results at
2 units each. It then deletes the photo. It never retries a paid job and never prints the judge code.
"""
import argparse
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke_deployment import Browser, check  # noqa: E402

PLANNED_UNITS = 4
PHOTO = "exchange/static/exchange/evidence/heldout-v1/04-model-e-ORIGINAL.jpg"
TERMINAL = {"succeeded", "failed", "uncertain"}


def upload(browser, path, photo):
    boundary = uuid.uuid4().hex
    with open(photo, "rb") as handle:
        image = handle.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"adult_permission\"\r\n\r\ntrue\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"own-photo.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n").encode() + image + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    status, _, data = browser.request(path, "POST", raw=body, extra=headers)
    if status != 201:
        raise RuntimeError(f"POST {path}: expected 201, received {status}: {data[:200]!r}")
    return json.loads(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--confirm-units", type=int, required=True,
                        help=f"Must equal the planned spend, {PLANNED_UNITS} units.")
    parser.add_argument("--photo", default=PHOTO, help="A synthetic person photo to upload as the judge's own.")
    args = parser.parse_args()
    if args.confirm_units != PLANNED_UNITS:
        parser.error(f"This check spends {PLANNED_UNITS} units; pass --confirm-units {PLANNED_UNITS}.")
    if not args.url.startswith(("http://127.0.0.1", "http://localhost")):
        parser.error("Run this inside the web container against loopback.")
    judge_code = os.environ.get("JUDGE_ACCESS_CODE")
    if not judge_code:
        raise RuntimeError("Set JUDGE_ACCESS_CODE.")
    host = os.environ.get("APP_DOMAIN", "localhost")

    judge = Browser(args.url, host)
    health = judge.api("/api/health")
    check(health["provider"] == "youcam" and health["status"] == "ok", "Live YouCam provider is healthy.")
    public = Browser(args.url, host)
    public.request("/")
    sid = public.api("/api/sessions", "POST", {"order_token": "DEMO-ORDER-001"}, 201)["session_id"]
    status, _, _ = public.request(f"/api/sessions/{sid}/assets", "POST", raw=b"--x--\r\n",
                                  extra={"Content-Type": "multipart/form-data; boundary=x"})
    check(status == 503, "A visitor without the judge code cannot upload a photo.")

    judge.request("/judge")
    judge.request("/judge", "POST", {"code": judge_code}, form=True)
    sid = judge.api("/api/sessions", "POST", {"order_token": "DEMO-ORDER-001"}, 201)["session_id"]
    root = f"/api/sessions/{sid}"
    judge.api(root + "/reason", "PUT", {"reason": "style"})
    judge.api(root + "/preferences", "PUT", {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
    skus = [row["sku"] for row in judge.api(root + "/candidates")["candidates"]]
    check(len(skus) == 2, "The confirmed preferences yield two eligible alternatives.")
    photo = upload(judge, root + "/assets", args.photo)["asset_id"]
    check(bool(photo), "The judge's own photo was accepted.")

    data = judge.api(root + "/previews", "POST", {"candidate_skus": skus[:1], "consent": True, "asset_id": photo,
                                                  "include_original": True, "mode": "live"}, 202)
    check(data["provider"] == "youcam" and len(data["jobs"]) == 2, "Two live YouCam jobs were planned.")
    jobs, until = data["jobs"], time.monotonic() + 300
    while time.monotonic() < until:
        jobs = [judge.api(f'/api/previews/{job["job_id"]}') for job in jobs]
        if all(job["state"] in TERMINAL for job in jobs):
            break
        time.sleep(5)
    print("Job states: " + ", ".join(job["state"] for job in jobs), flush=True)
    check(all(job["state"] == "succeeded" for job in jobs), "Both live previews succeeded on the worker.")
    for job in jobs:
        check(bool(job.get("image_url")), "A live result has an image.")
        status, headers, body = judge.request(job["image_url"])
        check(status == 200 and headers.get("Cache-Control") == "private, no-store" and body[:2] == b"\xff\xd8",
              "A live result image is served privately.")
    judge.api(f"/api/assets/{photo}", "DELETE", expected=200)
    check(judge.request(f"/api/assets/{photo}")[0] == 404, "Deleting the judge's photo revokes access.")
    print(f"PASS: live judge own-photo comparison; {PLANNED_UNITS} units planned, no retries.", flush=True)


if __name__ == "__main__":
    main()
