import io
import unittest
from types import SimpleNamespace

from PIL import Image, ImageDraw

from exchange.size_check import (check_size, earring_anchor, rendered_height, resize_earrings, resize_factor,
                                 resized_check)


def photo(draw=None, size=(400, 500)):
    image = Image.new("RGB", size, (200, 190, 180))
    if draw:
        draw(ImageDraw.Draw(image))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class SizeCheckTests(unittest.TestCase):
    def test_the_rendered_height_is_the_changed_area_at_each_ear_averaged(self):
        before = photo()
        both = photo(lambda d: (d.rectangle((40, 200, 60, 300), fill="black"), d.rectangle((340, 200, 360, 320), fill="black")))
        self.assertEqual(rendered_height(before, both), 111)
        one = photo(lambda d: d.rectangle((340, 200, 360, 260), fill="black"))
        self.assertEqual(rendered_height(before, one), 61)

    def test_no_change_a_speck_or_a_redrawn_photo_gives_no_height(self):
        before = photo()
        self.assertIsNone(rendered_height(before, before))
        self.assertIsNone(rendered_height(before, photo(lambda d: d.rectangle((340, 200, 343, 203), fill="black"))))
        self.assertIsNone(rendered_height(before, photo(lambda d: d.rectangle((0, 0, 400, 400), fill="black"))))
        self.assertIsNone(rendered_height(None, before))

    def test_the_anchor_is_the_top_centre_of_the_earring_in_its_photo(self):
        product = photo(lambda d: d.ellipse((100, 50, 300, 450), fill=(30, 60, 200)), size=(400, 500))
        self.assertEqual(earring_anchor(product), [200, 50])
        self.assertIsNone(earring_anchor(photo()))

    def test_sizes_are_compared_with_the_pair_bought(self):
        bought = SimpleNamespace(pk=1, length_mm=58)
        drop = SimpleNamespace(pk=2, length_mm=38)
        self.assertEqual(check_size(bought, 142, bought, 142).status, "size-reference")
        self.assertEqual(check_size(drop, 93, bought, 142).status, "size-close")
        differs = check_size(drop, 140, bought, 142)
        self.assertEqual(differs.status, "size-differs")
        self.assertTrue(differs.note.startswith("Size check: renders 50% longer than its length implies."), differs.note)
        self.assertTrue(check_size(drop, 60, bought, 142).note.startswith("Size check: renders 36% shorter than"))
        self.assertEqual(check_size(drop, None, bought, 142).status, "unchecked")
        self.assertEqual(check_size(drop, 93, None, None).status, "unchecked")


if __name__ == "__main__":
    unittest.main()


class ResizeTests(unittest.TestCase):
    def test_each_earring_is_scaled_from_where_it_hangs_and_nothing_else_moves(self):
        before = photo(lambda d: d.rectangle((150, 100, 250, 150), fill=(90, 60, 50)))
        after = photo(lambda d: (d.rectangle((150, 100, 250, 150), fill=(90, 60, 50)),
                                 d.rectangle((40, 200, 60, 320), fill="black"), d.rectangle((340, 200, 360, 320), fill="black")))
        out = resize_earrings(before, after, 0.5)
        self.assertAlmostEqual(rendered_height(before, out), 61, delta=1)
        image = Image.open(io.BytesIO(out)).convert("RGB")
        # Still hanging from the same point at each ear, the lower half gone, the face untouched.
        self.assertLess(sum(image.getpixel((50, 205))), 120)
        self.assertLess(sum(image.getpixel((350, 205))), 120)
        self.assertGreater(sum(image.getpixel((50, 300))), 500)
        self.assertEqual(image.getpixel((200, 125))[:1], Image.open(io.BytesIO(after)).convert("RGB").getpixel((200, 125))[:1])

    def test_a_larger_earring_grows_from_the_top(self):
        before = photo()
        after = photo(lambda d: d.rectangle((340, 200, 360, 260), fill="black"))
        self.assertAlmostEqual(rendered_height(before, resize_earrings(before, after, 2.0)), 121, delta=1)

    def test_no_resize_when_the_render_is_unreadable_or_the_factor_is_absurd(self):
        before = photo()
        after = photo(lambda d: d.rectangle((340, 200, 360, 320), fill="black"))
        self.assertIsNone(resize_earrings(before, after, 0.2))
        self.assertIsNone(resize_earrings(before, after, 3))
        self.assertIsNone(resize_earrings(before, before, 0.5))
        self.assertIsNone(resize_earrings(None, after, 0.5))

    def test_the_factor_makes_the_earring_stand_to_the_pair_bought_as_its_length_does(self):
        bought = SimpleNamespace(pk=1, length_mm=58)
        crystal = SimpleNamespace(pk=2, length_mm=38)
        self.assertAlmostEqual(resize_factor(crystal, 140, bought, 142), 142 * 38 / 58 / 140)
        self.assertIsNone(resize_factor(crystal, None, bought, 142))
        self.assertIsNone(resize_factor(crystal, 140, None, 142))

    def test_the_resized_note_says_what_youcam_drew(self):
        check = check_size(SimpleNamespace(pk=2, length_mm=38), 140, SimpleNamespace(pk=1, length_mm=58), 142)
        self.assertEqual(resized_check(check).status, "size-resized")
        self.assertEqual(resized_check(check).note, "Size check: YouCam drew it 50% longer; Rechoice resized it to "
                                                    "its catalog length, in proportion to the pair bought.")
