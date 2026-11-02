"""Exercise a seeded MOCK deployment over HTTP. Creates fictional sessions and one review draft.

Run inside the web container: python scripts/smoke_deployment.py
For HTTPS, pass --url https://<hostname> and set SMOKE_BASIC_PASSWORD if the proxy requires it.
The loopback mode simulates the trusted HTTPS proxy. It must never target a remote HTTP host.
"""
import argparse
import base64
from http.cookies import SimpleCookie
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Browser:
    def __init__(self, url, host):
        self.url = url.rstrip("/")
        self.host = host
        self.cookies = {}
        self.csrf = ""

    def request(self, path, method="GET", payload=None, *, form=False, extra=None, csrf=True, raw=None):
        headers = {"Host": self.host, "Referer": f"https://{self.host}/", "Origin": f"https://{self.host}"}
        if self.url.startswith("http:"):
            headers["X-Forwarded-Proto"] = "https"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        if self.csrf and csrf:
            headers["X-CSRFToken"] = self.csrf
        password = os.environ.get("SMOKE_BASIC_PASSWORD")
        if password:
            headers["Authorization"] = "Basic " + base64.b64encode(f"owner:{password}".encode()).decode()
        headers.update(extra or {})
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            data = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
        elif raw is not None:
            # A pre-encoded body, such as a multipart upload; its Content-Type comes in extra.
            data = raw
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            for value in response.headers.get_all("Set-Cookie", []):
                self.cookies.update({key: item.value for key, item in SimpleCookie(value).items()})
            if self.cookies.get("csrftoken"):
                self.csrf = self.cookies["csrftoken"]
            return response.status, response.headers, response.read()

    def api(self, path, method="GET", payload=None, expected=200, **kwargs):
        status, _, body = self.request(path, method, payload, **kwargs)
        if status != expected:
            raise RuntimeError(f"{method} {path}: expected {expected}, received {status}")
        return json.loads(body)


