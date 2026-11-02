"""Verify the recorded-results walkthrough over real HTTPS, using a private local access file.

Creates a fictional review draft, recorded preview jobs and private verification screenshots.
Refuses a non-mock YouCam backend. Paid judge AI requires --allow-paid-ai and an existing allowance.
Never prints credentials or sends them to a different origin.
"""
import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def read_access(path, requested_url=None):
    if path.stat().st_mode & 0o077:
        raise ValueError("The local access file must have mode 600.")
    fields = dict(line.split(": ", 1) for line in path.read_text().splitlines() if ": " in line)
    labels = ("URL", "HTTP username", "HTTP password", "Judge code")
    if any(not fields.get(label) for label in labels):
        raise ValueError("The private access file is missing required fields.")
    url = fields["URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Use the HTTPS origin recorded in the private access file.")
    if requested_url and requested_url.rstrip("/") != url:
        raise ValueError("Refusing to send access credentials to a different origin.")
    return fields, url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("The authenticated health check must not redirect.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-file", type=Path, required=True)
    parser.add_argument("--url")
    parser.add_argument("--allow-paid-ai", action="store_true", help="Use only within the owner's existing judge-AI allowance.")
    args = parser.parse_args()
    fields, url = read_access(args.access_file, args.url)
    token = base64.b64encode((fields["HTTP username"] + ":" + fields["HTTP password"]).encode()).decode()
    request = urllib.request.Request(url + "/api/health", headers={"Authorization": "Basic " + token})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        health = json.load(response)
    if health.get("provider") != "mock" or health.get("status") != "ok":
        raise ValueError("Refusing the walkthrough: a healthy mock backend is required.")
    paid_ai = health.get("exchange_ai", "off") != "off"
    if paid_ai and not args.allow_paid_ai:
        raise ValueError("Judge AI is enabled. This walkthrough needs --allow-paid-ai within an existing approved allowance.")

    sys.path.insert(0, str(ROOT))
    from scripts import record_submission_demo as demo
    from playwright.sync_api import Browser, expect

    # The local video uses an inline worker. HTTPS checks include network round trips and queued jobs.
    expect.set_options(timeout=30_000)

    work = Path(tempfile.mkdtemp(prefix="hosted-browser-", dir=ROOT / ".local/deployment"))
    original_context = Browser.new_context

    def authenticated_context(browser, *args, **kwargs):
        kwargs["http_credentials"] = {"origin": url, "username": fields["HTTP username"],
                                      "password": fields["HTTP password"]}
        return original_context(browser, *args, **kwargs)

    @contextmanager
    def hosted_app(_work):
        yield url, fields["Judge code"]

    # The same UI path as the video, scoped to its newly created request even on a populated host.
    demo.local_app = hosted_app
    demo.PACKAGE = work
    Browser.new_context = authenticated_context
    try:
        # Large recorded images travel over real HTTPS. Still require complete, decoded images;
        # keep the separate functional assertions at 30 seconds and record each image wait.
        record = demo.capture(work, rehearse=True, navigation_wait="domcontentloaded", image_timeout_ms=60_000)
    finally:
        Browser.new_context = original_context
    result = {"url": url, "tls_verification": "enabled", "provider": "mock", "paid_provider_calls": None if paid_ai else 0,
              "paid_youcam_calls": 0, "llm_calls_enabled_under_existing_cap": paid_ai,
              "completed": True, **record}
    (work / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"url": url, "completed": True, "browser_errors": record["browser_errors"],
                      "verification_file": str(work / "verification.json"), "paid_youcam_calls": 0,
                      "llm_calls_enabled_under_existing_cap": paid_ai}, indent=2))


if __name__ == "__main__":
    main()
