"""YouCam adapters for the preview contract: AI Clothes V4, Makeup VTO, Nail VTO and Earrings VTO.

Standard library only.

Flow, from docs.perfectcorp.com/reference/ai_clothes/v4.0 (read 19 October 2026):

1. POST /s2s/v2.0/file with the file's name, type and size. The reply holds a file_id and a
   signed PUT request. Done once for the person and once for the garment.
2. PUT each image to its signed URL with the headers the reply gave.
3. POST /s2s/v2.0/task/cloth-v4 with src_file_id, ref_file_id and garment_category. Reply: task_id.
4. GET /s2s/v2.0/task/cloth-v4/{task_id}. data.task_status is running, success or error.
5. On success, download data.results.url, a signed link valid for two hours.

Spend rules: uploads create no paid work, so a failure before step 3 is a definite rejection.
At step 3 only an explicit 400, 401 or 429 proves no task exists; any other outcome raises
ProviderOutcomeUnknown, so the job ends as uncertain and keeps its unit reservation. The
adapter never retries step 3. Failed tasks are counted as charged until the provider's
billing for them is confirmed. The API key is sent only to the API host, never to signed
storage URLs, and never appears in errors.

Makeup VTO and Nail VTO share the same file, task, poll and billing flow, with their own task path
and payload. Their payloads follow the published OpenAPI definitions (makeup_vto.json and
ai_nail_vto.json from docs.perfectcorp.com, read 27 October 2026), which list required fields the
reference pages leave out. The first beauty probes on 27 October 2026 were refused with
InvalidParameters for lip colour and nails until those fields were added; foundation succeeded.
The result URL appears both as results.url and results[0].download_url, so both are accepted.

Earrings VTO follows ai_earrings.json (read 28 October 2026): the face and one earring's product photo
are uploaded, then named again in source_info and object_infos. A photo of a single earring lets the
request set its size in centimetres; a photo of a pair would switch every parameter to auto-detection.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode
from dataclasses import dataclass, field
from typing import Callable

from .providers import (
    FAILED, RUNNING, SUCCEEDED, UNCERTAIN, PollResult, PreviewInput, ProviderOutcomeUnknown,
    ProviderRejected, StartResult,
)

HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_BASE_URL = "https://yce-api-01.makeupar.com"
FILE_PATH = "/s2s/v2.0/file"
TASK_PATH = "/s2s/v2.0/task/cloth-v4"
CREDIT_PATH = "/s2s/v1.0/client/credit"
HISTORY_PATH = "/s2s/v1.0/client/credit/history"
BALANCE_TYPES = ("ApiSubsToken", "ApiPaygToken")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024 - 1
MAX_RESULT_BYTES = 25 * 1024 * 1024
DEFINITE_TASK_REJECTIONS = {400: "TASK_REJECTED", 401: "AUTH_FAILED", 429: "RATE_LIMITED"}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict = field(default_factory=dict)


class TransportError(Exception):
    """The request did not complete, so its effect on the server is unknown."""


Transport = Callable[[str, str, dict, "bytes | None", float], HttpResponse]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read(MAX_RESULT_BYTES + 1), dict(response.headers))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read(64 * 1024), dict(exc.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError(type(exc).__name__) from None


def _json(response: HttpResponse) -> dict:
    try:
        data = json.loads(response.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _result_url(data: dict) -> str | None:
    """The result link, wherever this feature puts it: results.url or results[0].download_url."""
    results = data.get("results")
    if isinstance(results, list):
        results = results[0] if results and isinstance(results[0], dict) else {}
    if not isinstance(results, dict):
        return None
    return results.get("url") or results.get("download_url")


def _error_code(data: dict) -> str:
    """A failed task's error as a short code. Earrings VTO puts its code in brackets before a sentence, as in
    "[PHOTO_CHECK_INVALID] Ear size should fit in range ...", so that code is kept rather than the sentence."""
    for text in (data.get("error"), data.get("error_message")):
        match = re.match(r"\s*\[([A-Z_]+)\]", str(text or ""))
        if match:
            return match.group(1)[:40]
    return str(data.get("error") or "PROVIDER_ERROR")[:40]


class _YouCamTaskProvider:
    """The shared flow: upload, create one task, poll, download. Subclasses supply the task and payload."""
    name = "youcam"
    version = ""
    task_path = TASK_PATH
    default_units = 2
    # The longest image side this feature accepts; the caller scales the person photo to fit.
    max_long_side: int | None = None

    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, transport: Transport | None = None,
                 units_per_job: int | None = None, timeout: float = 30.0):
        if not api_key:
            raise ValueError("An API key is required.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport or urllib_transport
        self.units_per_job = self.default_units if units_per_job is None else units_per_job
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r}, version={self.version!r})"

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        raise NotImplementedError

    def _check_inputs(self, job: PreviewInput) -> None:
        pass

    def _api(self, method: str, path: str, payload: dict | None = None) -> HttpResponse:
        # The API answers 415 to a GET without a JSON content type, so every call sends one.
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json",
                   "Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        return self._transport(method, self._base_url + path, headers, body, self._timeout)

    def _upload(self, data: bytes, file_name: str) -> str:
        try:
            reply = self._api("POST", FILE_PATH, {"files": [
                {"content_type": "image/jpg", "file_name": file_name, "file_size": len(data)}]})
        except TransportError:
            raise ProviderRejected("UPLOAD_FAILED") from None
        if reply.status != 200:
            raise ProviderRejected(DEFINITE_TASK_REJECTIONS.get(reply.status, "UPLOAD_FAILED"))
        try:
            entry = _json(reply)["data"]["files"][0]
            file_id, put = entry["file_id"], entry["requests"][0]
            url, headers = put["url"], dict(put.get("headers") or {})
        except (KeyError, IndexError, TypeError):
            raise ProviderRejected("UPLOAD_FAILED") from None
        if not str(url).startswith("https://"):
            raise ProviderRejected("UPLOAD_FAILED")
        try:
            stored = self._transport(put.get("method", "PUT"), url, headers, data, self._timeout)
        except TransportError:
            raise ProviderRejected("UPLOAD_FAILED") from None
        if not 200 <= stored.status < 300:
            raise ProviderRejected("UPLOAD_FAILED")
        return file_id

    def start(self, job: PreviewInput) -> StartResult:
        if not job.consent:
            raise ProviderRejected("PHOTO_CONSENT_REQUIRED")
        if not job.person_image:
            raise ProviderRejected("PERSON_IMAGE_REQUIRED")
        self._check_inputs(job)
        if max(len(job.person_image), len(job.garment_image or b"")) > MAX_UPLOAD_BYTES:
            raise ProviderRejected("IMAGE_TOO_LARGE")
        src_id = self._upload(job.person_image, f"person-{job.job_id}.jpg")
        payload = self._payload(job, src_id)
        try:
            reply = self._api("POST", self.task_path, payload)
        except TransportError:
            raise ProviderOutcomeUnknown("TASK_REQUEST_LOST") from None
        if reply.status in DEFINITE_TASK_REJECTIONS:
            body = _json(reply)
            raise ProviderRejected(str(body.get("error_code") or DEFINITE_TASK_REJECTIONS[reply.status])[:40])
        task_id = (_json(reply).get("data") or {}).get("task_id") if reply.status == 200 else None
        if not task_id:
            raise ProviderOutcomeUnknown(f"TASK_REPLY_{reply.status}")
        return StartResult(str(task_id), self.units_per_job)

    def balance(self) -> float:
        """Read-only: units available across subscription and pay-as-you-go balances."""
        try:
            reply = self._api("GET", CREDIT_PATH)
        except TransportError:
            raise ProviderOutcomeUnknown("BALANCE_REQUEST_LOST") from None
        rows = _json(reply).get("results") if reply.status == 200 else None
        if not isinstance(rows, list):
            raise ProviderOutcomeUnknown(f"BALANCE_REPLY_{reply.status}")
        return round(sum(float(r.get("amount_dec") or 0) for r in rows if r.get("type") in BALANCE_TYPES), 2)

    def unit_history(self, max_pages: int = 10) -> list:
        """Read-only: recent unit history entries. Charges carry target_id "<task_id>:<suffix>"."""
        entries, token = [], None
        for _ in range(max_pages):
            query = {"page_size": 30, **({"starting_token": token} if token else {})}
            try:
                reply = self._api("GET", f"{HISTORY_PATH}?{urlencode(query)}")
            except TransportError:
                raise ProviderOutcomeUnknown("HISTORY_REQUEST_LOST") from None
            result = _json(reply).get("result") if reply.status == 200 else None
            if not isinstance(result, dict):
                raise ProviderOutcomeUnknown(f"HISTORY_REPLY_{reply.status}")
            entries.extend(result.get("history") or [])
            token = result.get("next_token")
            if not token:
                break
        return entries

    def poll(self, provider_task_id: str) -> PollResult:
        try:
            reply = self._api("GET", f"{self.task_path}/{provider_task_id}")
        except TransportError:
            raise ProviderOutcomeUnknown("POLL_REQUEST_LOST") from None
        if reply.status == 500:
            return PollResult(UNCERTAIN, self.units_per_job, "PROVIDER_TIMEOUT")
        if reply.status == 400:
            return PollResult(UNCERTAIN, self.units_per_job, "UNKNOWN_TASK")
        if reply.status != 200:
            raise ProviderOutcomeUnknown(f"POLL_REPLY_{reply.status}")
        data = _json(reply).get("data") or {}
        state = data.get("task_status")
        if state == "running":
            return PollResult(RUNNING)
        if state == "error":
            return PollResult(FAILED, self.units_per_job, _error_code(data))
        if state != "success":
            raise ProviderOutcomeUnknown("POLL_REPLY_UNREADABLE")
        url = _result_url(data)
        if not url or not str(url).startswith("https://"):
            return PollResult(FAILED, self.units_per_job, "RESULT_MISSING")
        try:
            image = self._transport("GET", url, {}, None, self._timeout)
        except TransportError:
            raise ProviderOutcomeUnknown("RESULT_DOWNLOAD_LOST") from None
        if image.status != 200 or not image.body or len(image.body) > MAX_RESULT_BYTES:
            raise ProviderOutcomeUnknown("RESULT_DOWNLOAD_FAILED")
        return PollResult(SUCCEEDED, self.units_per_job, image=image.body)


class YouCamClothesProvider(_YouCamTaskProvider):
    name = "youcam"
    version = "cloth-v4"
    task_path = TASK_PATH
    default_units = 2

    def __init__(self, api_key: str, *, garment_category: str = "upper_body", filter_multi_person: str = "normal",
                 **options):
        super().__init__(api_key, **options)
        self._category = garment_category
        self._filter = filter_multi_person

    def _check_inputs(self, job: PreviewInput) -> None:
        if not job.garment_image:
            raise ProviderRejected("GARMENT_IMAGE_REQUIRED")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        ref_id = self._upload(job.garment_image, f"garment-{job.sku}-v{job.variant_version}.jpg")
        return {"src_file_id": src_id, "ref_file_id": ref_id, "garment_category": self._category,
                "filter_multi_person": self._filter}


def _shade(job: PreviewInput) -> dict:
    params = job.params or {}
    if not HEX_COLOUR.match(str(params.get("color") or "")):
        raise ProviderRejected("SHADE_REQUIRED")
    return params


# Blush also needs a placement pattern from YouCam's blush.json, so it stays unsupported until a
# blush line exists and its pattern is chosen.
MAKEUP_CATEGORIES = {"lip": "lip_color", "foundation": "foundation"}
# Lip textures in the spec. A cream lipstick is satin. Gloss also needs gloss and transparencyIntensity.
LIP_TEXTURES = {"matte": "matte", "cream": "satin", "satin": "satin", "gloss": "gloss"}
# Lip colour requires a shape and a style: "original" keeps the person's own lip outline (a label in
# YouCam's lipshape.json) and "full" is one even shade.
LIP_SHAPE, LIP_STYLE = {"name": "original"}, {"type": "full"}
# Foundation as the beauty pilot sent it. A retest on 28 October 2026 at colour 100, coverage 80 and glow 10
# lightened Face A's cheek by the same amount (L* 55 to 63) and read no closer to the 220W swatch (L* 74):
# foundation blends with the skin beneath it, so these settings stay.
FOUNDATION = {"colorIntensity": 70, "coverageIntensity": 50, "glowIntensity": 30}


class YouCamMakeupProvider(_YouCamTaskProvider):
    """Makeup VTO: one shade on the shopper's own face. 1 unit, and none when the task errors."""
    name = "youcam-makeup"
    version = "makeup-vto-1.1"
    task_path = "/s2s/v2.0/task/makeup-vto"
    default_units = 1
    max_long_side = 1900

    def _check_inputs(self, job: PreviewInput) -> None:
        if MAKEUP_CATEGORIES.get(_shade(job).get("category")) is None:
            raise ProviderRejected("UNSUPPORTED_CATEGORY")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        params = _shade(job)
        category = MAKEUP_CATEGORIES[params["category"]]
        palette = {"color": params["color"], "colorIntensity": int(params.get("intensity", 70))}
        effect = {"category": category, "palettes": [palette]}
        if category == "lip_color":
            palette["texture"] = LIP_TEXTURES.get(params.get("finish") or "", "matte")
            if palette["texture"] == "gloss":
                palette.update({"gloss": 60, "transparencyIntensity": 30})
            effect.update({"shape": LIP_SHAPE, "style": LIP_STYLE})
        else:
            palette.update(FOUNDATION)
        return {"version": "1.0", "src_file_id": src_id, "effects": [effect]}


