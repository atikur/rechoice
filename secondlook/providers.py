"""Provider-neutral preview contract. Standard library only.

Django wiring, locking and unit accounting live in exchange.preview_jobs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PENDING, RUNNING, SUCCEEDED, FAILED, UNCERTAIN = (
    "pending", "running", "succeeded", "failed", "uncertain")
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, UNCERTAIN})


class ProviderRejected(Exception):
    """The provider definitely did not accept the work, so nothing was charged."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProviderOutcomeUnknown(Exception):
    """A request may or may not have created paid work. Never retry it automatically."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PreviewInput:
    job_id: str
    sku: str
    variant_version: int
    garment_asset_id: str
    person_image: bytes | None
    consent: bool
    consent_version: int
    garment_image: bytes | None = None
    # Feature settings for products without a photo, such as a beauty shade: {"category", "color", "finish"}.
    params: dict | None = None


@dataclass(frozen=True)
class StartResult:
    provider_task_id: str
    reserved_units: int = 0


@dataclass(frozen=True)
class PollResult:
    state: str
    observed_units: int = 0
    error_code: str = ""
    image: bytes | None = None


class PreviewProvider(Protocol):
    name: str
    version: str
    units_per_job: int

    def start(self, job: PreviewInput) -> StartResult: ...

    def poll(self, provider_task_id: str) -> PollResult: ...
