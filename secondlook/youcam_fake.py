"""Scripted stand-in for the YouCam HTTP API. Used by tests; it never opens a network connection.

It answers the file, upload, task, poll and result-download calls the adapter makes, records
every call, and fails loudly on anything else.
"""
from __future__ import annotations

import json

from .youcam_provider import CREDIT_PATH, FILE_PATH, HISTORY_PATH, TASK_PATH, HttpResponse, TransportError

API = "https://yce.invalid"
TASKS = "/s2s/v2.0/task/"
STORAGE = "https://storage.invalid"


def reply(status: int, payload: dict | None = None) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload or {}).encode())


def task_status(state: str, *, error: str | None = None, url: str | None = None) -> HttpResponse:
    data = {"task_status": state, "error": error}
    if url:
        data["results"] = {"url": url}
    return reply(200, {"status": 200, "data": data})


class FakeYouCamTransport:
    def __init__(self, *, result_image: bytes = b"\x89PNG fake", task: HttpResponse | Exception | None = None,
                 polls: list | None = None, file_api: HttpResponse | None = None, balance: float = 1000.0,
                 balance_reply: HttpResponse | Exception | None = None, extra_history: list | None = None,
                 units_per_task: float = 2.0):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.uploads: dict[str, bytes] = {}
        self._files = 0
        self._tasks = 0
        self._result = result_image
        self._task = task
        self._polls = list(polls or [task_status("success", url=f"{STORAGE}/result/task-1.jpg")])
        self._file_api = file_api
        self.balance = balance
        self._balance_reply = balance_reply
        self._units = units_per_task
        self.history = list(extra_history or [])
        # (task path, payload) for every task created, so tests can check what each feature sent.
        self.tasks: list[tuple[str, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if url == API + FILE_PATH and method == "POST":
            if self._file_api is not None:
                return self._file_api
            self._files += 1
            spec = json.loads(body)["files"][0]
            file_id = f"file-{self._files}"
            return reply(200, {"status": 200, "data": {"files": [{
                "file_id": file_id, "file_name": spec["file_name"], "content_type": spec["content_type"],
                "requests": [{"method": "PUT", "url": f"{STORAGE}/upload/{file_id}",
                              "headers": {"Content-Type": spec["content_type"],
                                          "Content-Length": str(spec["file_size"])}}]}]}})
        if url.startswith(f"{STORAGE}/upload/") and method == "PUT":
            self.uploads[url.rsplit("/", 1)[1]] = body
            return HttpResponse(200)
        # Tasks are created with POST to a feature path, which may have two parts (2d-vto/earring), and
        # polled with GET on that path plus the task ID.
        if url.startswith(API + TASKS) and method == "POST":
            if isinstance(self._task, Exception):
                raise self._task
            if self._task is None:
                self._tasks += 1
                task_id = f"task-{self._tasks}"
                feature = url[len(API + TASKS):].replace("2d-vto/", "")
                self.tasks.append((url[len(API):], json.loads(body)))
                self.balance -= self._units
                self.history.append({"id": len(self.history) + 1, "action": "api_" + feature.replace("-", "_"),
                                     "target_id": f"{task_id}:{self._tasks:012d}",
                                     "info": {"credits": [{"amount_dec": -self._units}]}})
                return reply(200, {"status": 200, "data": {"task_id": task_id}})
            return self._task
        if url.startswith(API + TASKS) and method == "GET":
            next_reply = self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]
            if isinstance(next_reply, Exception):
                raise next_reply
            return next_reply
        if url == API + CREDIT_PATH and method == "GET":
            if isinstance(self._balance_reply, Exception):
                raise self._balance_reply
            if self._balance_reply is not None:
                return self._balance_reply
            return reply(200, {"status": 200, "results": [{"type": "ApiPaygToken", "amount_dec": self.balance}]})
        if url.startswith(API + HISTORY_PATH) and method == "GET":
            return reply(200, {"status": 200, "result": {"history": list(reversed(self.history)), "next_token": None}})
        if url.startswith(f"{STORAGE}/result/") and method == "GET":
            # A callable gives each download its own image, so tests can draw a different result per task.
            return HttpResponse(200, self._result() if callable(self._result) else self._result)
        raise AssertionError(f"Unexpected call: {method} {url}")

    def api_calls(self, method: str, path: str) -> list:
        return [c for c in self.calls if c[0] == method and c[1] == API + path]


__all__ = ["API", "STORAGE", "FakeYouCamTransport", "TransportError", "reply", "task_status"]