NAIL_FINGERS = ("thumb", "index", "middle", "ring", "pinky")
# Each catalog finish as a Nail VTO texture plus the sliders (0-100) the spec requires on every
# colour effect: reflection, contrast and roughness, and transparency for all but metallic. A gloss
# polish is a cream texture with a strong, sharp reflection.
NAIL_FINISHES = {
    "gloss": {"texture": "cream", "transparency": 0, "reflection": 70, "contrast": 50, "roughness": 10},
    "cream": {"texture": "cream", "transparency": 0, "reflection": 50, "contrast": 50, "roughness": 30},
    "matte": {"texture": "matte", "transparency": 0, "reflection": 10, "contrast": 30, "roughness": 80},
    "jelly": {"texture": "jelly", "transparency": 40, "reflection": 70, "contrast": 50, "roughness": 10},
    "metallic": {"texture": "metallic", "reflection": 80, "contrast": 70, "roughness": 20},
}


class YouCamNailProvider(_YouCamTaskProvider):
    """Nail VTO: one polish colour on the shopper's own hand. 1 unit."""
    name = "youcam-nail"
    version = "nail-vto-1.1"
    task_path = "/s2s/v2.0/task/nail-vto"
    default_units = 1
    max_long_side = 2048

    def _check_inputs(self, job: PreviewInput) -> None:
        _shade(job)

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        params = _shade(job)
        finish = NAIL_FINISHES.get(params.get("finish") or "", NAIL_FINISHES["cream"])
        return {"version": "1.0", "src_file_id": src_id, "effect_type": "nail_polish",
                "effects": [{"sub_type": "color", "finger": finger, "color": params["color"], **finish}
                            for finger in NAIL_FINGERS]}


