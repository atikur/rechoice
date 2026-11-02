import hashlib
import io
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from PIL import Image

from exchange.assets import normalize_photo, private_path
from exchange.models import ExchangeSession, PreviewJob, PrivateAsset


def image_bytes(size, fmt="PNG", exif_orientation=None):
    out = io.BytesIO()
    image = Image.new("RGB", size, "#6688aa")
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        image.save(out, format=fmt, exif=exif.tobytes())
    else:
        image.save(out, format=fmt)
    return out.getvalue()


def upload(size=(800, 1000), fmt="PNG", **kwargs):
    content_type = "image/png" if fmt == "PNG" else "image/jpeg"
    return SimpleUploadedFile(f"photo.{fmt.lower()}", image_bytes(size, fmt, **kwargs), content_type=content_type)


class PhotoCheckTests(TestCase):
    def test_size_and_shape_limits_follow_youcam(self):
        cases = {(500, 400): "too small", (600, 380): "too small", (4200, 3000): "too large",
                 (3000, 1100): "too narrow or too wide"}
        for size, message in cases.items():
            with self.subTest(size=size), self.assertRaisesMessage(ValidationError, message):
                normalize_photo(upload(size))
        for size in ((512, 384), (768, 1024), (1024, 768)):
            with self.subTest(size=size):
                self.assertTrue(normalize_photo(upload(size)).startswith(b"\xff\xd8"))

    def test_phone_rotation_is_applied_and_metadata_dropped(self):
        data = normalize_photo(upload((1000, 750), fmt="JPEG", exif_orientation=6))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (750, 1000))
            self.assertEqual(dict(image.getexif()), {})


class PhotoStepTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        patch = override_settings(PRIVATE_MEDIA_ROOT=media.name)
        patch.enable()
        self.addCleanup(patch.disable)

    def send(self, method, path, payload=None, client=None):
        return getattr(client or self.client, method)(path, data=json.dumps(payload or {}),
                                                      content_type="application/json")

    def session(self, client=None):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}, client).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/preferences", {"quieter_print": True, "sleeve_required": "long",
                                                             "max_top_up_minor": 1000, "currency": "USD"}, client)
        return sid

    def demo(self, sid, model="model-a", client=None):
        return self.send("post", f"/api/sessions/{sid}/demo-person", {"model": model}, client)

    def test_demo_person_becomes_a_private_session_asset(self):
        sid = self.session()
        first = self.demo(sid)
        self.assertEqual(first.status_code, 201)
        body = first.json()
        self.assertEqual(body["label"], "Model A")
        asset = PrivateAsset.objects.get(id=body["asset_id"])
        self.assertEqual(asset.kind, "person")
        self.assertIn("AI-generated demo model model-a", asset.license_or_consent)
        self.assertEqual(hashlib.sha256(private_path(asset.storage_key).read_bytes()).hexdigest(), asset.sha256)
        self.assertTrue(ExchangeSession.objects.get(id=sid).consent_given)
        self.assertEqual(self.demo(sid).json()["asset_id"], body["asset_id"])
        self.assertEqual(self.client.get(body["image_url"]).status_code, 200)
        self.assertEqual(Client().get(body["image_url"]).status_code, 404)

    def test_demo_person_rejects_unknown_models_and_other_browsers(self):
        sid = self.session()
        self.assertEqual(self.demo(sid, "model-d").json()["error"]["code"], "UNKNOWN_MODEL")
        self.assertEqual(self.demo(sid, "../../etc/passwd").status_code, 422)
        self.assertEqual(self.demo(sid, client=Client()).status_code, 404)
        self.assertFalse(PrivateAsset.objects.exists())

    def test_demo_people_work_while_uploads_are_off(self):
        sid = self.session()
        asset_id = self.demo(sid, "model-b").json()["asset_id"]
        jobs = self.send("post", f"/api/sessions/{sid}/previews",
                         {"candidate_skus": ["CALM-07", "CALM-04"], "consent": True, "asset_id": asset_id}).json()["jobs"]
        self.assertEqual([j["state"] for j in jobs], ["succeeded", "succeeded"])
        self.assertEqual(set(PreviewJob.objects.values_list("person_asset_id", flat=True)), {PrivateAsset.objects.get().id})
        refused = self.client.post(f"/api/sessions/{sid}/assets", {"image": upload(), "adult_permission": "true"})
        self.assertEqual(refused.json()["error"]["code"], "UPLOAD_DISABLED")

    @override_settings(ALLOW_PHOTO_UPLOAD=True, UPLOAD_LIMIT_PER_SESSION=3, UPLOAD_LIMIT_PER_ADDRESS=4)
    def test_uploads_are_rate_limited_per_session_and_per_address(self):
        sid = self.session()
        for _ in range(3):
            response = self.client.post(f"/api/sessions/{sid}/assets", {"image": upload(), "adult_permission": "true"})
            self.assertEqual(response.status_code, 201, response.content)
            self.assertTrue(response.json()["image_url"].startswith("/api/assets/"))
        limited = self.client.post(f"/api/sessions/{sid}/assets", {"image": upload(), "adult_permission": "true"})
        self.assertEqual((limited.status_code, limited.json()["error"]["code"]), (429, "UPLOAD_LIMIT"))
        other = Client()
        other_sid = self.session(other)
        self.assertEqual(other.post(f"/api/sessions/{other_sid}/assets",
                                    {"image": upload(), "adult_permission": "true"}).status_code, 429)
        self.assertEqual(PrivateAsset.objects.count(), 3)

    @override_settings(ALLOW_PHOTO_UPLOAD=True)
    def test_unsuitable_upload_gets_a_specific_message(self):
        sid = self.session()
        response = self.client.post(f"/api/sessions/{sid}/assets", {"image": upload((600, 300)), "adult_permission": "true"})
        self.assertEqual(response.json()["error"]["message"], "This photo is too small. Use one at least 512 by 384 pixels.")

    def test_page_explains_photo_use_and_offers_demo_models(self):
        html = self.client.get("/").content.decode()
        for phrase in ("sends that person's photo and the product photos to YouCam",
                       "keeps uploaded and generated images for up to 30 days",
                       "does not use them to train models", "Deleting a photo here does not delete YouCam's copy",
                       "AI-generated images from Pixabay"):
            self.assertIn(phrase, html)
        for name, kind in (("model-a", "person"), ("model-b", "person"), ("model-c", "person"),
                           ("face-a", "face"), ("face-b", "face"), ("face-c", "face"), ("hand-a", "hand")):
            self.assertIn(f'data-kind="{kind}"><input type="radio" name="person" value="demo:{name}"', html)
        # Own photos are off by default: the option is in the page, hidden and marked not allowed, until a judge
        # unlocks it or the server allows uploads.
        self.assertIn('id="uploadOption" data-allowed="" hidden', html)
        with override_settings(ALLOW_PHOTO_UPLOAD=True):
            html = self.client.get("/").content.decode()
        self.assertIn('id="uploadOption" data-allowed="1">', html)
        self.assertIn('id="photoFile"', html)
        self.assertIn("Chest up, with both shoulders in the photo.", html)

    def test_demo_model_manifest_matches_files(self):
        directory = Path(finders.find("exchange/models/manifest.json")).parent
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual([(m["name"], m["kind"]) for m in manifest["models"]],
                         [("model-a", "person"), ("model-b", "person"), ("model-c", "person"), ("model-f", "full"),
                          ("face-a", "face"), ("face-b", "face"), ("face-c", "face"), ("hand-a", "hand")])
        sources = json.loads((Path(settings.BASE_DIR) / "assets" / "pixabay_sources.json").read_text())
        alternates = [m["name"] for m in sources["beauty_models"] if m["use"] == "alternate"]
        self.assertEqual(alternates, ["face-d", "face-e", "face-f", "hand-b"])
        for model in manifest["models"]:
            with self.subTest(model=model["name"]):
                self.assertTrue(model["source"]["ai_generated"])
                # Makeup VTO takes up to 1919 px; Nail VTO wants 1:1, 3:4 or 4:3.
                if model["kind"] == "face":
                    self.assertLess(max(model["width"], model["height"]), 1920)
                if model["kind"] == "hand":
                    self.assertIn(model["width"] / model["height"], (1.0, 0.75, 4 / 3))
                data = (directory / model["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), model["sha256"])
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(dict(image.getexif()), {})
