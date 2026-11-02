"""Resettable fictional inventory. No customer or Tulip data."""
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management.base import BaseCommand
from django.db.models import ProtectedError
from django.utils import timezone
from exchange.models import OrderLine, PolicySnapshot, ProductVariant


TOPS = [
    # sku, title, price in cents, pattern level, color, sleeve, neckline, stock.
    # Photos: exchange/static/exchange/catalog/<sku>.jpg, built from assets/pixabay_sources.json.
    ("ORIGINAL", "Midnight Floral Blouse", 4000, 2, "navy", "long", "collared", 7),
    ("BOLD-01", "Fair Isle Knit Sweater", 4400, 2, "brown", "long", "crew", 6),
    ("BOLD-02", "Sunset Stripe Sweater", 4500, 2, "navy", "long", "crew", 2),
    ("CALM-01", "Indigo Denim Shirt", 4500, 0, "blue", "long", "collared", 8),
    ("CALM-02", "Khaki Utility Shirt", 4900, 0, "sand", "long", "collared", 6),
    ("CALM-03", "Heather Mauve Knit", 4600, 0, "mauve", "long", "crew", 0),
    ("CALM-04", "Sage Knit Top", 4300, 0, "green", "long", "crew", 3),
    ("CALM-07", "Ribbed Turtleneck", 4200, 0, "red", "long", "turtleneck", 2),
    ("OVER-01", "Burgundy Knit Top", 5400, 0, "burgundy", "long", "crew", 3),
    ("SHORT-01", "Black Crew Tee", 4200, 0, "black", "short", "crew", 5),
    ("SHORT-02", "White Cotton Tee", 4300, 0, "white", "short", "crew", 7),
    ("SHORT-03", "Sky Blue Tee", 3900, 0, "blue", "short", "crew", 8),
    ("SHORT-04", "Red Crew Tee", 4100, 0, "red", "short", "crew", 4),
]
# Other sizes of the purchased blouse, for a shopper whose item does not fit: sku, size, stock.
ORIGINAL_SIZES = [("ORIGINAL-S", "S", 3), ("ORIGINAL-L", "L", 0), ("ORIGINAL-XL", "XL", 2)]

