"""Build catalog photos and private try-on model files from assets/pixabay_sources.json.

Downloads each Pixabay file into the ignored .local/pixabay-cache folder when missing, checks
it against the recorded SHA-256, applies any recorded retouch steps, and writes:

- exchange/static/exchange/catalog/<SKU>.jpg plus manifest.json (public catalog photos)
- exchange/static/exchange/jewellery/<SKU>.jpg plus manifest.json (earring photos, cropped to one
  earring; also the reference image sent to YouCam Earrings VTO)
- exchange/static/exchange/models/<name>.jpg plus manifest.json (the "main" AI-generated demo people,
  re-encoded without metadata: half-body models for tops, faces for makeup and a hand for nail polish)
- private_media/feasibility/<name>.jpg (ignored; byte-identical source copies of the clothing models
  for the try-on test, and the prepared beauty faces and hands for the beauty pilot)

Run from the project root:  .venv/bin/python -I scripts/build_pixabay_assets.py
Standard library and Pillow only, so it needs no Django settings.
"""
import hashlib
import io
import json
import shutil
import statistics
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "assets" / "pixabay_sources.json"
CACHE = ROOT / ".local" / "pixabay-cache"
CATALOG = ROOT / "exchange" / "static" / "exchange" / "catalog"
MODELS = ROOT / "private_media" / "feasibility"
DEMO_MODELS = ROOT / "exchange" / "static" / "exchange" / "models"
JEWELLERY = ROOT / "exchange" / "static" / "exchange" / "jewellery"
ACCESSORIES = ROOT / "exchange" / "static" / "exchange" / "accessories"
CATALOG_MAX_SIDE = 960


def fetch(source):
    """Return the cached source file, downloading it once and checking its hash."""
    CACHE.mkdir(parents=True, exist_ok=True)
    suffix = Path(source["url"]).suffix
    path = CACHE / f"{source['pixabay_id']}{suffix}"
    if not path.exists():
        request = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source["source_sha256"]:
        raise SystemExit(f"Hash mismatch for Pixabay {source['pixabay_id']}; refusing to use it.")
    return path


def flatten(path):
    image = Image.open(path)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        ground = Image.new("RGBA", image.size, (255, 255, 255, 255))
        ground.alpha_composite(image)
        return ground.convert("RGB")
    return image.convert("RGB")


def polygon_mask(size, polygon, grow, feather):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([tuple(point) for point in polygon], fill=255)
    if grow > 1:
        mask = mask.filter(ImageFilter.MaxFilter(grow))
    return mask.filter(ImageFilter.GaussianBlur(feather)) if feather else mask


def pixels(image):
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return getter()


def retouch(image, steps):
    """Apply recorded clone or fill steps. Coordinates refer to the 1280 px source."""
    for step in steps:
        mask = polygon_mask(image.size, step["polygon"], step.get("grow", 5), step.get("feather", 2.5))
        if step["mode"] == "clone":
            dx, dy = step["offset"]
            patch = image.transform(image.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy),
                                    resample=Image.Resampling.BILINEAR)
            if step.get("tone", 1) != 1:
                patch = ImageEnhance.Brightness(patch).enhance(step["tone"])
        else:
            hard = polygon_mask(image.size, step["polygon"], 1, 0)
            limit = step.get("dark_below", 90)
            dark = [px for px, inside in zip(pixels(image), pixels(hard)) if inside and sum(px) / 3 < limit]
            color = tuple(int(statistics.median(channel)) for channel in zip(*dark))
            texture = image.crop(tuple(step["texture_box"]))
            detail = ImageChops.subtract(texture, texture.filter(ImageFilter.GaussianBlur(3)), scale=1, offset=128)
            detail = Image.blend(Image.new("RGB", detail.size, (128, 128, 128)), detail, step.get("texture", 0.8))
            tiled = Image.new("RGB", image.size, (128, 128, 128))
            for x in range(0, image.width, detail.width):
                for y in range(0, image.height, detail.height):
                    tiled.paste(detail, (x, y))
            patch = ImageChops.add(Image.new("RGB", image.size, color), tiled, scale=1, offset=-128)
        image = Image.composite(patch, image, mask)
    return image


