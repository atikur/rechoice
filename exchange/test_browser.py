"""End-to-end tests in a real browser, with an axe-core accessibility check at every step.

They walk each shopper path, the staff question and answer, and a judge's own-photo preview, using only the
mock provider and the recorded results: no YouCam call and no units. They need requirements-dev.txt and
Google Chrome (or Playwright's Chromium, with E2E_BROWSER_CHANNEL=chromium), and are skipped without them.
Leave them out of a quick run with --exclude-tag browser.
"""
import io
import os
import re
import shutil
import tempfile
import unittest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.core.management import call_command
from django.core.servers.basehttp import WSGIServer
from django.test import override_settings, tag
from django.test.testcases import LiveServerThread, QuietWSGIRequestHandler
from PIL import Image
from unittest.mock import patch

from exchange.models import ExchangeAICall, ExchangeRequest, ExchangeSession, PreviewJob, PrivateAsset, Quote

try:
    from axe_playwright_python.sync_playwright import Axe
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:
    sync_playwright = None

# WCAG 2.2 at levels A and AA, plus axe's best practices.
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]
JUDGE_CODE = "e2e-judge-only"
TOP = "Midnight Floral Blouse"
TOPS = "Compare what meets your limits."
SHADES = "Compare shades that change the way you asked."
EARRINGS = "Compare earrings that change the way you asked."
# The accessible name of the focused element, near enough for the controls on these pages.
FOCUSED = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const name = el.getAttribute('aria-label') || (el.labels && el.labels[0] ? el.labels[0].textContent : el.textContent);
  return {name: name.trim(), outline: getComputedStyle(el).outlineStyle};
}"""


class OneRequestAtATime(LiveServerThread):
    """The test database is SQLite in memory, and a threaded server's threads would share its one connection,
    so a page's concurrent image loads failed at random. The dev server, with a connection per thread, served
    the same loads without error, so here requests are simply served one at a time."""

    def _create_server(self, connections_override=None):
        return WSGIServer((self.host, self.port), QuietWSGIRequestHandler, allow_reuse_address=False)


def photo():
    out = io.BytesIO()
    Image.new("RGB", (600, 800), (180, 160, 150)).save(out, format="JPEG")
    return out.getvalue()


@tag("browser")
class BrowserTests(StaticLiveServerTestCase):
    server_thread_class = OneRequestAtATime

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("Install requirements-dev.txt to run the browser tests.")
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel=os.environ.get("E2E_BROWSER_CHANNEL", "chrome"))
        except PlaywrightError as error:
            cls.playwright.stop()
            raise unittest.SkipTest(f"No browser for the browser tests: {error.message.splitlines()[0]}")
        cls.media = tempfile.mkdtemp(prefix="secondlook-e2e-")
        cls.overrides = override_settings(PREVIEW_PROVIDER="mock", JUDGE_ACCESS_CODE=JUDGE_CODE, PRIVATE_MEDIA_ROOT=cls.media,
                                          PREVIEW_POLL_SECONDS=0, ALLOW_PHOTO_UPLOAD=False, PUBLIC_LIVE_PREVIEWS=False)
        cls.overrides.enable()
        # Playwright's synchronous API keeps an event loop on this thread; the ORM calls here are still synchronous.
        cls.async_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.axe = Axe()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = cls.async_unsafe
        cls.overrides.disable()
        shutil.rmtree(cls.media, ignore_errors=True)
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        cache.clear()
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())
        self.errors = []
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        self.page = self.new_page()

    def new_page(self):
        page = self.context.new_page()
        # Failed requests are logged with their address; the console's own note about them would not say which.
        page.on("response", lambda response: response.status >= 400 and self.errors.append(f"{response.status} {response.url}"))
        page.on("console", lambda message: message.type == "error" and not message.text.startswith("Failed to load resource")
                and self.errors.append(message.text))
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        return page

    def go(self, path, page=None):
        (page or self.page).goto(self.live_server_url + path)

    def check(self, where, page=None):
        """No accessibility problem axe can find, no script error and no sideways scrolling at this step."""
        page = page or self.page
        results = self.axe.run(page, options={"runOnly": {"type": "tag", "values": AXE_TAGS}})
        problems = [f"{v['id']}: " + ", ".join(" ".join(node["target"]) for node in v["nodes"][:3])
                    for v in results.response["violations"]]
        self.assertEqual(problems, [], f"Accessibility problems at {where}")
        self.assertEqual(self.errors, [], f"Script errors at {where}")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"), 0,
                             f"The page scrolls sideways at {where}")

    def assert_step(self, heading):
        """The new step has the focus, so keyboard and screen reader users land where the page went."""
        expect(self.page.locator(":focus")).to_have_text(heading)

    def button(self, name):
        return self.page.get_by_role("button", name=name, exact=True)

    def grant_judge(self):
        self.go("/judge")
        self.page.get_by_label("Access code").fill(JUDGE_CODE)
        self.button("Continue").click()
        expect(self.page.get_by_text("Judge access is on for this browser.")).to_be_visible()

    def open_item(self, title, reason):
        self.go("/")
        self.button("Open demo order").click()
        self.assert_step("Which item is going back?")
        self.button(f"Return this item: {title}").click()
        self.assert_step("Tell us what went wrong.")
        self.page.get_by_label(reason, exact=True).check()
        self.button("Continue").click()

    def request_previews(self, person, heading, offered):
        self.button("Preview on a person").click()
        self.assert_step("Choose who the preview shows.")
        expect(self.page.locator(".demo-option:visible")).to_have_count(offered)
        self.check(f"choosing {person}")
        self.page.locator(".demo-option:visible", has_text=person).click()
        self.page.get_by_label("I understand how the photo is used and want the previews.").check()
        self.button("Request previews").click()
        self.assert_step(heading)

    def tab_to(self, name):
        """Press Tab until the control called `name` has the focus; every stop on the way shows a focus outline."""
        for _ in range(60):
            self.page.keyboard.press("Tab")
            focused = self.page.evaluate(FOCUSED)
            self.assertIsNotNone(focused, "Tab moved the focus off the page's controls")
            self.assertNotEqual(focused["outline"], "none", f"No visible focus on {focused['name']!r}")
            if re.fullmatch(name, focused["name"]):
                return
        self.fail(f"Tab never reached {name!r}")

    def test_tradeoff_review_requires_confirmation_and_keeps_other_confirmed_limits(self):
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#budget").fill("0")
        self.button("Confirm preferences").click()
        expect(self.page.locator("#noMatch")).to_be_visible()
        self.page.get_by_label("Which limit would you revisit?").select_option("budget")
        self.button("Explore this limit").click()
        expect(self.page.locator(".tradeoff-card h4")).to_have_text("Maximum extra spend: USD 0.00 → USD 2.00")
        expect(self.page.locator(".tradeoff-card")).to_contain_text("Keeps: Sleeves: long")
        self.check("the single-limit possibility")
        session = ExchangeSession.objects.get()
        self.assertEqual(session.preferences["max_top_up_minor"], 0)
        version = session.preference_version
        self.assertEqual((Quote.objects.count(), PreviewJob.objects.count()), (0, 0))
        # Unconfirmed edits must not leak into a proposal to change only the budget.
        self.page.locator("#sleeve").select_option("short")
        self.page.locator("#quieter").uncheck()
        self.button("Review this change").click()
        expect(self.page.locator("#budget")).to_have_value("2.00")
        expect(self.page.locator("#sleeve")).to_have_value("long")
        expect(self.page.locator("#quieter")).to_be_checked()
        expect(self.page.locator("#tradeoffReview")).to_contain_text("confirmed limits still apply")
        session.refresh_from_db()
        self.assertEqual((session.preference_version, session.preferences["max_top_up_minor"]), (version, 0))
        self.check("the unconfirmed proposal")
        self.button("Confirm preferences").click()
        self.assert_step(TOPS)
        expect(self.page.locator("#cards .card")).to_have_count(1)
        expect(self.page.locator("#cards .card")).to_have_attribute("data-sku", "CALM-07")
        session.refresh_from_db()
        self.assertEqual((session.preference_version, session.preferences["max_top_up_minor"]), (version + 1, 200))
        self.assertEqual((Quote.objects.count(), PreviewJob.objects.count()), (0, 0))
        self.request_previews("Model A", TOPS, offered=3)
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 2)
        self.check("the reconfirmed single alternative and purchased-item previews")

    def test_tradeoff_empty_and_sleeve_paths_work_on_a_narrow_screen(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#budget").fill("1.00")
        self.button("Confirm preferences").click()
        self.page.get_by_label("Which limit would you revisit?").select_option("print")
        self.button("Explore this limit").click()
        expect(self.page.locator(".tradeoff-explorer")).to_contain_text("Changing only this limit does not unlock an eligible top.")
        expect(self.page.locator(".tradeoff-card")).to_have_count(0)
        self.check("no single print change on mobile")
        self.page.get_by_label("Which limit would you revisit?").select_option("sleeves")
        self.button("Explore this limit").click()
        expect(self.page.locator(".tradeoff-card h4")).to_have_text("Sleeves: long → short")
        expect(self.page.locator(".tradeoff-card")).to_contain_text("Keeps: Maximum extra spend: USD 1.00")
        self.check("a sleeve possibility on mobile")
        self.button("Review this change").click()
        self.button("Confirm preferences").click()
        self.assert_step(TOPS)
        expect(self.page.locator("#cards .card")).to_have_attribute("data-sku", "SHORT-04")
        self.check("the confirmed sleeve change on mobile")

    def test_copilot_waits_for_slow_preference_confirmation(self):
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#budget").fill("0")
        pending, calls = [], []

        def hold_confirmation(route):
            pending.append((route, route.fetch()))

        self.page.route("**/preferences", hold_confirmation)
        self.page.on("request", lambda request: calls.append(request.url) if request.url.endswith("/copilot") else None)
        self.button("Confirm preferences").click()
        expect(self.page.locator("#confirmBtn")).to_be_disabled()
        expect(self.page.locator("#copilotBtn")).to_be_disabled()
        # Even a programmatic click must not plan against the previous confirmed version.
        self.page.locator("#copilotBtn").dispatch_event("click")
        self.assertEqual(calls, [])
        self.assertEqual(len(pending), 1)
        route, response = pending.pop()
        route.fulfill(response=response)
        expect(self.page.locator("#copilotBtn")).to_be_enabled()
        self.page.locator("#copilotBtn").click()
        expect(self.page.locator("#copilotResult")).to_be_visible()
        self.page.locator("#copilotResult").get_by_role("button", name="Maximum extra spend", exact=True).click()
        expect(self.page.locator("#copilotResult")).to_contain_text("Your saved limits are unchanged")
        self.check("the guide after a delayed preference confirmation")

    def test_copilot_clarifies_then_explores_without_confirming_and_labels_unassessed_images(self):
        from exchange.test_exchange_ai import unpublished_reviews
        evidence = unpublished_reviews()
        evidence.start()
        self.addCleanup(evidence.stop)
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#note").fill("Long sleeves and short sleeves. At most $2 extra.")
        self.page.locator("#copilotBtn").click()
        self.assert_step("A plan from the current facts.")
        expect(self.page.locator("#copilotResult")).to_contain_text("Clarify the request")
        self.assertEqual(ExchangeSession.objects.get().preferences, {})
        self.check("the copilot's clarification")
        self.button("Back to controls").click()
        self.page.locator("#budget").fill("0")
        self.button("Confirm preferences").click()
        self.page.locator("#copilotBtn").click()
        self.page.locator("#copilotResult").get_by_role("button", name="Maximum extra spend", exact=True).click()
        expect(self.page.locator("#copilotResult")).to_contain_text("Your saved limits are unchanged")
        self.assertEqual(ExchangeSession.objects.get().preferences["max_top_up_minor"], 0)
        self.button("Show this trade-off").click()
        expect(self.page.locator(".tradeoff-card h4")).to_have_text("Maximum extra spend: USD 0.00 → USD 2.00")
        self.button("Review this change").click()
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        self.page.get_by_role("button", name="Review requested changes: Ribbed Turtleneck", exact=True).click()
        expect(self.page.locator(".visual-review")).to_contain_text("No AI requested-change assessment")
        expect(self.page.locator(".visual-review")).to_contain_text("Paid visual review is off")
        self.check("an explicitly unassessed requested change")
        self.go("/evaluation/agents")
        expect(self.page.get_by_role("heading", name="Deterministic contracts", exact=True)).to_be_visible()
        self.check("the separate agent report on mobile")

    @override_settings(EXCHANGE_AI_PROVIDER="claude", EXCHANGE_AI_JUDGE_ONLY=False,
                       EXCHANGE_AI_APPROVAL="fake browser-test approval", EXCHANGE_AI_MAX_USD="2",
                       EXCHANGE_AI_DAILY_CALLS=20, EXCHANGE_AI_SESSION_CALLS=12, ANTHROPIC_API_KEY="fake")
    def test_ai_visual_concern_is_labelled_as_an_assessment_in_the_browser(self):
        from exchange.test_exchange_ai import client_for, text_review, unpublished_reviews
        evidence = unpublished_reviews()
        evidence.start()
        self.addCleanup(evidence.stop)
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#budget").fill("2")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        client = client_for(text_review("concern"))
        with patch("exchange.ai_client.make_client", return_value=client):
            self.page.get_by_role("button", name="Review requested changes: Ribbed Turtleneck", exact=True).click()
            expect(self.page.locator(".visual-review")).to_contain_text("independent human review pending")
            expect(self.page.locator(".visual-review")).to_contain_text("sleeves — concern")
        self.assertEqual(client.messages.create.call_count, 1)
        self.check("a model assessment with human verification still pending")

    @override_settings(ASSISTANT_PROVIDER="rules")
    def test_switching_recorded_people_discards_old_warnings_and_late_comparison(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model B", TOPS, offered=3)
        sage = self.page.locator('#cards .card[data-sku="CALM-04"]')
        expect(sage.locator(".gate-badge")).to_have_text("Flagged: colour")
        old_warning = PreviewJob.objects.get(variant__sku="CALM-04").gate_note
        self.button("Help me compare").click()
        expect(self.page.locator("#advice")).to_contain_text(old_warning)
        sage.get_by_role("button", name="Choose this top: Sage Knit Top", exact=True).click()
        expect(sage.locator(".gate-choice")).to_contain_text(old_warning)
        session = ExchangeSession.objects.get()
        self.assertEqual(self.errors, [f"422 {self.live_server_url}/api/sessions/{session.pk}/quotes"])
        self.errors.clear()  # This refusal is the intended explicit-choice gate.

        pending = []

        def hold_comparison(route):
            pending.append((route, route.fetch()))

        # The server really compares B, but its reply reaches the browser only after A is shown.
        self.page.route("**/api/sessions/*/compare", hold_comparison)
        self.button("Help me compare").click()
        expect(self.button("Help me compare")).to_be_disabled()
        for _ in range(100):
            if pending:
                break
            self.page.wait_for_timeout(20)
        self.assertEqual(len(pending), 1)
        self.request_previews("Model A", TOPS, offered=3)
        expect(sage.locator(".gate-badge")).to_have_text("Checks agree")
        expect(sage.locator(".gate-choice")).to_have_count(0)
        expect(self.page.locator("#advice")).to_be_hidden()
        route, response = pending.pop()
        route.fulfill(response=response)
        expect(self.button("Help me compare")).to_be_enabled()
        expect(self.page.locator("#advice")).to_be_hidden()

        # The visible A preview passes; B's earlier concern must not hold this quote or draft.
        sage.get_by_role("button", name="Choose this top: Sage Knit Top", exact=True).click()
        self.assert_step("Review the current quote.")
        self.assertEqual(Quote.objects.get().preview_concern_ack, "")
        self.button("Create exchange request draft").click()
        self.assert_step("Your exchange request is with staff.")
        self.check("the current photo's passing preview through the saved request")

    def test_a_late_preview_warning_reopens_the_visible_choice_before_saving(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        sage = self.page.locator('#cards .card[data-sku="CALM-04"]')
        sage.get_by_role("button", name="Choose this top: Sage Knit Top", exact=True).click()
        self.assert_step("Review the current quote.")
        # A worker can finish its checks after the shopper has already opened a quote.
        warning = "The preview's colour differs from the product photo."
        PreviewJob.objects.filter(variant__sku="CALM-04").update(gate_status="concern", gate_note=warning)
        self.button("Create exchange request draft").click()
        expect(sage.locator(".gate-choice")).to_be_visible()
        expect(sage.locator(".gate-choice")).to_contain_text(warning)
        self.assertEqual(ExchangeRequest.objects.count(), 0)
        session = ExchangeSession.objects.get()
        self.assertEqual(self.errors, [f"409 {self.live_server_url}/api/requests",
                                       f"422 {self.live_server_url}/api/sessions/{session.pk}/quotes"])
        self.errors.clear()  # Both refusals protect the newly flagged preview.
        self.check("the late warning returned to the visible comparison")
        sage.get_by_role("button", name="Choose it anyway: Sage Knit Top", exact=True).click()
        self.assert_step("Review the current quote.")
        self.button("Create exchange request draft").click()
        self.assert_step("Your exchange request is with staff.")
        self.assertTrue(ExchangeRequest.objects.get().quote.preview_concern_ack)
        self.check("the late warning acknowledged before the request is saved")

    def test_a_top_from_the_shoppers_words_through_recorded_previews_to_a_staff_question_and_answer(self):
        self.grant_judge()
        self.go("/")
        self.check("the start")
        self.open_item(TOP, "I don't like how it looks")
        self.assert_step("What would make it right?")
        prefs = self.page.locator("#prefs")
        prefs.get_by_label("In your own words, optional").fill("The print is too busy. Keep long sleeves. At most $10 extra.")
        prefs.get_by_role("button", name="Fill in from my words").click()
        expect(prefs.get_by_role("status").first).to_contain_text("We understood")
        expect(prefs.get_by_label("Maximum extra spend in USD")).to_have_value("10.00")
        self.check("the preferences filled in from the shopper's words")
        self.button("Confirm preferences").click()
        self.assert_step(TOPS)
        expect(self.page.locator("#cards .card")).to_have_count(2)
        self.check("the eligible tops")

        self.request_previews("Model A", TOPS, offered=3)
        expect(self.page.locator("#notice")).to_have_text("Recorded results shown. No new AI run and no units used.")
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
        expect(self.page.locator(".visual-preview img").first).to_have_attribute("alt", re.compile(r"^AI preview of .+ on Model A$"))
        self.check("the recorded previews")
        self.button("Help me compare").click()
        expect(self.page.locator("#advice")).to_contain_text("Put together from the catalog facts and what you asked for.")
        expect(self.page.locator("#advice li")).to_have_count(2)
        self.page.get_by_role("button", name=re.compile("^See it with the rest of my order: ")).first.click()
        self.assert_step("See it with what you're keeping.")
        looks = self.page.locator("#lookCards .look-card")
        expect(looks.get_by_role("heading")).to_have_text(["What you have now", "With Ribbed Turtleneck"])
        expect(looks.locator("img")).to_have_count(2)
        expect(looks.locator(".status-pill")).to_have_text(["Ready", "Ready"])
        expect(looks.first.locator(".preview-label")).to_contain_text("28 October 2026 looks pilot")
        self.check("the recorded looks")
        self.button("Back to comparison").click()
        self.assert_step(TOPS)
        self.page.get_by_role("button", name=re.compile("^Choose this top: ")).first.click()
        self.assert_step("Review the current quote.")
        self.check("the quote")
        self.button("Create exchange request draft").click()
        self.assert_step("Your exchange request is with staff.")
        expect(self.page.locator("#requestState")).not_to_be_empty()
        self.check("the saved request")

        staff = self.new_page()
        self.go("/staff/", staff)
        form = staff.locator("form.decision")
        form.get_by_label("Decision").select_option("NEEDS_CLARIFICATION")
        # A suggested question fills the message from the request's facts, for staff to edit.
        form.get_by_role("button", name="Suggest a message").click()
        expect(form.locator(".suggest-note")).to_contain_text("Edit it before you send it.")
        expect(form.get_by_label(re.compile("^Message to the shopper"))).to_have_value(re.compile("^Before we confirm "))
        form.get_by_label(re.compile("^Message to the shopper")).fill("Would the navy one suit you too?")
        form.get_by_role("button", name="Record decision").click()
        expect(form.locator(".result")).to_contain_text("Recorded.")
        self.check("the staff queue", staff)
        staff.close()

        self.button("Check for updates").click()
        expect(self.page.locator("#staffMessage")).to_have_text("Would the navy one suit you too?")
        self.page.get_by_label("Your answer").fill("Yes, navy is fine.")
        self.button("Send answer").click()
        expect(self.page.locator("#staffMessage")).to_contain_text("Your answer: Yes, navy is fine.")
        self.check("the answered question")
        self.assertEqual(ExchangeRequest.objects.get().shopper_reply, "Yes, navy is fine.")

    def test_lipstick_nail_polish_and_earrings_each_preview_on_the_right_demo_person(self):
        shade = ("The shade looks wrong", "Say how the shade should change.", "It looks too orange on me", "Find shades", SHADES)
        for title, (reason, step, words, find, heading), person, offered in (
                ("Velvet Matte Lipstick, 230 Terracotta", shade, "Face A", 3),
                ("Gloss Lacquer Nail Polish, 01 Coral Crush", shade, "Hand A", 1),
                ("Baroque Pearl Drops", ("I don't like how they look on me", "Say what should change.",
                                         "These are too long and I wanted silver", "Find earrings", EARRINGS), "Face A", 3)):
            with self.subTest(title):
                self.open_item(title, reason)
                self.assert_step(step)
                prefs = self.page.locator("section.panel:visible", has=self.page.get_by_role("button", name=find))
                prefs.get_by_label("In your own words, optional").fill(words)
                prefs.get_by_role("button", name="Fill in from my words").click()
                expect(prefs.get_by_role("status").first).to_contain_text("We understood")
                self.check(f"the {title} preferences")
                self.button(find).click()
                self.assert_step(heading)
                expect(self.page.locator("#cards .card")).to_have_count(2)
                self.check(f"the {title} choices")
                self.request_previews(person, heading, offered)
                expect(self.page.locator("#notice")).to_have_text("Recorded results shown. No new AI run and no units used.")
                expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
                self.check(f"the {title} previews")
        # The crystal drop, drawn half as long again as its length, is shown resized; YouCam's render is a click away.
        drawn = self.page.get_by_role("button", name="Show it as YouCam drew it")
        expect(drawn).to_have_count(1)
        card = self.page.locator("#cards .card", has=drawn)
        expect(card).to_contain_text("Blue Crystal Drops")
        expect(card.locator(".colour-check")).to_contain_text("Rechoice resized it to its catalog length")
        image = card.locator(".visual-preview img")
        resized = image.get_attribute("src")
        drawn.click()
        expect(drawn).to_have_attribute("aria-pressed", "true")
        expect(image).not_to_have_attribute("src", resized)
        drawn.click()
        expect(image).to_have_attribute("src", resized)
        self.check("the resized earring")

    def test_the_keyboard_alone_reaches_a_saved_request_and_the_focus_is_never_lost(self):
        self.go("/")
        self.tab_to("Open demo order")
        self.page.keyboard.press("Enter")
        self.assert_step("Which item is going back?")
        self.tab_to(f"Return this item: {TOP}")
        self.page.keyboard.press("Enter")
        self.assert_step("Tell us what went wrong.")
        self.tab_to("I don't like how it looks")
        self.page.keyboard.press("Space")
        self.tab_to("Continue")
        self.page.keyboard.press("Enter")
        self.assert_step("What would make it right?")
        self.tab_to("Confirm preferences")
        self.page.keyboard.press("Enter")
        self.assert_step(TOPS)
        self.tab_to("Choose this top: .+")
        self.page.keyboard.press("Enter")
        self.assert_step("Review the current quote.")
        self.tab_to("Create exchange request draft")
        self.page.keyboard.press("Enter")
        self.assert_step("Your exchange request is with staff.")
        self.assertEqual(ExchangeRequest.objects.count(), 1)

    def test_other_sizes_the_support_route_no_match_and_the_normal_return(self):
        self.open_item(TOP, "It doesn't fit")
        self.assert_step("Try a different size of the same top.")
        expect(self.page.locator("#sizesMessage")).to_contain_text("You have size")
        self.check("other sizes")

        self.open_item(TOP, "It arrived damaged or it is the wrong item")
        self.assert_step("This needs the retailer's support team.")
        self.check("the support route")
        self.button("See the normal return route").click()
        self.assert_step("Normal return route")
        self.button("End here and use the normal return").click()
        expect(self.page.locator("#returnDone")).not_to_be_empty()
        # The button pressed is gone, so the focus moves to the route rather than to nothing.
        self.assert_step("Normal return route")
        self.check("the ended return")

        self.open_item(TOP, "I don't like how it looks")
        self.page.locator("#prefs").get_by_label("Maximum extra spend in USD").fill("0")
        self.button("Confirm preferences").click()
        no_match = self.page.locator("#noMatch")
        expect(no_match).to_contain_text("No top meets every limit you confirmed. Nothing was relaxed.")
        expect(no_match.get_by_role("listitem").first).to_be_visible()
        self.check("no eligible top")

    def test_judge_access_is_in_the_header_and_explains_itself_on_desktop_and_mobile(self):
        for width in (1280, 320):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 720})
                self.go("/")
                button = self.button("Judge access")
                expect(button).to_be_visible()
                box = button.bounding_box()
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)
                self.assertLess(box["y"] + box["height"], 140, "Judge access should be above the hero")
                self.check(f"judge entry at {width}px")
                button.click()
                dialog = self.page.get_by_role("dialog", name="Judge access", exact=True)
                expect(dialog).to_be_visible()
                expect(self.page.locator(":focus")).to_have_text("Judge access")
                for instruction in ("live YouCam try-ons", "your own photo", "AI planning", "staff tools",
                                    "Devpost’s private judging instructions", "Unlocking uses no credits"):
                    expect(dialog).to_contain_text(instruction)
                self.check(f"judge instructions at {width}px")
                dialog.get_by_text("Where is my code?", exact=True).click()
                for instruction in ("Additional info", "rechoice-judges.zip", "START-HERE-PRIVATE.md"):
                    expect(dialog.get_by_text(instruction, exact=True)).to_be_visible()
                self.check(f"expanded code instructions at {width}px")
                # Native modal semantics keep focus out of the underlying order; Chrome may
                # also move it to its browser controls, represented by body as activeElement.
                for _ in range(8):
                    self.page.keyboard.press("Tab")
                    self.assertTrue(dialog.evaluate(
                        "el => document.activeElement === document.body || el.contains(document.activeElement)"))
                self.page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                expect(button).to_be_focused()
                self.button("Open demo order").click()
                self.assert_step("Which item is going back?")
                heading_box = self.page.get_by_role("heading", name="Which item is going back?").bounding_box()
                header_box = self.page.locator("header").bounding_box()
                self.assertGreaterEqual(heading_box["y"], header_box["y"] + header_box["height"],
                                        "The sticky judge header must not cover a newly focused step")
        self.assertEqual((PreviewJob.objects.count(), ExchangeAICall.objects.count()), (0, 0))

    @override_settings(PREVIEW_PROVIDER="judge-ui-test")
    def test_judge_popup_unlock_keeps_the_open_order_and_does_not_start_a_paid_preview(self):
        # A non-mock name exercises the live-access label/gate. It is deliberately unregistered,
        # so a regression cannot contact a real provider while this test unlocks the browser.
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.button("Preview on a person").click()
        session = ExchangeSession.objects.get()
        original = (session.pk, session.browser_session_key, session.preference_version, session.preferences)
        original_url = self.page.url
        expect(self.page.locator("#liveOption")).to_be_hidden()
        expect(self.page.locator("#uploadOption")).to_be_hidden()
        self.button("Judge access").click()
        self.page.locator("#judgeAccessCode").fill("incorrect-code")
        self.page.locator("#judgeAccessSubmit").click()
        expect(self.page.get_by_role("alert")).to_have_text("That code is not right.")
        expect(self.page.locator("#judgeAccessCode")).to_be_focused()
        self.assertEqual(self.errors, [f"403 {self.live_server_url}/api/judge-access"])
        self.errors.clear()  # This rejected credential is the only expected HTTP error.
        session.refresh_from_db()
        self.assertFalse(session.judge)
        self.check("the rejected judge code")
        self.page.locator("#judgeAccessCode").fill(JUDGE_CODE)
        self.page.locator("#judgeAccessCode").press("Enter")
        expect(self.page.locator("#judgeAccessSuccessTitle")).to_be_focused()
        expect(self.page.locator("#judgeAccessForm")).to_be_hidden()
        expect(self.page.locator("#judgeAccessCode")).to_have_value("")
        self.check("the successful judge unlock")
        self.button("Continue the demo").click()
        expect(self.button("Judge access active")).to_be_focused()
        expect(self.page.locator("#photo")).to_be_visible()
        expect(self.page.locator("#liveOption")).to_be_visible()
        expect(self.page.locator("#uploadOption")).to_be_visible()
        expect(self.page.locator("#judgeUnlock")).to_be_hidden()
        expect(self.page.locator('input[name="previewMode"][value="recorded"]')).to_be_checked()
        self.assertEqual(self.page.url, original_url)
        session.refresh_from_db()
        self.assertEqual(ExchangeSession.objects.count(), 1)
        self.assertEqual((session.pk, session.preference_version, session.preferences),
                         (original[0], original[2], original[3]))
        self.assertNotEqual(session.browser_session_key, original[1])
        self.assertTrue(session.judge)
        self.assertEqual((PreviewJob.objects.count(), ExchangeAICall.objects.count()), (0, 0))
        # The session-key rotation must leave the existing order usable, not only visible.
        self.button("Back to comparison").click()
        self.page.get_by_role("button", name=re.compile("^Choose this top: ")).first.click()
        self.assert_step("Review the current quote.")
        self.assertEqual(Quote.objects.get().owner_session_id, session.pk)
        self.go("/")
        self.button("Judge access active").click()
        expect(self.page.locator("#judgeAccessSuccessTitle")).to_be_focused()
        expect(self.page.locator("#judgeAccessForm")).to_be_hidden()
        expect(self.page.get_by_role("link", name="Staff queue (new tab)")).to_be_visible()
        self.check("already granted judge access after a page load")

    def test_inline_judge_unlock_also_updates_the_header_without_changing_preview_mode(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.button("Preview on a person").click()
        self.page.locator("#judgeCode").fill(JUDGE_CODE)
        self.button("Unlock").click()
        expect(self.page.locator("#judgeUnlock")).to_be_hidden()
        expect(self.page.get_by_label("Use my own photo")).to_be_focused()
        expect(self.page.locator('input[name="previewMode"][value="recorded"]')).to_be_checked()
        self.button("Judge access active").click()
        expect(self.page.locator("#judgeAccessSuccessTitle")).to_be_focused()
        expect(self.page.locator("#judgeAccessForm")).to_be_hidden()
        self.check("the popup after inline judge unlock")
        self.assertEqual((PreviewJob.objects.count(), ExchangeAICall.objects.count()), (0, 0))

    def test_a_judge_previews_on_their_own_photo_in_test_mode_and_deletes_it(self):
        self.grant_judge()
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.assert_step(TOPS)
        self.button("Preview on a person").click()
        self.page.get_by_label("Use my own photo").check()
        expect(self.page.locator("#recordedOption")).to_be_hidden()
        expect(self.page.get_by_label("Test mode: no image and no AI call")).to_be_checked()
        self.page.get_by_label("Choose a photo").set_input_files({"name": "me.jpg", "mimeType": "image/jpeg", "buffer": photo()})
        expect(self.page.get_by_alt_text("The photo you chose")).to_be_visible()
        self.check("an own photo chosen")
        self.page.get_by_label("I am an adult, and this photo shows me").check()
        self.page.get_by_label("I understand how the photo is used and want the previews.").check()
        self.button("Request previews").click()
        self.assert_step(TOPS)
        expect(self.page.locator("#notice")).to_have_text("Test previews complete. No image was generated or sent to YouCam.")
        uploads = PrivateAsset.objects.filter(kind="person", license_or_consent="confirmed adult permission")
        self.assertEqual(uploads.count(), 1)
        self.check("test-mode previews on an own photo")

        self.button("Preview on a person").click()
        self.button("Remove this photo").click()
        expect(self.page.locator("#notice")).to_contain_text("Your photo was deleted from Rechoice.")
        self.assertFalse(uploads.exists())

    def test_claude_fills_in_the_controls_from_the_shoppers_words_when_switched_on(self):
        from unittest import mock

        from exchange import assistant
        from exchange.test_assistant import ON, FakeClaude, apparel, reply
        fake = FakeClaude(reply({**apparel(quieter_print="quieter", sleeve_required="short", colours=["navy"],
                                           budget={"stated": True, "usd": 20}),
                                 "understood": ["a quieter print", "short sleeves", "navy", "up to USD 20.00 extra"]}))
        with override_settings(**ON), mock.patch.object(assistant, "make_client", return_value=fake):
            self.open_item(TOP, "I don't like how it looks")
            prefs = self.page.locator("#prefs")
            expect(prefs).to_contain_text("Your words are read by Claude, Anthropic's AI")
            prefs.get_by_label("In your own words, optional").fill(
                "Too loud. Short sleeves, navy, up to twenty dollars.")
            prefs.get_by_role("button", name="Fill in from my words").click()
            expect(prefs.locator("#understood")).to_contain_text("Read by Claude Haiku 5.5, Anthropic's AI")
        expect(prefs.locator("#understood")).to_contain_text("We understood: a quieter print · short sleeves")
        expect(prefs.get_by_label("Sleeves")).to_have_value("short")
        expect(prefs.get_by_label("Preferred color, optional")).to_have_value("navy")
        expect(prefs.get_by_label("Maximum extra spend in USD")).to_have_value("20.00")
        self.assertEqual(len(fake.requests), 1)
        self.check("the controls filled in by Claude")

    def test_a_new_top_with_the_rest_of_the_order_in_test_mode(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.assert_step(TOPS)
        self.button("Preview on a person").click()
        self.page.locator(".demo-option:visible", has_text="Model A").click()
        self.page.get_by_label("Test mode: no image and no AI call").check()
        self.page.get_by_label("I understand how the photo is used and want the previews.").check()
        self.button("Request previews").click()
        self.assert_step(TOPS)
        self.page.get_by_role("button", name=re.compile("^See it with the rest of my order: ")).first.click()
        self.assert_step("See it with what you're keeping.")
        cards = self.page.locator("#lookCards .look-card")
        expect(cards).to_have_count(2)
        expect(cards.get_by_role("heading")).to_have_text(["What you have now", re.compile("^With ")])
        expect(cards.first.locator(".look-steps li")).to_have_text([
            re.compile("^Clothes V4: Midnight Floral Blouse · Ready"),
            re.compile("^Makeup VTO: Velvet Matte Lipstick"), re.compile("^Necklace VTO: Amethyst Y Chain")])
        expect(self.page.locator("#lookLeftOut")).to_contain_text(
            "Baroque Pearl Drops: earrings need a close photo of the ear.")
        self.check("a look in test mode")
        self.button("Back to comparison").click()
        self.assert_step(TOPS)

    def test_shoes_from_the_shoppers_words_to_a_test_preview_on_the_full_length_model(self):
        self.open_item("Red Heeled Ankle Boots", "I don't like how they look on me")
        self.assert_step("Say what should change.")
        prefs = self.page.locator("#jewelPrefs")
        expect(prefs.locator("#jewelMetalLabel")).to_have_text("Colour")
        prefs.get_by_label("In your own words, optional").fill("The heels are too high and the red is too loud.")
        prefs.get_by_role("button", name="Fill in from my words").click()
        expect(prefs.get_by_role("status").first).to_contain_text("We understood")
        expect(prefs.locator("#jewelSize")).to_have_value("smaller")
        expect(prefs.locator("#jewelMetal")).to_have_value("neutral")
        self.check("the shoe preferences")
        self.button("Find shoes").click()
        self.assert_step("Compare shoes that change the way you asked.")
        expect(self.page.locator("#cards .card")).to_have_count(1)
        expect(self.page.locator("#cards .card")).to_contain_text("Cream Block-Heel Peep-Toes")
        self.button("Preview on a person").click()
        self.assert_step("Choose who the preview shows.")
        expect(self.page.locator(".demo-option:visible")).to_have_count(1)
        expect(self.page.locator(".demo-option:visible")).to_contain_text("Model F")
        self.check("choosing Model F for shoes")

    def test_a_return_starts_on_the_stores_own_order_page(self):
        self.go("/store")
        self.check("the demo store's order page")
        self.page.get_by_role("link", name="Return or exchange an item").click()
        self.assert_step("Which item is going back?")
        expect(self.page.locator("#itemList .card")).to_have_count(9)
        self.page.set_viewport_size({"width": 320, "height": 720})
        self.go("/store")
        self.check("the demo store's order page at 320 px")

    def test_a_judges_link_opens_the_order_at_its_item_with_the_recorded_person_chosen(self):
        self.go("/judge")
        self.check("/judge")
        self.page.get_by_role("link", name="Return the lipstick").click()
        self.assert_step("Tell us what went wrong.")
        expect(self.page.locator("#orderTitle")).to_have_text("Velvet Matte Lipstick, 230 Terracotta")
        self.page.get_by_label("The shade looks wrong", exact=True).check()
        self.button("Continue").click()
        self.assert_step("Say how the shade should change.")
        self.page.locator("#tone").select_option("cooler")
        self.button("Find shades").click()
        self.button("Preview on a person").click()
        self.assert_step("Choose who the preview shows.")
        # Face A, which the recorded shades cover, is already chosen.
        expect(self.page.locator(".demo-option:visible input:checked")).to_have_count(1)
        expect(self.page.locator(".demo-option:visible", has=self.page.locator("input:checked"))).to_contain_text("Face A")
        self.page.get_by_label("I understand how the photo is used and want the previews.").check()
        self.button("Request previews").click()
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
        # The shade bought sits in the same row as the two suggestions.
        tops = self.page.locator("#choices .compare .card").evaluate_all("cards => cards.map(c => Math.round(c.getBoundingClientRect().top))")
        self.assertEqual(len(set(tops)), 1, tops)
        self.check("the lipstick comparison from a judge's link")

    def test_every_page_reads_and_reflows_at_320_pixels_wide(self):
        self.page.set_viewport_size({"width": 320, "height": 720})
        self.go("/judge")
        self.check("/judge before the code")
        self.grant_judge()
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
        self.check("the recorded previews at 320 px")
        self.page.get_by_role("button", name=re.compile("^Choose this top: ")).first.click()
        self.button("Create exchange request draft").click()
        self.assert_step("Your exchange request is with staff.")
        for path in ("/evidence", "/evaluation", "/evaluation/heldout", "/staff/", "/staff/insights"):
            self.go(path)
            self.check(f"{path} at 320 px")
        # The wide tables scroll inside their own boxes, and the keyboard can scroll them.
        self.go("/evidence")
        table = self.page.get_by_role("region", name="Beauty shades: requested and measured")
        table.focus()
        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_function("el => el.scrollLeft > 0", arg=table.element_handle())

    def test_preview_trust_separates_catalog_facts_and_checks_and_opens_the_evaluation(self):
        self.open_item(TOP, "I don't like how it looks")
        self.page.locator('#quieter').check()
        self.page.locator('#sleeve').select_option('long')
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        expect(self.page.locator('.preview-trust')).to_have_count(3)
        panel = self.page.locator('#cards .card').first.locator('.preview-trust')
        panel.locator('summary').click()
        expect(panel.get_by_role('heading', name='What you asked for')).to_be_visible()
        expect(panel).to_contain_text('A quieter print')
        expect(panel).to_contain_text('stock, policy and your confirmed controls were rechecked')
        expect(panel.get_by_role('heading', name='Checked in this image')).to_be_visible()
        expect(panel).to_contain_text('not automatically verified')
        self.check('the open preview-trust panel')
        panel.get_by_role('link', name='How we evaluate these checks').click()
        expect(self.page.get_by_role('heading', name='What can we trust in a preview?')).to_be_visible()
        expect(self.page.locator('#case-list article')).to_have_count(36)
        expect(self.page.get_by_text('36 development outcomes.', exact=True)).to_be_visible()
        self.check('the reproducible evaluation page')
        self.page.get_by_role('link', name='Separate photo holdout: 6 / 6 first-attempt renders').click()
        expect(self.page.get_by_role('heading', name='6 planned renders on 2 synthetic photos')).to_be_visible()
        self.check('the separate registered photo holdout')

    def test_queued_recorded_previews_replace_the_preparing_notice_when_polling_finishes(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()

        def queued_initial_response(route):
            response = route.fetch()
            data = response.json()
            # Real completed jobs remain on the server; only the initial response models the queue.
            for job in data["jobs"]:
                job.update(state="pending", image_url=None, rendered_url=None)
            route.fulfill(response=response, json=data)

        self.page.route("**/api/sessions/*/previews", queued_initial_response)
        self.request_previews("Model A", TOPS, offered=3)
        expect(self.page.locator("#notice")).to_contain_text("Previews are being prepared.")
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
        expect(self.page.locator("#notice")).to_contain_text("Recorded results shown. No new AI run and no units used.")
        self.check("recorded previews completed through polling")

    def _exercise_preview_status_recovery(self, checks_pending=False):
        self.page.add_init_script("""window.pollTestOffset = 0;
            const realNow = Date.now.bind(Date); Date.now = () => realNow() + window.pollTestOffset;""")
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        hold = [True]
        methods = []
        self.page.on("request", lambda request: methods.append((request.method, request.url))
                     if "/previews" in request.url else None)

        def delayed(data):
            if checks_pending:
                data["gate_pending"] = True
            else:
                data.update(state="running", image_url=None, rendered_url=None)
            return data

        def initial(route):
            response = route.fetch()
            data = response.json()
            data["jobs"] = [delayed(job) for job in data["jobs"]]
            route.fulfill(response=response, json=data)

        def status(route):
            response = route.fetch()
            data = response.json()
            route.fulfill(response=response, json=delayed(data) if hold[0] else data)

        self.page.route("**/api/sessions/*/previews", initial)
        self.page.route("**/api/previews/*", status)
        self.request_previews("Model A", TOPS, offered=3)
        self.page.evaluate("window.pollTestOffset = 151000")
        expect(self.button("Check preview status")).to_be_visible()
        expect(self.page.locator("#previewStatusCheck")).to_contain_text("will not request another preview")
        if checks_pending:
            expect(self.page.locator("#notice")).not_to_contain_text("Previews complete.")
        self.check("slow preview status is recoverable")
        posts_before = [url for method, url in methods if method == "POST"]
        jobs_before = PreviewJob.objects.count()
        hold[0] = False
        self.button("Check preview status").click()
        expect(self.page.locator(".status-pill")).to_have_text(["Ready"] * 3)
        expect(self.page.locator("#previewStatusCheck")).to_have_count(0)
        self.assertEqual([url for method, url in methods if method == "POST"], posts_before)
        self.assertEqual(PreviewJob.objects.count(), jobs_before)
        self.assert_step(TOPS)
        self.check("existing preview status refreshed without generation")

    def test_slow_previews_can_be_checked_without_requesting_new_jobs(self):
        self._exercise_preview_status_recovery()

    def test_slow_preview_checks_are_not_mistaken_for_a_completed_comparison(self):
        self._exercise_preview_status_recovery(checks_pending=True)

    def test_partial_combined_look_is_labelled_without_claiming_the_failed_item(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        posts = []
        self.page.on("request", lambda request: posts.append(request.url)
                     if request.method == "POST" and "/looks" in request.url else None)

        def partial(route):
            response = route.fetch()
            data = response.json()
            data.update(state="failed", partial=True, completed_steps=2, total_steps=3,
                        image_items=[item["title"] for item in data["items"][:2]],
                        message="Step 3 of 3: The necklace could not be applied.")
            data["items"][2]["state"] = "failed"
            route.fulfill(response=response, json=data)

        self.page.route("**/api/sessions/*/looks", partial)
        self.page.get_by_role("button", name=re.compile("^See it with the rest of my order: ")).first.click()
        self.assert_step("See it with what you're keeping.")
        expect(self.page.locator("#look .status-pill")).to_have_text(
            ["Partial preview — 2 of 3 steps completed"] * 2)
        for img in self.page.locator("#lookCards img").all():
            self.assertIn("Partial AI preview", img.get_attribute("alt"))
            self.assertNotIn("Amethyst", img.get_attribute("alt"))
        self.check("partial look retains a clear failure and accessible recovery")
        self.assertEqual(len(posts), 2)
        self.button("Back to comparison").click()
        self.assert_step(TOPS)
        self.assertEqual(len(posts), 2)

    def test_slow_combined_looks_can_be_checked_without_new_look_requests(self):
        self.page.add_init_script("""window.pollTestOffset = 0;
            const realNow = Date.now.bind(Date); Date.now = () => realNow() + window.pollTestOffset;""")
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        hold = [True]
        methods = []
        self.page.on("request", lambda request: methods.append((request.method, request.url))
                     if "/looks" in request.url else None)

        def delayed(data):
            data.update(state="running", image_url=None)
            for item in data["items"]:
                item["state"] = "running"
            return data

        def initial(route):
            response = route.fetch()
            route.fulfill(response=response, json=delayed(response.json()))

        def status(route):
            response = route.fetch()
            data = response.json()
            route.fulfill(response=response, json=delayed(data) if hold[0] else data)

        self.page.route("**/api/sessions/*/looks", initial)
        self.page.route("**/api/looks/*", status)
        self.page.get_by_role("button", name=re.compile("^See it with the rest of my order: ")).first.click()
        self.assert_step("See it with what you're keeping.")
        self.page.evaluate("window.pollTestOffset = 241000")
        expect(self.button("Check look status")).to_be_visible()
        expect(self.page.locator("#lookStatusCheck")).to_contain_text("will not create another look")
        self.check("slow combined look status is recoverable")
        posts_before = [url for method, url in methods if method == "POST"]
        jobs_before = PreviewJob.objects.count()
        hold[0] = False
        self.button("Check look status").click()
        expect(self.page.locator("#lookCards .status-pill")).to_have_text(["Ready"] * 2)
        expect(self.page.locator("#lookStatusCheck")).to_have_count(0)
        self.assertEqual([url for method, url in methods if method == "POST"], posts_before)
        self.assertEqual(PreviewJob.objects.count(), jobs_before)
        self.assert_step("See it with what you're keeping.")
        self.check("existing combined looks refreshed without generation")

    def test_late_manual_status_cannot_replace_a_new_photo_comparison(self):
        self.page.add_init_script("""window.pollTestOffset = 0;
            const realNow = Date.now.bind(Date); Date.now = () => realNow() + window.pollTestOffset;""")
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        first_jobs = set()
        held = []
        manual = [False]
        initial_count = [0]

        def initial(route):
            response = route.fetch()
            data = response.json()
            initial_count[0] += 1
            if initial_count[0] == 1:
                for job in data["jobs"]:
                    first_jobs.add(job["job_id"])
                    job.update(state="running", image_url=None, rendered_url=None)
            route.fulfill(response=response, json=data)

        def status(route):
            response = route.fetch()
            data = response.json()
            if data["job_id"] in first_jobs:
                if manual[0]:
                    held.append((route, response))
                    return
                data.update(state="running", image_url=None, rendered_url=None)
            route.fulfill(response=response, json=data)

        self.page.route("**/api/sessions/*/previews", initial)
        self.page.route("**/api/previews/*", status)
        self.request_previews("Model A", TOPS, offered=3)
        self.page.evaluate("window.pollTestOffset = 151000")
        expect(self.button("Check preview status")).to_be_visible()
        manual[0] = True
        self.button("Check preview status").click()
        for _ in range(100):
            if len(held) == 3:
                break
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 3)
        self.request_previews("Model B", TOPS, offered=3)
        sage = self.page.locator('#cards .card[data-sku="CALM-04"]')
        expect(sage.locator(".gate-badge")).to_have_text("Flagged: colour")
        for route, response in held:
            route.fulfill(response=response)
        expect(self.page.locator("#previewStatusCheck")).to_have_count(0)
        expect(sage.locator(".gate-badge")).to_have_text("Flagged: colour")
        self.assertEqual(initial_count[0], 2)
        self.check("late manual status leaves the new photo comparison intact")

    def test_delayed_look_request_cannot_reopen_an_old_photo_comparison(self):
        self.open_item(TOP, "I don't like how it looks")
        self.button("Confirm preferences").click()
        self.request_previews("Model A", TOPS, offered=3)
        held = []

        def initial(route):
            response = route.fetch()
            held.append((route, response))

        self.page.route("**/api/sessions/*/looks", initial)
        self.page.get_by_role("button", name=re.compile("^See it with the rest of my order: ")).first.click()
        for _ in range(100):
            if held:
                break
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.request_previews("Model B", TOPS, offered=3)
        # Track the completion of the delayed JSON read, then allow the response continuation to run.
        self.page.evaluate("""() => {
            const original = Response.prototype.json;
            Response.prototype.json = async function() {
                const data = await original.call(this);
                if (this.url.includes('/looks')) setTimeout(() => window.lookResponseRead = true, 0);
                return data;
            };
        }""")
        route, response = held[0]
        route.fulfill(response=response)
        self.page.wait_for_function("window.lookResponseRead === true")
        expect(self.page.locator("#look")).to_be_hidden()
        expect(self.page.locator("#lookCards .look-card")).to_have_count(0)
        self.assertEqual(len(held), 1, "A stale look response must not submit the next comparison look")
        self.assert_step(TOPS)
        self.check("delayed look request leaves the new photo comparison intact")