# Fictional beauty lines. Each shade is a variant whose colour is a reviewed hex fact; there are no
# product photos, so shades show as swatches. Line: (code, title, category, price in cents, finish).
BEAUTY_LINES = {
    "LIP-VELVET": ("Velvet Matte Lipstick", "lip", 1800, "matte"),
    "LIP-SATIN": ("Satin Cream Lipstick", "lip", 2000, "cream"),
    "FDN-VEIL": ("Skin Veil Foundation", "foundation", 3200, "natural"),
    "NAIL-GLOSS": ("Gloss Lacquer Nail Polish", "nail", 1200, "gloss"),
    "HAIR-TONE": ("Tone Permanent Hair Colour", "hair", 1400, "permanent"),
}
SHADES = [
    # sku, line, shade name, hex, stock.
    ("LIP-VELVET-210", "LIP-VELVET", "210 Nude Rose", "#c98a84", 5),
    ("LIP-VELVET-220", "LIP-VELVET", "220 Rosewood", "#a04a55", 4),
    ("LIP-VELVET-225", "LIP-VELVET", "225 Brick", "#9a3b2e", 3),
    ("LIP-VELVET-228", "LIP-VELVET", "228 Cinnamon", "#a65a3f", 4),
    ("LIP-VELVET-230", "LIP-VELVET", "230 Terracotta", "#b5532f", 6),
    ("LIP-VELVET-235", "LIP-VELVET", "235 Coral", "#e0654a", 5),
    ("LIP-VELVET-240", "LIP-VELVET", "240 Mauve", "#9c5a6e", 4),
    ("LIP-VELVET-250", "LIP-VELVET", "250 Berry", "#7e2a44", 0),
    ("LIP-SATIN-10", "LIP-SATIN", "10 Petal", "#c4707f", 4),
    ("LIP-SATIN-20", "LIP-SATIN", "20 Raspberry", "#a64d63", 3),
    ("LIP-SATIN-30", "LIP-SATIN", "30 Plum", "#7b3552", 2),
    ("LIP-SATIN-40", "LIP-SATIN", "40 Peach", "#e08e6d", 3),
    ("FDN-VEIL-110C", "FDN-VEIL", "110C Fair Cool", "#f0cfc4", 4),
    ("FDN-VEIL-110W", "FDN-VEIL", "110W Fair Warm", "#f1d4b5", 3),
    ("FDN-VEIL-210C", "FDN-VEIL", "210C Light Cool", "#dcb2a3", 4),
    ("FDN-VEIL-210N", "FDN-VEIL", "210N Light Neutral", "#ddb593", 5),
    ("FDN-VEIL-220W", "FDN-VEIL", "220W Light Warm", "#d9ae7f", 6),
    ("FDN-VEIL-230W", "FDN-VEIL", "230W Medium Warm", "#cfa06d", 4),
    ("FDN-VEIL-310C", "FDN-VEIL", "310C Tan Cool", "#b8877a", 3),
    ("FDN-VEIL-310N", "FDN-VEIL", "310N Tan Neutral", "#b98a68", 3),
    ("FDN-VEIL-320W", "FDN-VEIL", "320W Tan Warm", "#b5844f", 2),
    ("FDN-VEIL-410N", "FDN-VEIL", "410N Deep Neutral", "#7e5a43", 3),
    ("NAIL-GLOSS-01", "NAIL-GLOSS", "01 Coral Crush", "#ef6f5a", 6),
    ("NAIL-GLOSS-02", "NAIL-GLOSS", "02 Cherry", "#b3203a", 5),
    ("NAIL-GLOSS-03", "NAIL-GLOSS", "03 Blush Pink", "#e8a3b0", 4),
    ("NAIL-GLOSS-04", "NAIL-GLOSS", "04 Lilac", "#b9a3d6", 4),
    ("NAIL-GLOSS-05", "NAIL-GLOSS", "05 Navy", "#2b3a67", 3),
    ("NAIL-GLOSS-06", "NAIL-GLOSS", "06 Nude", "#d9b8a0", 5),
    ("NAIL-GLOSS-07", "NAIL-GLOSS", "07 Tangerine", "#f2843b", 4),
    # Hair colour shades use the trade's level.tone numbering: 1 is black and 10 is lightest blonde; .1 is ash
    # (cool) and .3 is golden (warm). The line is blondes and browns only: by hue a red counts as cooler than a
    # golden blonde, which fits lipstick but not what "too brassy" means for hair.
    ("HAIR-TONE-4.0", "HAIR-TONE", "4.0 Medium Brown", "#4a3328", 4),
    ("HAIR-TONE-6.0", "HAIR-TONE", "6.0 Dark Blonde", "#8a6a48", 5),
    ("HAIR-TONE-6.1", "HAIR-TONE", "6.1 Dark Ash Blonde", "#7a6b62", 4),
    ("HAIR-TONE-7.1", "HAIR-TONE", "7.1 Ash Blonde", "#9f8572", 5),
    ("HAIR-TONE-7.3", "HAIR-TONE", "7.3 Golden Blonde", "#c08f45", 6),
    ("HAIR-TONE-8.3", "HAIR-TONE", "8.3 Light Golden Blonde", "#d8ad62", 4),
    ("HAIR-TONE-9.1", "HAIR-TONE", "9.1 Very Light Ash Blonde", "#d3bcaa", 3),
    ("NAIL-GLOSS-08", "NAIL-GLOSS", "08 Berry", "#8e2a52", 2),
]
# Fictional earrings, each pair its own style: sku, title, metal, style, drop length in mm, price, stock.
# Photos come from assets/pixabay_sources.json; the facts here are the store's, not the photos'.
# sku, title, metal, form, length in mm, the length below the hook that Earrings VTO is told, price, stock.
# The crystal drop's photo shows a lever-back hook over the top 38% of its height; the others show no hook.
EARRINGS = [
    ("EAR-PEARL-G58", "Baroque Pearl Drops", "gold", "drop", 58, 58, 4200, 3),
    ("EAR-CRYSTAL-S38", "Blue Crystal Drops", "silver", "drop", 38, 24, 4400, 4),
    ("EAR-TOPAZ-S12", "Blue Topaz Studs", "silver", "stud", 12, 12, 4200, 5),
    ("EAR-ROPE-G10", "Pearl Rope Studs", "gold", "stud", 10, 10, 4200, 2),
]
# sku, title, metal, form, length in mm, price, stock. Necklace VTO draws the whole necklace, so no scale is sent.
NECKLACES = [
    ("NECK-AMETHYST-S55", "Amethyst Y Chain", "silver", "drop", 550, 4800, 3),
    ("NECK-LAPIS-S45", "Lapis Collar", "silver", "collar", 450, 5200, 2),
    ("NECK-BLOOD-S40", "Bloodstone Bib", "silver", "collar", 400, 4800, 4),
    ("NECK-GEM-S50", "Teal Gem Drop", "silver", "drop", 500, 4900, 3),
    ("NECK-LEAF-G40", "Rose Gold Leaf Pendant", "gold", "drop", 400, 4800, 5),
]
# Shoes and bags: sku, title, colour family, style, heel height or width in mm, price, stock. Every alternative costs
# at least the credit for the item bought, so none needs staff review.
ACCESSORIES = [
    ("shoes", "SHOE-RED-BOOT", "Red Heeled Ankle Boots", "bright", "ankle boot", 100, 7800, 3),
    ("shoes", "SHOE-CREAM-PEEP", "Cream Block-Heel Peep-Toes", "neutral", "peep-toe", 50, 7800, 4),
    ("shoes", "SHOE-NUDE-PUMP", "Nude Suede Pumps", "neutral", "pump", 110, 7900, 2),
    ("shoes", "SHOE-GOLD-BROGUE", "Metallic Platform Brogues", "bright", "brogue", 30, 8200, 3),
    ("bag", "BAG-RED-HOBO", "Red Slouch Shoulder Bag", "bright", "shoulder bag", 420, 6400, 3),
    ("bag", "BAG-YELLOW-CROSS", "Yellow Ring Crossbody", "bright", "crossbody", 200, 6400, 5),
    ("bag", "BAG-CORAL-TOTE", "Coral Mini Tote", "bright", "tote", 260, 6600, 4),
    ("bag", "BAG-TAN-MESSENGER", "Tan Leather Messenger", "neutral", "messenger", 300, 6800, 2),
]
# More lines in the same fictional order: token, sku, paid cents, opened.
ORDER_ITEMS = [("DEMO-ORDER-001-LIP", "LIP-VELVET-230", 1800, True),
               ("DEMO-ORDER-001-FDN", "FDN-VEIL-220W", 3200, True),
               ("DEMO-ORDER-001-NAIL", "NAIL-GLOSS-01", 1200, False),
               ("DEMO-ORDER-001-EAR", "EAR-PEARL-G58", 4200, False),
               ("DEMO-ORDER-001-NECK", "NECK-AMETHYST-S55", 4800, False),
               ("DEMO-ORDER-001-HAIR", "HAIR-TONE-7.3", 1400, True),
               ("DEMO-ORDER-001-SHOE", "SHOE-RED-BOOT", 7800, False),
               ("DEMO-ORDER-001-BAG", "BAG-RED-HOBO", 6400, False)]


