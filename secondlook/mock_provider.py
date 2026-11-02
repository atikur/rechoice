"""Deterministic preview stand-in; never creates or transmits an image."""
from .providers import FAILED, SUCCEEDED, PollResult, PreviewInput, ProviderRejected, StartResult

MOCK_LABEL = "Mock only. No generated preview or YouCam call."


class MockPreviewProvider:
    name = "mock"
    version = "mock-v1"
    units_per_job = 0

    def start(self, job: PreviewInput) -> StartResult:
        if not job.consent:
            raise ProviderRejected("PHOTO_CONSENT_REQUIRED")
        return StartResult(f"mock-{job.job_id}", 0)

    def poll(self, provider_task_id: str) -> PollResult:
        if not provider_task_id.startswith("mock-"):
            return PollResult(FAILED, error_code="UNKNOWN_TASK")
        return PollResult(SUCCEEDED)
