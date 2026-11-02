"""Run the recorded feasibility pairs as preview jobs, one pair at a time.

--set clothes, the default, runs the ten Clothes V4 try-on pairs. --set shoes and --set bags run the shoe pairs
through Shoes V3 on the full-length demo model and the bag pairs through Bag V2 on Model A. --set necklace runs the necklace pairs
through Necklace VTO on a demo person. --set beauty runs the lip,
foundation and nail pairs on the published demo faces and hand, each through the YouCam feature
its category needs: Makeup VTO or Nail VTO. --set jewellery runs the earring pairs through Earrings
VTO on a demo face, with each earring's product photo as the reference. --set looks runs whole looks
on a demo person: a top through Clothes V4, then the lipstick through Makeup VTO and the earrings
through Earrings VTO, each step on the result of the one before.

Dry run by default: it checks the inputs and prints the plan, the unit estimate for the current
provider and what the same pairs cost on YouCam, writing nothing. --execute creates or reuses one
session and person asset per model and drives each pair's job to an end state. A paid provider
also needs --confirm-units equal to the estimate, plus room under PREVIEW_UNIT_CEILING and
today's PREVIEW_DAILY_UNIT_CAP. Settings load YouCam only with the live switch described in
README.md.

--reconcile FILE reads a saved copy of YouCam's unit history and records what each task
actually cost, replacing the assumption that failed tasks are charged.

Reports go to <PRIVATE_MEDIA_ROOT>/feasibility/reports/<label>/ and are never committed.
"""
import hashlib
import json
import os
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from exchange import preview_jobs
from exchange.assets import normalize_photo, private_image_exists, read_private_image, save_private_image
from exchange.management.commands.reconcile_units import entries_from_file
from exchange.models import ExchangeSession, LookPreview, OrderLine, PreviewJob, PrivateAsset, ProductVariant
from secondlook.providers import FAILED, PENDING, RUNNING, SUCCEEDED, UNCERTAIN

ORDER_TOKEN = "DEMO-ORDER-001"
GATE = ("Proceed only if at least 8 of 10 pairs are useful enough for a style comparison, none "
        "materially misrepresents the garment, and delay and cost fit the owner-approved budget.")
SCALE = "Garment detail per aspect: 0 absent or misleading, 1 partly retained, 2 broadly recognizable."
BEAUTY_GATE = ("Switch a feature on only if each of its results puts the colour on the right area alone "
               "(lips, skin or nails), reads as the requested shade, leaves the face or hand otherwise "
               "unchanged, and delay and cost fit the owner-approved budget.")
BEAUTY_SCALE = "Applied area and shade match: 0 wrong or misleading, 1 partly right, 2 clearly right."
JEWELLERY_GATE = ("Switch earrings on only if each result shows the earring on the ear at a believable size, "
                  "keeps its metal and design, leaves the face otherwise unchanged, and delay and cost fit the "
                  "owner-approved budget.")
JEWELLERY_SCALE = "Placement and likeness to the product photo: 0 wrong or misleading, 1 partly right, 2 clearly right."
LOOK_GATE = ("Show recorded looks only if each result keeps the top's design, puts the lipstick on the lips alone, "
             "hangs the necklace at the neck at a believable length, keeps the person recognisably the same from "
             "step to step, and delay and cost fit the owner-approved budget.")
HAIR_GATE = ("Switch hair colour on only if each result colours the hair alone, reads as the requested shade, leaves "
             "the face and clothes unchanged, and delay and cost fit the owner-approved budget.")
ACCESSORY_GATE = ("Switch shoes and bags on only if each result puts the product where it is worn or carried, keeps its "
                  "colour and design, leaves the person otherwise unchanged, and delay and cost fit the owner-approved "
                  "budget.")
NECKLACE_GATE = ("Switch necklaces on only if each result hangs the necklace at the neck at a believable length, keeps "
                 "its metal and design, leaves the person otherwise unchanged, and delay and cost fit the "
                 "owner-approved budget.")
LOOK_SCALE = "Each step's item and the person's stability: 0 wrong or misleading, 1 partly right, 2 clearly right."
# The demo order line each category belongs to, so a pilot session matches a shopper's.
BEAUTY_LINES = {"lip": "DEMO-ORDER-001-LIP", "foundation": "DEMO-ORDER-001-FDN", "nail": "DEMO-ORDER-001-NAIL",
                "earrings": "DEMO-ORDER-001-EAR", "necklace": "DEMO-ORDER-001-NECK", "hair": "DEMO-ORDER-001-HAIR",
                "shoes": "DEMO-ORDER-001-SHOE", "bag": "DEMO-ORDER-001-BAG"}
# The demo person each category is previewed on.
DEMO_KINDS = {"lip": "face", "foundation": "face", "blush": "face", "nail": "hand", "earrings": "face",
              "necklace": "person", "hair": "person", "shoes": "full", "bag": "person"}