def check(condition, message):
    if not condition:
        raise RuntimeError(message)
    print(message, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Use an HTTP(S) origin without credentials, query or fragment.")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
        parser.error("Plain HTTP smoke checks are restricted to loopback.")
    host = os.environ.get("APP_DOMAIN", parsed.netloc)
    shopper = Browser(args.url, host)
    health = shopper.api("/api/health")
    check(health["provider"] == "mock" and health["status"] == "ok"
          and set(health["checks"].values()) == {"ok"}, "Mock provider, database, cache, queue and worker are healthy.")
    for path in ("/", "/judge", "/evidence", "/evaluation", "/evaluation/heldout", "/store"):
        status, headers, body = shopper.request(path)
        check(status == 200 and headers.get("X-Robots-Tag") == "noindex, nofollow", f"{path}: HTTP 200 and noindex.")
        if path == "/":
            match = re.search(rb'src="([^"]*app\.[a-f0-9]+\.js)"', body)
            check(match is not None, "Production HTML references hashed JavaScript.")
            status, static_headers, _ = shopper.request(match[1].decode())
            check(status == 200 and "max-age=" in static_headers.get("Cache-Control", ""), "Hashed static JavaScript is served.")
    status, _, _ = shopper.request("/api/sessions", "POST", {"order_token": "DEMO-ORDER-001"}, csrf=False)
    check(status == 403, "POST without CSRF is refused.")
    sid = shopper.api("/api/sessions", "POST", {"order_token": "DEMO-ORDER-001"}, 201)["session_id"]
    root = f"/api/sessions/{sid}"
    shopper.api(root + "/reason", "PUT", {"reason": "style"})
    controls = {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 0}
    shopper.api(root + "/preferences", "PUT", controls)
    plan, = shopper.api(root + "/tradeoffs?constraint=budget")["possibilities"]
    check(plan["before"] == 0 and plan["after"] == 200 and plan["minimum_budget"]
          and not plan["confirmed"] and [c["sku"] for c in plan["candidates"]] == ["CALM-07"],
          "A read-only budget possibility finds the current $2 minimum.")
    check(shopper.api(root + "/candidates")["status"] == "NO_MATCH", "Exploring does not change the confirmed zero budget.")
    check(shopper.api(root + "/quotes", "POST", {"selected_sku": "CALM-07"}, 422)["error"]["code"] == "INELIGIBLE",
          "An unconfirmed possibility cannot authorize a quote.")
    shopper.api(root + "/preferences", "PUT", {**controls, "max_top_up_minor": 200})
    check([c["sku"] for c in shopper.api(root + "/candidates")["candidates"]] == ["CALM-07"],
          "Separate confirmation makes the $2 alternative eligible.")
    shopper.api(root + "/preferences", "PUT", {"quieter_print": True, "sleeve_required": "long", "max_top_up_minor": 1000})
    skus = [row["sku"] for row in shopper.api(root + "/candidates")["candidates"]]
    check(len(skus) == 2, "The confirmed preferences yield two eligible alternatives.")
    person = shopper.api(root + "/demo-person", "POST", {"model": "model-a"}, 201)["asset_id"]
    status, headers, body = shopper.request(f"/api/assets/{person}")
    check(status == 200 and headers.get("Cache-Control") == "private, no-store" and body[:2] == b"\xff\xd8",
          "Private demo photo is served with no-store.")
    stranger = Browser(args.url, host)
    check(stranger.request(f"/api/assets/{person}")[0] == 404, "Another browser cannot read the private photo.")
    for mode in ("live", "recorded"):
        data = shopper.api(root + "/previews", "POST", {"candidate_skus": skus, "consent": True,
                           "asset_id": person, "include_original": True, "mode": mode}, 202)
        check(data["provider"] == ("mock" if mode == "live" else "replay"), f"{mode}: expected free provider selected.")
        jobs = data["jobs"]
        until = time.monotonic() + 60
        while time.monotonic() < until:
            jobs = [shopper.api(f'/api/previews/{job["job_id"]}') for job in jobs]
            if all(job["state"] == "succeeded" for job in jobs):
                break
            time.sleep(1)
        check(len(jobs) == 3 and all(job["state"] == "succeeded" for job in jobs), f"{mode}: three previews completed on the worker.")
        if mode == "recorded":
            check(all(job["image_url"] and job["trust"] for job in jobs), "Recorded previews include trust panels and images.")
            for job in jobs:
                status, headers, body = shopper.request(job["image_url"])
                check(status == 200 and headers.get("Cache-Control") == "private, no-store" and body[:2] == b"\xff\xd8",
                      "Worker-written result is readable by web through the private volume.")
    quote = shopper.api(root + "/quotes", "POST", {"selected_sku": skus[0]}, 201)
    payload = {"quote_id": quote["quote_id"], "quote_version": 1, "confirmed": True}
    idem = {"Idempotency-Key": "deployment-smoke-" + uuid.uuid4().hex}
    draft = shopper.api("/api/requests", "POST", payload, 201, extra=idem)
    duplicate = shopper.api("/api/requests", "POST", payload, extra=idem)
    check(duplicate["request_id"] == draft["request_id"] and duplicate.get("duplicate"), "A repeated draft submission reuses the same request.")
    judge_code = os.environ.get("JUDGE_ACCESS_CODE")
    if not judge_code:
        raise RuntimeError("Set JUDGE_ACCESS_CODE to verify the staff flow.")
    staff = Browser(args.url, host)
    staff.request("/judge")
    staff.request("/judge", "POST", {"code": judge_code}, form=True)
    check(staff.request("/staff/")[0] == 200, "The configured judge code opens the staff queue.")
    rid = draft["request_id"]
    staff.api(f"/api/staff/requests/{rid}", "PATCH", {"state": "NEEDS_CLARIFICATION", "message": "Smoke check: confirm this fictional choice."})
    shopper.api(f"/api/requests/{rid}/reply", "POST", {"reply": "Confirmed for the deployment smoke check."})
    staff.api(f"/api/staff/requests/{rid}", "PATCH", {"state": "REVIEWED", "message": "Fictional deployment check complete."})
    check(shopper.api(f"/api/requests/{rid}")["state"] == "REVIEWED", "Shopper/staff question, reply and review complete.")
    shopper.api(f"/api/assets/{person}", "DELETE", expected=200)
    check(shopper.request(f"/api/assets/{person}")[0] == 404, "Deleting the demo photo revokes access.")
    print("PASS: mock deployment smoke check; no paid provider requests.", flush=True)


if __name__ == "__main__":
    main()