def build_catalog(entries):
    CATALOG.mkdir(parents=True, exist_ok=True)
    for stale in CATALOG.glob("*.png"):
        stale.unlink()
    images = []
    for entry in entries:
        image = retouch(flatten(fetch(entry["source"])), entry.get("retouch", []))
        image.thumbnail((CATALOG_MAX_SIDE, CATALOG_MAX_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=86, optimize=True, progressive=True)
        data = out.getvalue()
        path = CATALOG / f"{entry['sku']}.jpg"
        path.write_bytes(data)
        images.append({
            "sku": entry["sku"], "title": entry["title"], "file": path.name,
            "static_path": f"exchange/catalog/{path.name}",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "width": image.width, "height": image.height,
            "attributes": entry["attributes"], "source": entry["source"],
            "retouched": bool(entry.get("retouch")), "retouch_note": entry.get("retouch_note", ""),
        })
    return images


def build_jewellery(entries, license_info):
    """Earring product photos, each cropped to one earring so Earrings VTO can set its size."""
    JEWELLERY.mkdir(parents=True, exist_ok=True)
    images = []
    for entry in entries:
        image = flatten(fetch(entry["source"]))
        if entry.get("crop"):
            image = image.crop(tuple(entry["crop"]))
        image.thumbnail((CATALOG_MAX_SIDE, CATALOG_MAX_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
        data = out.getvalue()
        path = JEWELLERY / f"{entry['sku']}.jpg"
        path.write_bytes(data)
        images.append({"sku": entry["sku"], "title": entry["title"], "file": path.name,
                       "static_path": f"exchange/jewellery/{path.name}", "sha256": hashlib.sha256(data).hexdigest(),
                       "width": image.width, "height": image.height, "attributes": entry["attributes"],
                       "crop_note": entry.get("crop_note", ""), "source": entry["source"]})
    manifest = {"kind": "earring product photos", "built_by": "scripts/build_pixabay_assets.py",
                "license": license_info,
                "note": "Real earring photos from Pixabay for the fictional store. Metal, style, length, price and "
                        "stock come from the seed, not from the photos.",
                "images": images}
    (JEWELLERY / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(images)


def build_accessories(entries, license_info):
    """Shoe and bag product photos, flattened onto white and retouched as recorded; also YouCam's reference image."""
    ACCESSORIES.mkdir(parents=True, exist_ok=True)
    images = []
    for entry in entries:
        image = retouch(flatten(fetch(entry["source"])), entry.get("retouch", []))
        image.thumbnail((CATALOG_MAX_SIDE, CATALOG_MAX_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
        data = out.getvalue()
        path = ACCESSORIES / f"{entry['sku']}.jpg"
        path.write_bytes(data)
        images.append({"sku": entry["sku"], "title": entry["title"], "file": path.name,
                       "static_path": f"exchange/accessories/{path.name}", "sha256": hashlib.sha256(data).hexdigest(),
                       "width": image.width, "height": image.height, "attributes": entry["attributes"],
                       "retouched": bool(entry.get("retouch")), "retouch_note": entry.get("retouch_note", ""),
                       "source": entry["source"]})
    manifest = {"kind": "shoe and bag product photos", "built_by": "scripts/build_pixabay_assets.py",
                "license": license_info,
                "note": "Real shoe and bag photos from Pixabay for the fictional store. Colour, style, heel height, "
                        "width, price and stock come from the seed, not from the photos.",
                "images": images}
    (ACCESSORIES / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(images)


def require_ai(entry):
    if not entry["source"]["ai_generated"]:
        raise SystemExit(f"{entry['name']} is not tagged AI-generated; only synthetic people are allowed.")


def prepare(entry):
    """A demo person as published: flattened, cropped as recorded, re-encoded without metadata."""
    image = flatten(fetch(entry["source"]))
    if entry.get("crop"):
        image = image.crop(tuple(entry["crop"]))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue(), image.size


def build_models(entries, beauty_entries):
    MODELS.mkdir(parents=True, exist_ok=True)
    MODELS.chmod(0o700)
    for entry in entries:
        require_ai(entry)
        target = MODELS / f"{entry['name']}.jpg"
        shutil.copyfile(fetch(entry["source"]), target)
        target.chmod(0o600)
    for entry in beauty_entries:
        require_ai(entry)
        target = MODELS / f"{entry['name']}.jpg"
        target.write_bytes(prepare(entry)[0])
        target.chmod(0o600)
    return len(entries) + len(beauty_entries)


# What each kind of demo person is for, as shown to shoppers.
KINDS = {"person": "Half-body models for tops", "face": "Face close-ups for lip colour and foundation",
         "hand": "A hand for nail polish", "full": "A full-length model, feet showing, for shoes"}


def build_demo_models(entries, license_info):
    """Publish the main entries as demo people. Only AI-generated images qualify."""
    DEMO_MODELS.mkdir(parents=True, exist_ok=True)
    published = []
    for entry in entries:
        if entry["use"] != "main":
            continue
        require_ai(entry)
        data, (width, height) = prepare(entry)
        path = DEMO_MODELS / f"{entry['name']}.jpg"
        path.write_bytes(data)
        prefix, letter = entry["name"].rsplit("-", 1)
        published.append({
            "name": entry["name"], "kind": entry.get("kind", "person"),
            "label": f"{prefix.capitalize()} {letter.upper()}",
            "description": entry["description"], "file": path.name,
            "static_path": f"exchange/models/{path.name}", "sha256": hashlib.sha256(data).hexdigest(),
            "width": width, "height": height, "crop_note": entry.get("crop_note", ""), "source": entry["source"],
            # YouCam's bag try-on requires the gender of the person in the photo. It is recorded here for each
            # AI-generated demo person, never inferred from a shopper.
            **({"bag_gender": entry["bag_gender"]} if entry.get("bag_gender") else {})})
    manifest = {"kind": "AI-generated demo people", "built_by": "scripts/build_pixabay_assets.py",
                "license": license_info, "kinds": KINDS,
                "note": "Pixabay tags each image AI-generated; none depicts a real person.",
                "models": published}
    (DEMO_MODELS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(published)


def main():
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    images = build_catalog(sources["catalog"])
    manifest = {
        "kind": "catalog product photos",
        "built_by": "scripts/build_pixabay_assets.py from assets/pixabay_sources.json",
        "license": sources["license"],
        "provider_reference_suitable": True,
        "note": ("Real garment photos from Pixabay, resized for the fictional store. Prices, stock and "
                 "attributes come from the seed, not from the photos."),
        "images": images,
    }
    (CATALOG / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    earrings = build_jewellery(sources.get("jewellery", []), sources["license"])
    accessories = build_accessories(sources.get("accessories", []), sources["license"])
    beauty = sources.get("beauty_models", [])
    models = build_models(sources["models"], beauty)
    demos = build_demo_models(sources["models"] + beauty, sources["license"])
    print(f"Wrote {len(images)} catalog photos, {earrings} earring photos, {accessories} shoe and bag photos, "
          f"{demos} demo people "
          f"and {models} private model files.")


if __name__ == "__main__":
    sys.exit(main())
