import json
import unittest

from secondlook.providers import (
    FAILED, RUNNING, SUCCEEDED, UNCERTAIN, PreviewInput, ProviderOutcomeUnknown, ProviderRejected,
)
from secondlook.youcam_fake import API, STORAGE, FakeYouCamTransport, TransportError, reply, task_status
from secondlook.youcam_provider import FILE_PATH, MAX_UPLOAD_BYTES, TASK_PATH, YouCamClothesProvider

KEY = "test-only-key-not-real"


def job(**changes):
    values = dict(job_id="job-1", sku="CALM-04", variant_version=1, garment_asset_id="exchange/catalog/CALM-04.jpg",
                  person_image=b"person-bytes", consent=True, consent_version=1, garment_image=b"garment-bytes")
    values.update(changes)
    return PreviewInput(**values)


def provider(fake):
    return YouCamClothesProvider(KEY, base_url=API, transport=fake)


class StartTests(unittest.TestCase):
    def test_uploads_both_images_then_creates_one_task(self):
        fake = FakeYouCamTransport()
        started = provider(fake).start(job())
        self.assertEqual((started.provider_task_id, started.reserved_units), ("task-1", 2))
        self.assertEqual(fake.uploads, {"file-1": b"person-bytes", "file-2": b"garment-bytes"})
        (task_call,) = fake.api_calls("POST", TASK_PATH)
        self.assertEqual(json.loads(task_call[3]), {"src_file_id": "file-1", "ref_file_id": "file-2",
                                                    "garment_category": "upper_body",
                                                    "filter_multi_person": "normal"})

    def test_key_goes_only_to_the_api_host(self):
        fake = FakeYouCamTransport()
        provider(fake).start(job())
        for method, url, headers, _ in fake.calls:
            with self.subTest(url=url):
                if url.startswith(API):
                    self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
                else:
                    self.assertNotIn("Authorization", headers)
                    self.assertTrue(url.startswith(STORAGE))
        self.assertNotIn(KEY, repr(provider(fake)))

    def test_missing_inputs_are_rejected_before_any_request(self):
        cases = {"PHOTO_CONSENT_REQUIRED": dict(consent=False), "PERSON_IMAGE_REQUIRED": dict(person_image=None),
                 "GARMENT_IMAGE_REQUIRED": dict(garment_image=None),
                 "IMAGE_TOO_LARGE": dict(person_image=b"x" * (MAX_UPLOAD_BYTES + 1))}
        for code, changes in cases.items():
            with self.subTest(code=code):
                fake = FakeYouCamTransport()
                with self.assertRaises(ProviderRejected) as ctx:
                    provider(fake).start(job(**changes))
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(fake.calls, [])

    def test_upload_failure_is_a_definite_rejection_with_no_task(self):
        for file_reply, code in ((reply(401, {"error_code": "InvalidAccessToken"}), "AUTH_FAILED"),
                                 (reply(503), "UPLOAD_FAILED")):
            with self.subTest(status=file_reply.status):
                fake = FakeYouCamTransport(file_api=file_reply)
                with self.assertRaises(ProviderRejected) as ctx:
                    provider(fake).start(job())
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(fake.api_calls("POST", TASK_PATH), [])

    def test_explicit_task_refusals_are_definite(self):
        for status, body, code in ((400, {"error_code": "invalid_parameter"}, "invalid_parameter"),
                                   (401, {}, "AUTH_FAILED"), (429, {}, "RATE_LIMITED")):
            with self.subTest(status=status):
                fake = FakeYouCamTransport(task=reply(status, body))
                with self.assertRaises(ProviderRejected) as ctx:
                    provider(fake).start(job())
                self.assertEqual(ctx.exception.code, code)

    def test_unclear_task_outcomes_are_never_called_rejections(self):
        for task, code in ((TransportError("TimeoutError"), "TASK_REQUEST_LOST"),
                           (reply(502), "TASK_REPLY_502"), (reply(200, {"status": 200}), "TASK_REPLY_200")):
            with self.subTest(code=code):
                fake = FakeYouCamTransport(task=task)
                with self.assertRaises(ProviderOutcomeUnknown) as ctx:
                    provider(fake).start(job())
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(len(fake.api_calls("POST", TASK_PATH)), 1)


