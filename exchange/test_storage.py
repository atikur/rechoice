"""Private image storage: the local disk and a private S3-compatible bucket, with no network.

The S3 backend is checked twice: against botocore's own Stubber, which validates every request against
the real S3 API model, and through a whole earring comparison on an in-memory bucket.
"""
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import boto3
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from exchange import assets, preview_jobs, tasks
from exchange.models import OrderLine, PreviewJob, PrivateAsset
from secondlook.youcam_fake import API, FakeYouCamTransport
from secondlook.youcam_provider import YouCamEarringProvider

BUCKET = dict(PRIVATE_STORAGE="s3", PRIVATE_STORAGE_BUCKET="secondlook-private-test",
              PRIVATE_STORAGE_ENDPOINT="https://t3.storageapi.dev", PRIVATE_STORAGE_REGION="auto",
              PRIVATE_STORAGE_ADDRESSING="virtual", PRIVATE_STORAGE_ACCESS_KEY_ID="test-only",
              PRIVATE_STORAGE_SECRET_ACCESS_KEY="test-only")
KEY = "0123456789abcdef0123456789abcdef.jpg"


class MemoryBucket:
    """An S3 client that keeps objects in a dict and fails as S3 does for a missing key."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (bytes(Body), ContentType)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class LocalStorageTests(SimpleTestCase):
    def test_an_image_is_saved_read_checked_and_deleted_on_disk_owner_only(self):
        with tempfile.TemporaryDirectory() as media, override_settings(PRIVATE_MEDIA_ROOT=media, PRIVATE_STORAGE="local"):
            key, digest = assets.save_private_image(b"jpeg bytes")
            path = Path(media) / key
            self.assertEqual((path.read_bytes(), oct(path.stat().st_mode & 0o777)), (b"jpeg bytes", "0o600"))
            self.assertEqual((assets.read_private_image(key), assets.private_image_exists(key)), (b"jpeg bytes", True))
            assets.delete_private_image(key)
            assets.delete_private_image(key)
            self.assertFalse(assets.private_image_exists(key))
            with self.assertRaises(FileNotFoundError):
                assets.read_private_image(key)

    def test_a_key_that_could_leave_the_folder_is_refused(self):
        for key in ("../settings.py", "a/b.jpg", "", "image.png"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                assets.read_private_image(key)


@override_settings(**BUCKET)
class S3RequestTests(SimpleTestCase):
    """Each call is checked against the real S3 API model; nothing leaves the machine."""

    def setUp(self):
        self.client = boto3.client("s3", endpoint_url=BUCKET["PRIVATE_STORAGE_ENDPOINT"], region_name="auto",
                                   aws_access_key_id="test-only", aws_secret_access_key="test-only")
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.storage = assets.S3Storage(client=self.client)
        patch = mock.patch.dict(assets._storages, {"s3": self.storage})
        patch.start()
        self.addCleanup(patch.stop)

    def test_saving_puts_a_private_jpeg_under_its_random_key(self):
        self.stub.add_response("put_object", {}, {"Bucket": "secondlook-private-test", "Key": mock.ANY,
                                                  "Body": b"jpeg bytes", "ContentType": "image/jpeg"})
        key, digest = assets.save_private_image(b"jpeg bytes")
        self.assertRegex(key, r"^[0-9a-f]{32}\.jpg$")
        self.stub.assert_no_pending_responses()

    def test_reading_returns_the_bytes_and_a_missing_key_is_file_not_found(self):
        self.stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"jpeg bytes"), 10)},
                               {"Bucket": "secondlook-private-test", "Key": KEY})
        self.assertEqual(assets.read_private_image(KEY), b"jpeg bytes")
        self.stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        with self.assertRaises(FileNotFoundError):
            assets.read_private_image(KEY)

    def test_exists_reads_the_head_and_other_errors_are_not_hidden(self):
        self.stub.add_response("head_object", {}, {"Bucket": "secondlook-private-test", "Key": KEY})
        self.assertTrue(assets.private_image_exists(KEY))
        self.stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        self.assertFalse(assets.private_image_exists(KEY))
        self.stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
        with self.assertRaises(ClientError):
            assets.private_image_exists(KEY)

    def test_deleting_removes_the_object(self):
        self.stub.add_response("delete_object", {}, {"Bucket": "secondlook-private-test", "Key": KEY})
        assets.delete_private_image(KEY)
        self.stub.assert_no_pending_responses()

    def test_the_client_uses_the_bucket_settings(self):
        client = assets.S3Storage().client
        self.assertEqual((client.meta.endpoint_url, client.meta.region_name),
                         ("https://t3.storageapi.dev", "auto"))
        self.assertEqual(client.meta.config.s3["addressing_style"], "virtual")


@override_settings(**BUCKET)
class S3FlowTests(TestCase):
    """A whole earring comparison with every image in the bucket and none on this machine's disk."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        self.bucket = MemoryBucket()
        patch = override_settings(PRIVATE_MEDIA_ROOT=self.media, PREVIEW_POLL_SECONDS=0)
        patch.enable()
        self.addCleanup(patch.disable)
        backend = mock.patch.dict(assets._storages, {"s3": assets.S3Storage(client=self.bucket)})
        backend.start()
        self.addCleanup(backend.stop)

    def send(self, method, path, payload=None):
        return getattr(self.client, method)(path, data=json.dumps(payload or {}), content_type="application/json")

    def stored(self):
        return {key for _, key in self.bucket.objects}

    def test_photos_results_and_resized_copies_live_in_the_bucket_and_go_with_the_photo(self):
        face = Image.open(Path(__file__).resolve().parent / "static/exchange/models/face-a.jpg").convert("RGB")

        def drawn(height):
            out = io.BytesIO()
            image = face.copy()
            ImageDraw.Draw(image).rectangle((930, 520, 960, 520 + height), fill=(20, 20, 20))
            image.save(out, format="JPEG", quality=92)
            return out.getvalue()
        images = [drawn(142), drawn(140), drawn(29)]
        fake = FakeYouCamTransport(result_image=lambda: images.pop(0), units_per_task=1)
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/item", {"line_id": OrderLine.objects.get(token="DEMO-ORDER-001-EAR").id})
        self.send("put", f"/api/sessions/{sid}/reason", {"reason": "look"})
        self.send("put", f"/api/sessions/{sid}/preferences", {"max_top_up_minor": 500, "size": "smaller", "metal": "silver"})
        skus = [c["sku"] for c in self.client.get(f"/api/sessions/{sid}/candidates").json()["candidates"]]
        person = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "face-a"}).json()["asset_id"]
        with override_settings(PREVIEW_PROVIDER="youcam", PREVIEW_UNIT_CEILING=50, PREVIEW_DAILY_UNIT_CAP=50), \
                mock.patch.dict(preview_jobs.PROVIDERS, {
                    "youcam-earring": lambda: YouCamEarringProvider("test-only-key", base_url=API, transport=fake)}):
            data = self.send("post", f"/api/sessions/{sid}/previews", {
                "candidate_skus": skus, "consent": True, "asset_id": person, "include_original": True,
                "mode": "live"}).json()
        # The person photo, three results and the crystal's resized copy, all in the bucket, none on disk.
        self.assertEqual(len(self.stored()), 5)
        self.assertEqual(self.stored(), set(PrivateAsset.objects.values_list("storage_key", flat=True)))
        self.assertEqual(list(self.media.iterdir()), [])
        self.assertTrue(all(kind == "image/jpeg" for _, kind in self.bucket.objects.values()))
        crystal = next(j for j in data["jobs"] if j["sku"] == "EAR-CRYSTAL-S38")
        self.assertEqual(crystal["size_check"]["status"], "size-resized")
        served = self.client.get(crystal["image_url"])
        self.assertEqual((served.status_code, served["Cache-Control"]), (200, "private, no-store"))
        self.assertTrue(served.content.startswith(b"\xff\xd8"))
        # Withdrawing the photo empties the bucket.
        self.assertEqual(self.client.delete(f"/api/assets/{person}").status_code, 200)
        self.assertEqual(self.stored(), set())
        self.assertEqual(self.client.get(crystal["image_url"]).status_code, 404)
        self.assertEqual(PreviewJob.objects.filter(provider="youcam-earring").count(), 3)

    def test_cleanup_deletes_expired_images_from_the_bucket_and_keeps_one_it_could_not(self):
        sid = self.send("post", "/api/sessions", {"order_token": "DEMO-ORDER-001"}).json()["session_id"]
        self.send("put", f"/api/sessions/{sid}/item", {"line_id": OrderLine.objects.get(token="DEMO-ORDER-001").id})
        person = self.send("post", f"/api/sessions/{sid}/demo-person", {"model": "model-a"}).json()["asset_id"]
        self.assertEqual(len(self.stored()), 1)
        PrivateAsset.objects.filter(pk=person).update(expires_at=timezone.now() - timedelta(minutes=1))
        with mock.patch.object(self.bucket, "delete_object", side_effect=ClientError(
                {"Error": {"Code": "SlowDown"}}, "DeleteObject")), self.assertLogs("exchange.cleanup", "ERROR"):
            self.assertEqual(tasks.cleanup_expired()["assets_failed"], 1)
        self.assertTrue(PrivateAsset.objects.filter(pk=person).exists())
        self.assertEqual(tasks.cleanup_expired()["assets_removed"], 1)
        self.assertEqual(self.stored(), set())