class Command(BaseCommand):
    help = "Seed fictional USD products, order and policy; never touches real commerce systems."

    def handle(self, *args, **options):
        references = Path(settings.BASE_DIR) / "assets/reference_colours.json"
        colours = json.loads(references.read_text())["colours"] if references.exists() else {}
        rows = [(top[0], top[0], "M", top) for top in TOPS]
        original = next(top for top in TOPS if top[0] == "ORIGINAL")
        rows += [(sku, "ORIGINAL", size, (sku, *original[1:7], stock)) for sku, size, stock in ORIGINAL_SIZES]
        for sku, style, size, (_, title, price, pattern, color, sleeve, neckline, stock) in rows:
            image = f"exchange/catalog/{style}.jpg"
            ProductVariant.objects.update_or_create(
                retailer_id="secondlook-demo", sku=sku,
                defaults={
                    "style_code": style, "title": title, "category": "tops", "size_label": size,
                    "currency": "USD", "price_minor": price, "stock_qty": stock,
                    "pattern_level": pattern, "color_tag": color, "sleeve": sleeve,
                    "neckline": neckline, "image_asset_id": image if finders.find(image) else "",
                    "reference_colour": colours.get(style, {}).get("hex", ""),
                    "reference_colour_status": colours.get(style, {}).get("status", ""),
                    "verified_at": timezone.now(), "version": 1,
                })
        for sku, line_code, shade_name, shade_hex, stock in SHADES:
            title, category, price, finish = BEAUTY_LINES[line_code]
            ProductVariant.objects.update_or_create(
                retailer_id="secondlook-demo", sku=sku,
                defaults={
                    "style_code": line_code, "title": f"{title}, {shade_name}", "category": category,
                    "size_label": "", "currency": "USD", "price_minor": price, "stock_qty": stock,
                    "pattern_level": None, "color_tag": "", "sleeve": "", "neckline": "", "image_asset_id": "",
                    "product_line": title, "shade_name": shade_name, "shade_hex": shade_hex, "finish": finish,
                    "reference_colour": shade_hex, "reference_colour_status": "reviewed",
                    "verified_at": timezone.now(), "version": 1,
                })
        jewellery = [("earrings", *row) for row in EARRINGS] + [
            ("necklace", sku, title, metal, form, length, None, price, stock)
            for sku, title, metal, form, length, price, stock in NECKLACES]
        for category, sku, title, metal, form, length, scale, price, stock in jewellery:
            image = f"exchange/jewellery/{sku}.jpg"
            ProductVariant.objects.update_or_create(
                retailer_id="secondlook-demo", sku=sku,
                defaults={
                    "style_code": sku, "title": title, "category": category, "size_label": "",
                    "currency": "USD", "price_minor": price, "stock_qty": stock, "pattern_level": None,
                    "color_tag": metal, "sleeve": "", "neckline": "", "form": form, "length_mm": length, "scale_mm": scale,
                    "image_asset_id": image if finders.find(image) else "", "verified_at": timezone.now(),
                    "version": 1,
                })
        for category, sku, title, family, form, size, price, stock in ACCESSORIES:
            image = f"exchange/accessories/{sku}.jpg"
            ProductVariant.objects.update_or_create(
                retailer_id="secondlook-demo", sku=sku,
                defaults={
                    "style_code": sku, "title": title, "category": category, "size_label": "",
                    "currency": "USD", "price_minor": price, "stock_qty": stock, "pattern_level": None,
                    "color_tag": family, "sleeve": "", "neckline": "", "form": form, "length_mm": size, "scale_mm": None,
                    "image_asset_id": image if finders.find(image) else "", "verified_at": timezone.now(),
                    "version": 1,
                })
        current = ({row[0] for row in rows} | {shade[0] for shade in SHADES} | {pair[0] for pair in EARRINGS}
                   | {item[1] for item in ACCESSORIES}
                   | {item[0] for item in NECKLACES})
        for stale in ProductVariant.objects.filter(retailer_id="secondlook-demo").exclude(sku__in=current):
            try:
                stale.delete()
            except ProtectedError:
                # Still referenced by an old quote or preview; keep the row but take it off sale.
                ProductVariant.objects.filter(pk=stale.pk).update(stock_qty=0)
        PolicySnapshot.objects.update_or_create(
            retailer_id="secondlook-demo", version=1,
            defaults={"allowed_category": "tops", "categories": ["tops", "lip", "foundation", "nail", "earrings", "necklace", "hair", "shoes", "bag"],
                      "opened_same_line": True, "expires_at": timezone.now() + timedelta(days=30),
                      "fee_minor": 0, "exchange_allowed": True})
        OrderLine.objects.update_or_create(
            token="DEMO-ORDER-001",
            defaults={"fictional_order_id": "SL-DEMO-1001", "retailer_id": "secondlook-demo",
                      "variant": ProductVariant.objects.get(retailer_id="secondlook-demo", sku="ORIGINAL"),
                      "quantity": 1, "paid_minor": 4000, "eligible_credit_minor": 4000,
                      "currency": "USD", "simple_pricing": True, "policy_version": 1})
        for token, sku, paid, opened in ORDER_ITEMS:
            OrderLine.objects.update_or_create(
                token=token,
                defaults={"fictional_order_id": "SL-DEMO-1001", "retailer_id": "secondlook-demo",
                          "variant": ProductVariant.objects.get(retailer_id="secondlook-demo", sku=sku),
                          "quantity": 1, "paid_minor": paid, "eligible_credit_minor": paid, "currency": "USD",
                          "simple_pricing": True, "policy_version": 1, "opened": opened})
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(TOPS)} fictional tops in {len(rows)} variants, {len(SHADES)} beauty shades, "
            f"{len(NECKLACES)} necklaces, "
            f"{len(EARRINGS)} earrings, {len(ACCESSORIES)} shoes and bags, "
            f"one order with {1 + len(ORDER_ITEMS)} items and one policy."))