HAIR_COLOR_PATH = "/s2s/v2.0/task/hair-color"
EARRING_PATH = "/s2s/v2.0/task/2d-vto/earring"
NECKLACE_PATH = "/s2s/v2.0/task/2d-vto/necklace"


# One even colour over all the hair, at the intensities a permanent dye reads as. Ombre mode exists too but a
# catalog shade is one colour.
HAIR_PATTERN = {"name": "full"}
HAIR_PALETTE = {"color_intensity": 80, "shine_intensity": 30}


class YouCamHairColorProvider(_YouCamTaskProvider):
    """AI Hair Color, from ai_hair_color.json read 28 October 2026: one shade over the hair in the photo, which must
    show the hair and be under 1920 px on its long side. 1 unit in full mode."""
    name = "youcam-hair"
    version = "hair-color-1.0"
    task_path = HAIR_COLOR_PATH
    default_units = 1
    max_long_side = 1900

    def _check_inputs(self, job: PreviewInput) -> None:
        _shade(job)

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        return {"src_file_id": src_id, "pattern": dict(HAIR_PATTERN),
                "palettes": [{"color": _shade(job)["color"], **HAIR_PALETTE}]}


class YouCamEarringProvider(_YouCamTaskProvider):
    """Earrings VTO: one earring from its product photo, worn on the person's ear. 1 unit for one earring."""
    name = "youcam-earring"
    version = "earring-vto-1.1"
    task_path = EARRING_PATH
    default_units = 1
    max_long_side = 4096

    def _check_inputs(self, job: PreviewInput) -> None:
        if not job.garment_image:
            raise ProviderRejected("PRODUCT_IMAGE_REQUIRED")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        ref_id = self._upload(job.garment_image, f"earring-{job.sku}-v{job.variant_version}.jpg")
        parameter = {"earring_need_remove_background": True}
        params = job.params or {}
        # The size in centimetres, as the part below the hook, with the anchor where the hook meets the ear. Neither
        # changed anything on 28 October 2026: a drop told 2.4 cm rendered pixel for pixel as it had when told 3.8,
        # so this engine sizes an earring from its photo, not from these fields. The size check on each result is
        # what tells the shopper.
        length = params.get("scale_mm") or params.get("length_mm")
        if length:
            parameter["earring_scale"] = round(length / 10, 2)
        if params.get("anchor"):
            parameter["earring_anchor_point"] = [int(v) for v in params["anchor"]]
        return {"src_file_id": src_id, "ref_file_ids": [ref_id], "source_info": {"name": src_id},
                "object_infos": [{"name": ref_id, "parameter": parameter}]}