class PollTests(unittest.TestCase):
    def test_running_then_success_downloads_without_the_key(self):
        result_url = f"{STORAGE}/result/task-1.jpg"
        fake = FakeYouCamTransport(result_image=b"result-image",
                                   polls=[task_status("running"), task_status("success", url=result_url)])
        adapter = provider(fake)
        self.assertEqual(adapter.poll("task-1").state, RUNNING)
        done = adapter.poll("task-1")
        self.assertEqual((done.state, done.observed_units, done.image), (SUCCEEDED, 2, b"result-image"))
        download = [c for c in fake.calls if c[1] == result_url][0]
        self.assertNotIn("Authorization", download[2])

    def test_every_api_call_sends_a_json_content_type(self):
        fake = FakeYouCamTransport(polls=[task_status("running")])
        adapter = provider(fake)
        adapter.start(job())
        adapter.poll("task-1")
        api_calls = [c for c in fake.calls if c[1].startswith(API)]
        self.assertEqual({c[0] for c in api_calls}, {"POST", "GET"})
        for method, url, headers, _ in api_calls:
            with self.subTest(method=method, url=url):
                self.assertEqual(headers.get("Content-Type"), "application/json")

    def test_engine_error_counts_units_until_billing_is_confirmed(self):
        fake = FakeYouCamTransport(polls=[task_status("error", error="error_pose")])
        result = provider(fake).poll("task-1")
        self.assertEqual((result.state, result.error_code, result.observed_units), (FAILED, "error_pose", 2))

    def test_a_bracketed_engine_code_is_kept_without_its_sentence(self):
        polled = reply(200, {"status": 200, "data": {
            "task_status": "error", "error": "[PHOTO_CHECK_INVALID] Ear size should fit in range and position should be "
                                             "in the center.", "error_message": "Ear size should fit in range."}})
        result = provider(FakeYouCamTransport(polls=[polled])).poll("task-1")
        self.assertEqual((result.state, result.error_code), (FAILED, "PHOTO_CHECK_INVALID"))

    def test_timeouts_and_lost_tasks_are_uncertain(self):
        for status, code in ((500, "PROVIDER_TIMEOUT"), (400, "UNKNOWN_TASK")):
            with self.subTest(status=status):
                result = provider(FakeYouCamTransport(polls=[reply(status)])).poll("task-1")
                self.assertEqual((result.state, result.error_code, result.observed_units), (UNCERTAIN, code, 2))

    def test_transient_poll_problems_raise_so_the_runner_polls_again(self):
        for polled in (TransportError("URLError"), reply(429), task_status("queued")):
            with self.subTest(polled=polled):
                with self.assertRaises(ProviderOutcomeUnknown):
                    provider(FakeYouCamTransport(polls=[polled])).poll("task-1")

    def test_success_without_a_safe_result_url_fails(self):
        result = provider(FakeYouCamTransport(polls=[task_status("success", url="http://plain.invalid/x.jpg")])).poll("t")
        self.assertEqual((result.state, result.error_code), (FAILED, "RESULT_MISSING"))


class ReadOnlyTests(unittest.TestCase):
    def test_balance_and_history_are_read_only_calls(self):
        fake = FakeYouCamTransport(balance=12.5)
        adapter = provider(fake)
        self.assertEqual(adapter.balance(), 12.5)
        adapter.start(job())
        history = adapter.unit_history()
        self.assertEqual(history[0]["target_id"], "task-1:000000000001")
        read_calls = [c for c in fake.calls if "/client/credit" in c[1]]
        self.assertEqual({c[0] for c in read_calls}, {"GET"})

    def test_unreadable_balance_or_history_raise(self):
        with self.assertRaises(ProviderOutcomeUnknown):
            provider(FakeYouCamTransport(balance_reply=reply(500))).balance()
        with self.assertRaises(ProviderOutcomeUnknown):
            provider(FakeYouCamTransport(balance_reply=TransportError("URLError"))).balance()


class ConstructionTests(unittest.TestCase):
    def test_requires_a_key(self):
        with self.assertRaises(ValueError):
            YouCamClothesProvider("")
        self.assertEqual((YouCamClothesProvider(KEY).name, YouCamClothesProvider(KEY).version), ("youcam", "cloth-v4"))


if __name__ == "__main__":
    unittest.main()