# Categories previewed from a product photo with a recorded size, rather than a shade.
PRODUCT_PHOTO = ("earrings", "necklace", "shoes", "bag")
FEATURE_NAMES = preview_jobs.FEATURE_NAMES
GATES = {"clothes": (GATE, SCALE), "beauty": (BEAUTY_GATE, BEAUTY_SCALE),
         "jewellery": (JEWELLERY_GATE, JEWELLERY_SCALE), "necklace": (NECKLACE_GATE, JEWELLERY_SCALE),
         "hair": (HAIR_GATE, BEAUTY_SCALE), "shoes": (ACCESSORY_GATE, JEWELLERY_SCALE),
         "bags": (ACCESSORY_GATE, JEWELLERY_SCALE),
         "looks": (LOOK_GATE, LOOK_SCALE)}


def blank_review(which="clothes"):
    if which in ("beauty", "hair"):
        return {"applied_area": None, "shade_match": None, "person_stability": None, "usefulness": None,
                "reason": ""}
    if which in ("jewellery", "necklace", "shoes", "bags"):
        return {"placement": None, "likeness": None, "person_stability": None, "usefulness": None, "reason": ""}
    if which == "looks":
        return {"top": None, "lipstick": None, "person_stability": None, "usefulness": None, "reason": ""}
    return {"garment_detail": {"pattern": None, "sleeve": None, "neckline": None},
            "person_stability": None, "usefulness": None, "reason": ""}