class YouCamNecklaceProvider(_YouCamTaskProvider):
    """Necklace VTO, from ai_necklace.json read 28 October 2026: a front-facing product photo of the necklace as worn,
    whose background the engine removes, on a front-facing photo with the neck visible. 1 unit for one necklace."""
    name = "youcam-necklace"
    version = "necklace-vto-1.0"
    task_path = NECKLACE_PATH
    default_units = 1
    max_long_side = 4096

    def _check_inputs(self, job: PreviewInput) -> None:
        if not job.garment_image:
            raise ProviderRejected("PRODUCT_IMAGE_REQUIRED")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        ref_id = self._upload(job.garment_image, f"necklace-{job.sku}-v{job.variant_version}.jpg")
        return {"src_file_id": src_id, "ref_file_ids": [ref_id], "source_info": {"name": src_id},
                "object_infos": [{"name": ref_id, "parameter": {"necklace_need_remove_background": True}}]}


SHOES_PATH = "/s2s/v2.0/task/shoes-transfer"
BAG_PATH = "/s2s/v2.0/task/bag"
BAG_GENDERS = ("female", "male")


class YouCamShoesProvider(_YouCamTaskProvider):
    """Shoes Virtual Try-On V3.0, from ai_shoes.json and its overview read 28 October 2026: one product photo of the
    shoes, at least 512 px with the product over 25% of its height, on a full-body photo of one person whose feet are
    fully visible. 2 units per result."""
    name = "youcam-shoes"
    version = "shoes-v3-1.0"
    task_path = SHOES_PATH
    default_units = 2
    max_long_side = 4096

    def _check_inputs(self, job: PreviewInput) -> None:
        if not job.garment_image:
            raise ProviderRejected("PRODUCT_IMAGE_REQUIRED")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        ref_id = self._upload(job.garment_image, f"shoes-{job.sku}-v{job.variant_version}.jpg")
        return {"src_file_id": src_id, "ref_file_id": ref_id}


class YouCamBagProvider(_YouCamTaskProvider):
    """Bag Virtual Try-On V2.0, from ai_bag.json and its reference page read 28 October 2026: one product photo of the
    bag on a head-to-chest or half-body photo, face over 15% of its height. YouCam requires the gender of the person
    in the photo; Rechoice sends only the one recorded for each AI-generated demo person, never one inferred from a
    shopper's photo. 2 units per result."""
    name = "youcam-bag"
    version = "bag-v2-1.0"
    task_path = BAG_PATH
    default_units = 2
    max_long_side = 4096

    def _check_inputs(self, job: PreviewInput) -> None:
        if not job.garment_image:
            raise ProviderRejected("PRODUCT_IMAGE_REQUIRED")
        if (job.params or {}).get("gender") not in BAG_GENDERS:
            raise ProviderRejected("DEMO_PERSON_REQUIRED")

    def _payload(self, job: PreviewInput, src_id: str) -> dict:
        ref_id = self._upload(job.garment_image, f"bag-{job.sku}-v{job.variant_version}.jpg")
        return {"src_file_id": src_id, "ref_file_id": ref_id, "gender": job.params["gender"]}