class BeautyAdapterTests(unittest.TestCase):
    def setUp(self):
        from secondlook.providers import PreviewInput
        self.input = lambda **params: PreviewInput("job-1", "LIP-VELVET-220", 1, "", b"face", True, 1, None, params)

    def test_makeup_sends_one_lip_shade_and_costs_one_unit(self):
        from secondlook.youcam_provider import YouCamMakeupProvider
        fake = FakeYouCamTransport(units_per_task=1, polls=[task_status("success", url=f"{STORAGE}/result/m.jpg")])
        provider = YouCamMakeupProvider("test-only-key", base_url=API, transport=fake)
        started = provider.start(self.input(category="lip", color="#a04a55", finish="cream", intensity=80))
        self.assertEqual(started.reserved_units, 1)
        path, payload = fake.tasks[0]
        self.assertEqual(path, "/s2s/v2.0/task/makeup-vto")
        # The OpenAPI spec requires a shape and a style on lip colour; the first live probe was refused without them.
        self.assertEqual(payload["effects"], [{"category": "lip_color", "shape": {"name": "original"},
                                               "style": {"type": "full"}, "palettes": [
            {"color": "#a04a55", "colorIntensity": 80, "texture": "satin"}]}])
        self.assertEqual(payload["version"], "1.0")
        self.assertEqual(len(fake.uploads), 1)
        result = provider.poll(started.provider_task_id)
        self.assertEqual((result.state, result.observed_units), ("succeeded", 1))
        self.assertEqual(fake.history[-1]["action"], "api_makeup_vto")
        glossy = YouCamMakeupProvider("test-only-key", base_url=API, transport=fake)
        glossy.start(self.input(category="lip", color="#a04a55", finish="gloss"))
        palette = fake.tasks[-1][1]["effects"][0]["palettes"][0]
        self.assertEqual((palette["texture"], palette["gloss"], palette["transparencyIntensity"]), ("gloss", 60, 30))

    def test_foundation_carries_its_three_settings_and_a_list_shaped_result_is_read(self):
        from secondlook.youcam_provider import YouCamMakeupProvider
        listed = reply(200, {"status": 200, "data": {"task_status": "success",
                                                     "results": [{"download_url": f"{STORAGE}/result/f.jpg"}]}})
        fake = FakeYouCamTransport(polls=[listed])
        provider = YouCamMakeupProvider("test-only-key", base_url=API, transport=fake)
        started = provider.start(self.input(category="foundation", color="#dcb2a3"))
        palette = fake.tasks[0][1]["effects"][0]["palettes"][0]
        self.assertEqual(palette, {"color": "#dcb2a3", "colorIntensity": 70, "coverageIntensity": 50,
                                   "glowIntensity": 30})
        self.assertEqual(provider.poll(started.provider_task_id).state, "succeeded")

    def test_nail_polish_colours_every_finger_with_a_supported_texture(self):
        from secondlook.youcam_provider import YouCamNailProvider
        fake = FakeYouCamTransport(units_per_task=1)
        provider = YouCamNailProvider("test-only-key", base_url=API, transport=fake)
        provider.start(self.input(category="nail", color="#ef6f5a", finish="gloss"))
        path, payload = fake.tasks[0]
        self.assertEqual(path, "/s2s/v2.0/task/nail-vto")
        self.assertEqual([e["finger"] for e in payload["effects"]], ["thumb", "index", "middle", "ring", "pinky"])
        self.assertEqual({(e["sub_type"], e["color"], e["texture"]) for e in payload["effects"]},
                         {("color", "#ef6f5a", "cream")})
        # Required by the OpenAPI spec; the first live probe was refused without them.
        self.assertEqual(payload["effect_type"], "nail_polish")
        for effect in payload["effects"]:
            self.assertTrue({"reflection", "contrast", "roughness", "transparency"} <= set(effect))
        metallic = YouCamNailProvider("test-only-key", base_url=API, transport=fake)
        metallic.start(self.input(category="nail", color="#2b3a67", finish="metallic"))
        self.assertNotIn("transparency", fake.tasks[-1][1]["effects"][0])

    def test_earrings_upload_the_product_photo_and_send_its_length_as_the_size(self):
        from secondlook.providers import PreviewInput, ProviderRejected
        from secondlook.youcam_provider import YouCamEarringProvider
        fake = FakeYouCamTransport(units_per_task=1, polls=[task_status("success", url=f"{STORAGE}/result/e.jpg")])
        provider = YouCamEarringProvider("test-only-key", base_url=API, transport=fake)
        job = PreviewInput("job-1", "EAR-CRYSTAL-S38", 1, "", b"face", True, 1, b"earring",
                           {"category": "earrings", "length_mm": 38, "scale_mm": 24, "anchor": [158, 0]})
        started = provider.start(job)
        path, payload = fake.tasks[0]
        self.assertEqual(path, "/s2s/v2.0/task/2d-vto/earring")
        self.assertEqual(len(fake.uploads), 2)
        src, ref = payload["src_file_id"], payload["ref_file_ids"][0]
        # The spec names both files again. The size in centimetres is the part below the hook, and the anchor is
        # where the hook meets the ear.
        self.assertEqual(payload["source_info"], {"name": src})
        self.assertEqual(payload["object_infos"], [{"name": ref, "parameter": {
            "earring_need_remove_background": True, "earring_scale": 2.4, "earring_anchor_point": [158, 0]}}])
        self.assertEqual((started.reserved_units, provider.poll(started.provider_task_id).state), (1, "succeeded"))
        with self.assertRaises(ProviderRejected) as missing:
            provider.start(PreviewInput("job-2", "EAR-X", 1, "", b"face", True, 1, None, {"length_mm": 10}))
        self.assertEqual(missing.exception.code, "PRODUCT_IMAGE_REQUIRED")

    def test_hair_colour_sends_one_full_shade_for_one_unit(self):
        from secondlook.providers import PreviewInput, ProviderRejected
        from secondlook.youcam_provider import HAIR_COLOR_PATH, YouCamHairColorProvider
        fake = FakeYouCamTransport(units_per_task=1, polls=[task_status("success", url=f"{STORAGE}/result/h.jpg")])
        provider = YouCamHairColorProvider("test-only-key", base_url=API, transport=fake)
        job = PreviewInput("job-1", "HAIR-TONE-7.1", 1, "", b"person", True, 1, None,
                           {"category": "hair", "color": "#a4927a", "finish": "permanent"})
        started = provider.start(job)
        path, payload = fake.tasks[0]
        self.assertEqual(path, HAIR_COLOR_PATH)
        self.assertEqual(len(fake.uploads), 1)
        self.assertEqual(payload, {"src_file_id": payload["src_file_id"], "pattern": {"name": "full"},
                                   "palettes": [{"color": "#a4927a", "color_intensity": 80, "shine_intensity": 30}]})
        self.assertEqual((started.reserved_units, provider.poll(started.provider_task_id).state), (1, "succeeded"))
        with self.assertRaises(ProviderRejected) as missing:
            provider.start(PreviewInput("job-2", "HAIR-X", 1, "", b"person", True, 1, None, {"category": "hair"}))
        self.assertEqual(missing.exception.code, "SHADE_REQUIRED")

    def test_necklace_sends_the_product_photo_with_background_removal_for_one_unit(self):
        from secondlook.providers import PreviewInput
        from secondlook.youcam_provider import NECKLACE_PATH, YouCamNecklaceProvider
        fake = FakeYouCamTransport(units_per_task=1, polls=[task_status("success", url=f"{STORAGE}/result/n.jpg")])
        provider = YouCamNecklaceProvider("test-only-key", base_url=API, transport=fake)
        job = PreviewInput("job-1", "NECK-LEAF-G40", 1, "", b"person", True, 1, b"necklace",
                           {"category": "necklace", "length_mm": 400})
        started = provider.start(job)
        path, payload = fake.tasks[0]
        self.assertEqual(path, NECKLACE_PATH)
        src, ref = payload["src_file_id"], payload["ref_file_ids"][0]
        self.assertEqual(payload["source_info"], {"name": src})
        self.assertEqual(payload["object_infos"], [{"name": ref, "parameter": {"necklace_need_remove_background": True}}])
        self.assertEqual((started.reserved_units, provider.poll(started.provider_task_id).state), (1, "succeeded"))

    def test_a_missing_or_malformed_shade_is_refused_before_any_upload(self):
        from secondlook.providers import ProviderRejected
        from secondlook.youcam_provider import YouCamMakeupProvider, YouCamNailProvider
        for provider_cls, params, code in ((YouCamMakeupProvider, {"category": "lip", "color": "red"}, "SHADE_REQUIRED"),
                                           (YouCamMakeupProvider, {"category": "nail", "color": "#ef6f5a"}, "UNSUPPORTED_CATEGORY"),
                                           (YouCamMakeupProvider, {"category": "blush", "color": "#e8a3b0"}, "UNSUPPORTED_CATEGORY"),
                                           (YouCamNailProvider, {}, "SHADE_REQUIRED")):
            fake = FakeYouCamTransport()
            with self.subTest(code=code), self.assertRaises(ProviderRejected) as refused:
                provider_cls("test-only-key", base_url=API, transport=fake).start(self.input(**params))
            self.assertEqual(refused.exception.code, code)
            self.assertEqual(fake.calls, [])