def youcam_units(category):
    """What one pair costs on YouCam, from the prices the read-only feature-cost check confirmed."""
    feature = preview_jobs.FEATURES.get(category)
    return {"makeup": settings.YOUCAM_MAKEUP_UNITS, "nail": settings.YOUCAM_NAIL_UNITS,
            "earring": settings.YOUCAM_EARRING_UNITS,
            "necklace": settings.YOUCAM_NECKLACE_UNITS,
            "hair": settings.YOUCAM_HAIR_UNITS, "shoes": settings.YOUCAM_SHOES_UNITS,
            "bag": settings.YOUCAM_BAG_UNITS}.get(feature, settings.YOUCAM_UNITS_PER_RESULT)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Command(BaseCommand):
    help = "Plan, run or reconcile the try-on feasibility pairs. Dry run unless --execute."

    def add_arguments(self, parser):
        parser.add_argument("--sources", default=str(Path(settings.BASE_DIR) / "assets" / "pixabay_sources.json"))
        parser.add_argument("--set", dest="which",
                            choices=("clothes", "beauty", "jewellery", "necklace", "hair", "shoes", "bags", "looks"),
                            default="clothes",
                            help="Which pairs: clothes try-on, beauty shades, earrings, necklaces, hair colour or "
                                 "whole looks.")
        parser.add_argument("--label", default=None, help="Names this run; reruns with the same label reuse its "
                                                          "jobs. Default: pilot, or <set>-pilot for other sets.")
        parser.add_argument("--execute", action="store_true", help="Create and drive jobs. Without it nothing is written.")
        parser.add_argument("--confirm-units", type=int, default=None,
                            help="Required for a paid provider: must equal the unit estimate.")
        parser.add_argument("--stop-after-failures", type=int, default=3,
                            help="Stop after this many failed or uncertain pairs in a row.")
        parser.add_argument("--reconcile", metavar="FILE", help="Saved unit history JSON to record actual charges from.")
        parser.add_argument("--pairs", help="Comma-separated pair numbers to run, such as 1 or 2,3. Default: all.")

    def handle(self, *args, **options):
        which = options["which"]
        label = options["label"] or ("pilot" if which == "clothes" else f"{which}-pilot")
        if not label.replace("-", "").isalnum() or len(label) > 40:
            raise CommandError("Use a label of letters, digits and dashes, at most 40 characters.")
        sources = json.loads(Path(options["sources"]).read_text(encoding="utf-8"))
        full_plan = (self.plan(sources) if which == "clothes" else self.look_plan(sources) if which == "looks"
                     else self.beauty_plan(sources, which))
        plan = full_plan
        if options["pairs"]:
            try:
                wanted = {int(part) for part in options["pairs"].split(",")}
            except ValueError:
                raise CommandError("Give --pairs as numbers, such as 1 or 2,3.") from None
            plan = [item for item in full_plan if item["n"] in wanted]
            if len(plan) != len(wanted):
                raise CommandError(f"Pair numbers run from 1 to {len(full_plan)}.")
        # Each pair runs on the provider its category needs: youcam becomes youcam-makeup for a lipstick. A look
        # has one step per item, each on its own provider.
        providers = {}
        try:
            for item in full_plan:
                item["providers"] = []
                for category in item.get("categories") or [item["category"]]:
                    name = preview_jobs.provider_name_for(category)
                    if name not in providers:
                        providers[name] = preview_jobs.get_provider(name)
                    item["providers"].append(providers[name])
                item["provider"] = item["providers"][0]
        except preview_jobs.ProviderUnavailable as exc:
            raise CommandError(str(exc)) from None
        # A look's steps already made, here or as the first steps of another look, are not run or paid for again.
        made = {item["n"]: self.made_steps(label, item) for item in plan} if which == "looks" else {}
        estimate = sum(provider.units_per_job for item in plan for provider in item["providers"][made.get(item["n"], 0):])
        noun = "looks" if which == "looks" else "pairs"
        committed = preview_jobs.committed_units()
        ceiling = settings.PREVIEW_UNIT_CEILING
        for provider in providers.values():
            self.stdout.write(f"Provider: {provider.name} {provider.version}")
        self.stdout.write(f"{noun.capitalize()}: {len(plan)}. Unit estimate: {estimate}. "
                          f"Already committed: {committed}. Ceiling: {ceiling}.")
        by_feature = {}
        for item in plan:
            for category in item.get("categories") or [item["category"]]:
                feature = preview_jobs.feature_name(category)
                by_feature[feature] = by_feature.get(feature, 0) + youcam_units(category)
        self.stdout.write(f"On YouCam these {noun} cost {sum(by_feature.values())} units: "
                          + ", ".join(f"{name} {units}" for name, units in by_feature.items()) + ".")
        if any(made.values()):
            self.stdout.write("Already made: " + ", ".join(f"look {n} steps 1 to {k}" for n, k in made.items() if k)
                              + ". Those steps are not run again.")
        if options["reconcile"]:
            changed, unmatched = self.reconcile(label, Path(options["reconcile"]))
            self.stdout.write(f"Recorded actual charges for {changed} tasks.")
            if unmatched:
                self.stdout.write(self.style.WARNING(
                    f"{unmatched} tasks had no matching charge in the history; their counts were left unchanged."))
            self.report(label, full_plan, which, billing="reconciled from unit history")
            return
        if not options["execute"]:
            for item in plan:
                self.stdout.write(f"  {item['n']:>2}. {self.describe(item)}: {item['tests']}")
            self.stdout.write("Dry run. Nothing was written. Add --execute to run.")
            return
        if preview_jobs.control().paused:
            raise CommandError("Previews are paused. Resume them with manage.py preview_switch resume first.")
        if estimate:
            if committed + estimate > ceiling:
                raise CommandError(f"This run may spend {estimate} units, but only {max(ceiling - committed, 0)} "
                                   "remain under PREVIEW_UNIT_CEILING. Nothing was sent.")
            today = preview_jobs.committed_units(started_at__gte=preview_jobs._day_start())
            if today + estimate > settings.PREVIEW_DAILY_UNIT_CAP:
                raise CommandError(f"This run may spend {estimate} units, but only "
                                   f"{max(settings.PREVIEW_DAILY_UNIT_CAP - today, 0)} remain under "
                                   "PREVIEW_DAILY_UNIT_CAP today. Nothing was sent.")
            if options["confirm_units"] != estimate:
                raise CommandError(f"This run may spend up to {estimate} units. Re-run with "
                                   f"--confirm-units {estimate} to proceed. Nothing was sent.")
        self.run(label, plan, options["stop_after_failures"])
        self.report(label, full_plan, which, billing="assumed: failed tasks counted as charged" if estimate else "none")

    def plan(self, sources):
        models = {m["name"]: m for m in sources["models"]}
        folder = Path(settings.PRIVATE_MEDIA_ROOT) / "feasibility"
        variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
        plan = []
        for n, pair in enumerate(sources["pairs"], 1):
            model = models.get(pair["model"])
            if model is None:
                raise CommandError(f"Pair {n} names an unknown model, {pair['model']}.")
            if not model["source"]["ai_generated"]:
                raise CommandError(f"{model['name']} is not tagged AI-generated; only synthetic models may be used.")
            path = folder / f"{model['name']}.jpg"
            if not path.exists():
                raise CommandError(f"{path.name} is missing. Run scripts/build_pixabay_assets.py first.")
            if sha256(path) != model["source"]["source_sha256"]:
                raise CommandError(f"{path.name} does not match its recorded hash. Rebuild it before running.")
            variant = variants.get(pair["sku"])
            garment = finders.find(variant.image_asset_id) if variant and variant.image_asset_id else None
            if not garment:
                raise CommandError(f"Pair {n} needs a catalog photo for {pair['sku']}. Run seed_demo first.")
            plan.append({"n": n, "model": model["name"], "model_entry": model, "model_path": path,
                         "sku": pair["sku"], "variant": variant, "garment_path": Path(garment), "tests": pair["tests"],
                         "category": variant.category})
        return plan

    def beauty_plan(self, sources, which="beauty"):
        """Shade or earring pairs on the published demo faces and hand, checked against the committed manifest."""
        # Faces and the hand are beauty models; necklaces go on a half-body model from the clothes list.
        models = {m["name"]: m for m in sources.get("beauty_models", []) + sources.get("models", [])}
        manifest = finders.find("exchange/models/manifest.json")
        published = ({m["name"]: m for m in json.loads(Path(manifest).read_text(encoding="utf-8"))["models"]}
                     if manifest else {})
        variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
        plan = []
        for n, pair in enumerate(sources.get(f"{which}_pairs", []), 1):
            model = models.get(pair["model"])
            if model is None:
                raise CommandError(f"Pair {n} names an unknown model, {pair['model']}.")
            if not model["source"]["ai_generated"]:
                raise CommandError(f"{model['name']} is not tagged AI-generated; only synthetic models may be used.")
            demo = published.get(model["name"])
            path = finders.find(demo["static_path"]) if demo else None
            if not path:
                raise CommandError(f"{model['name']} is not published. Run scripts/build_pixabay_assets.py first.")
            if sha256(path) != demo["sha256"]:
                raise CommandError(f"{Path(path).name} does not match its recorded hash. Rebuild it before running.")
            variant = variants.get(pair["sku"])
            earring = variant is not None and variant.category in PRODUCT_PHOTO
            if variant is None or variant.category not in DEMO_KINDS or not (
                    (earring and variant.image_asset_id and variant.length_mm) or variant.shade_hex):
                raise CommandError(f"Pair {n} needs the product {pair['sku']} with its details. Run seed_demo first.")
            needed = DEMO_KINDS[variant.category]
            if (model.get("kind") or demo.get("kind") or "person") != needed:
                raise CommandError(f"Pair {n} previews {variant.category} on {model['name']}, but it needs a {needed}.")
            plan.append({"n": n, "model": model["name"], "model_entry": model, "model_path": Path(path),
                         "model_sha256": demo["sha256"], "sku": pair["sku"], "variant": variant,
                         "tests": pair["tests"], "category": variant.category})
        return plan

    def look_plan(self, sources):
        """Whole looks on a published demo person, checked against the committed manifest."""
        models = {m["name"]: m for m in sources["models"]}
        manifest = finders.find("exchange/models/manifest.json")
        published = ({m["name"]: m for m in json.loads(Path(manifest).read_text(encoding="utf-8"))["models"]}
                     if manifest else {})
        variants = {v.sku: v for v in ProductVariant.objects.filter(retailer_id="secondlook-demo")}
        plan = []
        for n, pair in enumerate(sources.get("look_pairs", []), 1):
            model = models.get(pair["model"])
            if model is None:
                raise CommandError(f"Look {n} names an unknown model, {pair['model']}.")
            if not model["source"]["ai_generated"]:
                raise CommandError(f"{model['name']} is not tagged AI-generated; only synthetic models may be used.")
            demo = published.get(model["name"])
            path = finders.find(demo["static_path"]) if demo else None
            if not path:
                raise CommandError(f"{model['name']} is not published. Run scripts/build_pixabay_assets.py first.")
            if sha256(path) != demo["sha256"]:
                raise CommandError(f"{Path(path).name} does not match its recorded hash. Rebuild it before running.")
            if demo.get("kind", "person") != "person":
                raise CommandError(f"Look {n} needs a photo of a person's upper body, but {model['name']} is a "
                                   f"{demo['kind']}.")
            items = [variants.get(sku) for sku in pair["skus"]]
            if any(v is None or not (v.shade_hex if v.category == "lip" else v.image_asset_id) for v in items):
                raise CommandError(f"Look {n} needs the products {', '.join(pair['skus'])} with their details. "
                                   "Run seed_demo first.")
            if any(v.category == "necklace" and not v.length_mm for v in items):
                raise CommandError(f"Look {n} needs the necklace's length. Run seed_demo first.")
            categories = [v.category for v in items]
            if categories != sorted(categories, key=lambda c: preview_jobs.LOOK_ORDER.index(c)
                                    if c in preview_jobs.LOOK_ORDER else 99) or not set(categories) <= set(
                    preview_jobs.LOOK_ORDER):
                raise CommandError(f"Look {n} must add a top, then a lipstick, then a necklace.")
            plan.append({"n": n, "model": model["name"], "model_entry": model, "model_path": Path(path),
                         "model_sha256": demo["sha256"], "sku": "+".join(pair["skus"]), "skus": pair["skus"],
                         "variant": items[0], "variants": items, "categories": categories, "category": "look",
                         "tests": pair["tests"]})
        return plan

    def describe(self, item):
        variant = item["variant"]
        if item.get("variants"):
            return f"{item['model']}: " + ", then ".join(self.describe_item(v) for v in item["variants"])
        if item["category"] not in DEMO_KINDS:
            return f"{item['model']} x {item['sku']}"
        feature = FEATURE_NAMES[preview_jobs.FEATURES.get(item["category"])]
        if item["category"] in PRODUCT_PHOTO:
            return (f"{item['model']} x {item['sku']} ({variant.title}, {variant.color_tag} {variant.form}, "
                    f"{variant.length_mm} mm) on {feature}")
        return (f"{item['model']} x {item['sku']} ({variant.shade_name} {variant.shade_hex}, {variant.finish}) "
                f"on {feature}")

    def describe_item(self, variant):
        feature = preview_jobs.feature_name(variant.category)
        if variant.category in PRODUCT_PHOTO:
            return f"{variant.sku} ({variant.color_tag} {variant.form}, {variant.length_mm} mm) on {feature}"
        if variant.shade_hex:
            return f"{variant.sku} ({variant.shade_name} {variant.shade_hex}, {variant.finish}) on {feature}"
        return f"{variant.sku} on {feature}"

    def session_for(self, label, item):
        key, token = f"feasibility:{label}:{item['model']}", ORDER_TOKEN
        if item["category"] in BEAUTY_LINES:
            # A face is used for both lipstick and foundation, so each category gets its own session.
            key, token = f"{key}:{item['category']}", BEAUTY_LINES[item["category"]]
        session = ExchangeSession.objects.filter(browser_session_key=key).first()
        if session is None:
            line = OrderLine.objects.filter(token=token).first()
            if line is None:
                raise CommandError("The demo order is missing. Run seed_demo first.")
            session = ExchangeSession.objects.create(
                browser_session_key=key, order_line=line, status="FEASIBILITY", consent_given=True,
                consent_version=1, expires_at=timezone.now() + timedelta(days=30))
        return session

    def asset_for(self, session, item):
        note = f"AI-generated Pixabay image {item['model_entry']['source']['pixabay_id']}; depicts no real person"
        existing = PrivateAsset.objects.filter(owner_session=session, kind="person", license_or_consent=note,
                                               expires_at__gt=timezone.now()).first()
        if existing and private_image_exists(existing.storage_key):
            return existing
        data = item["model_path"].read_bytes()
        key, digest = save_private_image(normalize_photo(SimpleUploadedFile(item["model_path"].name, data,
                                                                            content_type="image/jpeg")))
        return PrivateAsset.objects.create(owner_session=session, kind="person", license_or_consent=note,
                                           sha256=digest, storage_key=key,
                                           expires_at=timezone.now() + timedelta(days=30))

    def job_for(self, label, item):
        provider = item["provider"]
        session = self.session_for(label, item)
        asset = self.asset_for(session, item)
        variant = item["variant"]
        fingerprint = hashlib.sha256(
            f"feasibility:{label}:{item['model']}:{asset.sha256}:{variant.sku}:{variant.version}:"
            f"{provider.name}:{provider.version}".encode()).hexdigest()
        job, _ = PreviewJob.objects.get_or_create(fingerprint=fingerprint, defaults={
            "owner_session": session, "variant": variant, "provider": provider.name,
            "provider_version": provider.version, "consent_version": session.consent_version,
            "person_asset": asset, "person_asset_sha256": asset.sha256})
        return job

    def drive(self, job):
        """Advance one job until it ends. A job that already ended is never restarted."""
        state, polls = job.state, 0
        if state == PENDING:
            state = preview_jobs.advance(str(job.id))
        while state == RUNNING:
            if polls >= settings.PREVIEW_MAX_POLLS:
                return preview_jobs.give_up(str(job.id))
            time.sleep(settings.PREVIEW_POLL_SECONDS)
            polls += 1
            state = preview_jobs.advance(str(job.id))
        return state

    def earlier_look(self, label, item):
        """A longer look in this run whose first steps made exactly this look, such as the looks probe, whose top and
        lipstick steps succeeded before its earrings step was refused. Returns the look and those steps."""
        n = len(item["skus"])
        for look in LookPreview.objects.filter(
                owner_session__browser_session_key=f"feasibility:{label}:{item['model']}").order_by("created_at"):
            if len(look.plan) > n and [entry["sku"] for entry in look.plan[:n]] == item["skus"]:
                steps = preview_jobs.look_steps(look)[:n]
                if len(steps) == n and all(step.state == SUCCEEDED for step in steps):
                    return look, steps
        return None, []

    def shorter_look(self, label, item):
        """The look in this run with the most of this look's first steps made, all succeeded, and fewer than all:
        such as a top-and-lipstick look before the necklace was added. Returns the look and those steps."""
        best, best_steps = None, []
        for look in LookPreview.objects.filter(
                owner_session__browser_session_key=f"feasibility:{label}:{item['model']}").order_by("created_at"):
            planned = [entry["sku"] for entry in look.plan]
            if planned == item["skus"]:
                continue
            steps = []
            for step in preview_jobs.look_steps(look):
                if (step.look_step >= len(item["skus"]) - 1 or step.state != SUCCEEDED
                        or planned[step.look_step] != item["skus"][step.look_step]):
                    break
                steps.append(step)
            if len(steps) > len(best_steps):
                best, best_steps = look, steps
        return best, best_steps

    def made_steps(self, label, item):
        """How many of a look's steps are already made, so a run is estimated and confirmed for the rest only."""
        look, steps = self.earlier_look(label, item)
        if look is not None:
            return len(steps)
        made = next((preview_jobs.look_steps(look) for look in LookPreview.objects.filter(
            owner_session__browser_session_key=f"feasibility:{label}:{item['model']}")
            if [entry["sku"] for entry in look.plan] == item["skus"]), None)
        if made is not None:
            return sum(1 for step in made if step.started_at)
        return len(self.shorter_look(label, item)[1])

    def run_look(self, label, item):
        """Drive a look's steps in turn, each on the result of the one before. A step that ended is never restarted."""
        look, steps = self.earlier_look(label, item)
        if look is not None:
            return SUCCEEDED, steps[-1], False
        plan = preview_jobs.look_plan(item["variants"])
        base, steps = self.shorter_look(label, item)
        if base is not None:
            look, job = preview_jobs.continue_look(base, steps, item["variants"], plan)
        else:
            session = self.session_for(label, item)
            asset = self.asset_for(session, item)
            look, job = preview_jobs.create_look(session, item["variants"], plan, asset)
        fresh = False
        while job is not None:
            if job.state in (PENDING, RUNNING):
                fresh = True
                state = self.drive(job)
            else:
                state = job.state
            job = preview_jobs.next_look_step(job.id) if state == SUCCEEDED else None
        state, decided = preview_jobs.look_state(look, preview_jobs.look_steps(look))
        return state, decided, fresh

    def run(self, label, plan, stop_after):
        misses = 0
        for item in plan:
            if item.get("variants"):
                state, decided, fresh = self.run_look(label, item)
                code = decided.error_code if decided else ""
                where = f" at step {decided.look_step + 1}" if decided and state != SUCCEEDED else ""
                note = f"{where} ({code})" if code else where
            else:
                job = self.job_for(label, item)
                fresh = job.state in (PENDING, RUNNING)
                state = self.drive(job) if fresh else job.state
                job.refresh_from_db()
                note = f" ({job.error_code})" if job.error_code else ""
            self.stdout.write(f"  {item['n']:>2}. {item['model']} x {item['sku']}: {state}{note}"
                              + ("" if fresh else ", from an earlier run"))
            misses = misses + 1 if fresh and state in (FAILED, UNCERTAIN) else (0 if state == SUCCEEDED else misses)
            if stop_after and misses >= stop_after:
                self.stdout.write(self.style.WARNING(f"Stopped after {misses} failed or uncertain pairs in a row."))
                return

    def looks_by_pair(self, label, plan):
        """Each look and its steps: the look made for it, or the first steps of a longer one that made it."""
        prefix = f"feasibility:{label}:"
        found = {}
        for look in LookPreview.objects.filter(owner_session__browser_session_key__startswith=prefix).select_related(
                "owner_session").order_by("created_at"):
            model = look.owner_session.browser_session_key[len(prefix):].split(":")[0]
            found[(model, tuple(entry["sku"] for entry in look.plan))] = look
        looks = {}
        for item in plan:
            look = found.get((item["model"], tuple(item["skus"])))
            looks[item["n"]] = ((look, preview_jobs.look_steps(look), False) if look
                                else (*self.earlier_look(label, item), True))
        return looks

    def jobs_by_pair(self, label, plan):
        prefix = f"feasibility:{label}:"
        # A pair rerun after an adapter fix gets a new job; the newest one is the pair's result.
        jobs = PreviewJob.objects.filter(owner_session__browser_session_key__startswith=prefix).select_related(
            "owner_session", "variant", "result_asset", "person_asset").order_by("created_at")
        found = {}
        for job in jobs:
            model = job.owner_session.browser_session_key[len(prefix):].split(":")[0]
            found[(model, job.variant.sku)] = job
        return {item["n"]: found.get((item["model"], item["sku"])) for item in plan}

    def reconcile(self, label, history_file):
        jobs = PreviewJob.objects.filter(owner_session__browser_session_key__startswith=f"feasibility:{label}:"
                                         ).exclude(provider="mock").exclude(provider_task_id="")
        result = preview_jobs.reconcile_units(entries_from_file(history_file), jobs=jobs)
        return result["matched"], result["unmatched"]

    def inputs(self, item):
        if item.get("variants"):
            return {"model_file": str(item["model_path"].relative_to(settings.BASE_DIR)),
                    "model_source": item["model_entry"]["source"]["page"], "model_sha256": item["model_sha256"],
                    "items": [self.item_inputs(v) for v in item["variants"]]}
        if item["category"] not in DEMO_KINDS:
            return {"model_file": item["model_path"].name, "model_source": item["model_entry"]["source"]["page"],
                    "garment_file": str(item["garment_path"].relative_to(settings.BASE_DIR)),
                    "garment_sha256": sha256(item["garment_path"])}
        variant = item["variant"]
        if variant.category in PRODUCT_PHOTO:
            product = Path(finders.find(variant.image_asset_id))
            return {"model_file": str(item["model_path"].relative_to(settings.BASE_DIR)),
                    "model_source": item["model_entry"]["source"]["page"], "model_sha256": item["model_sha256"],
                    "earring": {"sku": variant.sku, "title": variant.title, "category": variant.category,
                                "metal": variant.color_tag,
                                "form": variant.form, "length_mm": variant.length_mm, "scale_mm": variant.scale_mm,
                                "feature": FEATURE_NAMES["earring"],
                                "image_file": str(product.relative_to(settings.BASE_DIR)),
                                "image_sha256": sha256(product)}}
        return {"model_file": str(item["model_path"].relative_to(settings.BASE_DIR)),
                "model_source": item["model_entry"]["source"]["page"], "model_sha256": item["model_sha256"],
                "shade": {"sku": variant.sku, "name": variant.shade_name, "hex": variant.shade_hex,
                          "finish": variant.finish, "category": variant.category,
                          "feature": FEATURE_NAMES[preview_jobs.FEATURES.get(variant.category)]}}

    def item_inputs(self, variant):
        entry = {"sku": variant.sku, "title": variant.title, "feature": preview_jobs.feature_name(variant.category)}
        if variant.category == "lip":
            return {**entry, "hex": variant.shade_hex, "finish": variant.finish}
        image = Path(finders.find(variant.image_asset_id))
        entry.update(image_file=str(image.relative_to(settings.BASE_DIR)), image_sha256=sha256(image))
        if variant.category in PRODUCT_PHOTO:
            entry.update(metal=variant.color_tag, form=variant.form, length_mm=variant.length_mm)
        return entry

    def step_row(self, job, folder, n, model):
        elapsed = (job.ended_at - job.started_at).total_seconds() if job.started_at and job.ended_at else None
        result_file = None
        if job.result_asset and private_image_exists(job.result_asset.storage_key):
            result_file = f"{n:02d}-{model}-step{job.look_step + 1}-{job.variant.sku}.jpg"
            (folder / result_file).write_bytes(read_private_image(job.result_asset.storage_key))
            os.chmod(folder / result_file, 0o600)
        row = {"step": job.look_step + 1, "sku": job.variant.sku,
               "feature": preview_jobs.feature_name(job.variant.category), "state": job.state, "error_code": job.error_code, "job_id": str(job.id), "provider": job.provider,
               "provider_version": job.provider_version, "provider_task_id": job.provider_task_id,
               "elapsed_seconds": elapsed, "reserved_units": job.reserved_units, "observed_units": job.observed_units,
               "result_file": result_file}
        if job.variant.category == "lip":
            row["shade_check"] = {"status": job.colour_check, "delta": job.colour_delta, "note": job.colour_note}
        return row

    def look_row(self, item, found, folder):
        look, jobs, reused = found
        row = {"pair": item["n"], "model": item["model"], "sku": item["sku"], "skus": item["skus"],
               "tests": item["tests"], "inputs": self.inputs(item)}
        if look is None:
            row.update(state="not attempted", job_id=None, result_file=None, steps=[])
            return row, []
        if reused:
            planned = "+".join(entry["sku"] for entry in look.plan)
            row["reused_from"] = f"the first {len(jobs)} steps of look {look.id}, planned as {planned}"
        elif look.continues_id:
            planned = "+".join(entry["sku"] for entry in look.continues.plan)
            row["continued_from"] = (f"steps 1 to {look.continued_steps} of look {look.continues_id}, planned as "
                                     f"{planned}")
        state, decided = preview_jobs.look_state(look, jobs, len(item["skus"]))
        steps = [self.step_row(job, folder, item["n"], item["model"]) for job in jobs]
        timed = [step["elapsed_seconds"] for step in steps if step["elapsed_seconds"] is not None]
        row.update(state=state, error_code=decided.error_code if decided and state != SUCCEEDED else "",
                   job_id=str(look.id), steps=steps, elapsed_seconds=sum(timed) if timed else None,
                   reserved_units=sum(step["reserved_units"] for step in steps),
                   observed_units=sum(step["observed_units"] for step in steps),
                   result_file=steps[-1]["result_file"] if state == SUCCEEDED else None)
        return row, jobs

    def report(self, label, plan, which, billing):
        folder = Path(settings.PRIVATE_MEDIA_ROOT) / "feasibility" / "reports" / label
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        previous = {}
        old = folder / "report.json"
        if old.exists():
            previous = {(r["pair"], r.get("job_id")): r.get("review") for r in json.loads(old.read_text())["pairs"]}
        rows, jobs = [], ({} if which == "looks" else self.jobs_by_pair(label, plan))
        looks, ran = (self.looks_by_pair(label, plan) if which == "looks" else {}), []
        for item in plan:
            if which == "looks":
                row, steps = self.look_row(item, looks[item["n"]], folder)
                ran += steps
                row["review"] = previous.get((item["n"], row["job_id"])) or blank_review(which)
                rows.append(row)
                continue
            job = jobs[item["n"]]
            row = {"pair": item["n"], "model": item["model"], "sku": item["sku"], "tests": item["tests"],
                   "inputs": self.inputs(item)}
            if job is None:
                row.update(state="not attempted", job_id=None, result_file=None)
            else:
                elapsed = (job.ended_at - job.started_at).total_seconds() if job.started_at and job.ended_at else None
                result_file = None
                if job.result_asset and private_image_exists(job.result_asset.storage_key):
                    result_file = f"{item['n']:02d}-{item['model']}-{item['sku']}.jpg"
                    (folder / result_file).write_bytes(read_private_image(job.result_asset.storage_key))
                    os.chmod(folder / result_file, 0o600)
                row.update(state=job.state, error_code=job.error_code, job_id=str(job.id), provider=job.provider,
                           provider_version=job.provider_version, provider_task_id=job.provider_task_id,
                           person_sha256=job.person_asset_sha256, elapsed_seconds=elapsed,
                           reserved_units=job.reserved_units, observed_units=job.observed_units,
                           result_file=result_file)
                if which in ("beauty", "hair"):
                    # Raw shade-check readings, kept to calibrate the check before shoppers see it.
                    row["shade_check"] = {"status": job.colour_check, "delta": job.colour_delta,
                                          "note": job.colour_note}
                if which in ("jewellery", "necklace"):
                    row["size_check"] = {"status": job.size_check, "rendered_px": job.size_px, "note": job.size_note}
            row["review"] = previous.get((item["n"], row["job_id"])) or blank_review(which)
            rows.append(row)
        counts = {s: sum(1 for r in rows if r["state"] == s) for s in (SUCCEEDED, FAILED, UNCERTAIN, "not attempted")}
        # Name the providers that actually ran, not the ones configured for this invocation, which may be
        # the mock when an offline reconcile rewrites the report.
        ran += [job for job in jobs.values() if job is not None]
        names = sorted({job.provider for job in ran} or {item["provider"].name for item in plan})
        versions = sorted({job.provider_version for job in ran} or {item["provider"].version for item in plan})
        gate, scale = GATES[which]
        summary = {"label": label, "set": which, "provider": ", ".join(names), "provider_version": ", ".join(versions),
                   "generated_at": timezone.now().isoformat(), "pairs": len(rows),
                   "succeeded": counts[SUCCEEDED], "failed": counts[FAILED], "uncertain": counts[UNCERTAIN],
                   "not_attempted": counts["not attempted"],
                   "units": {"reserved": sum(r.get("reserved_units") or 0 for r in rows),
                             "observed": sum(r.get("observed_units") or 0 for r in rows), "billing": billing},
                   "gate": gate, "review_scale": scale, "decision": "pending human review"}
        (folder / "report.json").write_text(json.dumps({"summary": summary, "pairs": rows}, indent=2) + "\n")
        (folder / "report.md").write_text(self.markdown(summary, rows, folder))
        for name in ("report.json", "report.md"):
            os.chmod(folder / name, 0o600)
        self.stdout.write(f"Report: {folder / 'report.md'}")

    def markdown(self, summary, rows, folder):
        lines = [f"# Feasibility run {summary['label']}", "",
                 f"Provider {summary['provider']} {summary['provider_version']}, generated {summary['generated_at']}.", "",
                 "| Pairs | Succeeded | Failed | Uncertain | Not attempted | Units reserved | Units observed |",
                 "|---|---|---|---|---|---|---|",
                 f"| {summary['pairs']} | {summary['succeeded']} | {summary['failed']} | {summary['uncertain']} | "
                 f"{summary['not_attempted']} | {summary['units']['reserved']} | {summary['units']['observed']} |", "",
                 f"Billing: {summary['units']['billing']}.", "", f"Gate: {summary['gate']}", "",
                 summary["review_scale"], "Record each review in report.json; rerunning keeps those entries.", ""]
        for row in rows:
            state = (f"State: {row['state']}" + (f", {row['error_code']}" if row.get("error_code") else "") +
                     (f", {row['elapsed_seconds']:.0f} s" if row.get("elapsed_seconds") is not None else ""))
            result = f" ![Result]({row['result_file']})" if row["result_file"] else ""
            if "steps" in row:
                model = os.path.relpath(Path(settings.BASE_DIR) / row["inputs"]["model_file"], folder)
                lines += [f"## Look {row['pair']}: {row['model']} with {', '.join(row['skus'])}", "", row["tests"], "",
                          state + (f". Reused: {row['reused_from']}" if row.get("reused_from") else ""), ""]
                for step in row["steps"]:
                    check = step.get("shade_check") or {}
                    lines.append(f"{step['step']}. {step['feature']}, {step['sku']}: {step['state']}"
                                 + (f", {step['error_code']}" if step["error_code"] else "")
                                 + f", {step['observed_units']} units"
                                 + (f", shade check {check['status']} ΔE {check['delta']:.1f}"
                                    if check.get("delta") is not None else ""))
                lines += ["", f"![Model]({model})" + "".join(f" ![Step {s['step']}]({s['result_file']})"
                                                            for s in row["steps"] if s["result_file"]), ""]
                continue
            lines += [f"## Pair {row['pair']}: {row['model']} with {row['sku']}", "", row["tests"], ""]
            shade = row["inputs"].get("shade")
            earring = row["inputs"].get("earring")
            if earring:
                model = os.path.relpath(Path(settings.BASE_DIR) / row["inputs"]["model_file"], folder)
                product = os.path.relpath(Path(settings.BASE_DIR) / earring["image_file"], folder)
                lines += [f"Earring: {earring['title']}, {earring['metal']} {earring['form']}, {earring['length_mm']} mm, "
                          f"on {earring['feature']}.", "", state, "",
                          f"![Model]({model}) ![Earring]({product})" + result, ""]
            elif shade:
                model = os.path.relpath(Path(settings.BASE_DIR) / row["inputs"]["model_file"], folder)
                check = row.get("shade_check") or {}
                lines += [f"Shade: {shade['name']}, {shade['hex']}, {shade['finish']}, on {shade['feature']}.", "",
                          state + (f". Shade check: {check['status']}" if check.get("status") else "") +
                          (f", ΔE {check['delta']:.1f}" if check.get("delta") is not None else ""), "",
                          f"![Model]({model})" + result, ""]
            else:
                garment = os.path.relpath(Path(settings.BASE_DIR) / row["inputs"]["garment_file"], folder)
                lines += [state, "", f"![Model](../../{row['inputs']['model_file']}) ![Garment]({garment})" + result, ""]
        return "\n".join(lines) + "\n"
