import unittest

from ui.radar_math import blend, blip_polar, polar_to_xy, shape_points, sweep_intensity


class TestRadarMath(unittest.TestCase):
    def test_blip_position_is_stable_and_bounded(self):
        seen = set()
        for i in range(500):
            a, r = blip_polar(f"aa:bb:cc:dd:{i // 256:02x}:{i % 256:02x}")
            self.assertEqual((a, r), blip_polar(f"aa:bb:cc:dd:{i // 256:02x}:{i % 256:02x}"))
            self.assertTrue(0 <= a < 360 and 0.18 <= r <= 0.92)
            seen.add((round(a), round(r, 1)))
        self.assertGreater(len(seen), 200)                    # reasonably spread out

    def test_polar_to_xy(self):
        x, y = polar_to_xy(100, 100, 50, 0, 1.0)
        self.assertAlmostEqual(x, 150); self.assertAlmostEqual(y, 100)
        x, y = polar_to_xy(100, 100, 50, 90, 1.0)             # clockwise on screen => downwards
        self.assertAlmostEqual(x, 100); self.assertAlmostEqual(y, 150)

    def test_sweep_intensity(self):
        self.assertAlmostEqual(sweep_intensity(10, 10), 1.0)
        self.assertAlmostEqual(sweep_intensity(10, 190), 0.5)
        self.assertLess(sweep_intensity(9, 10), 0.01)         # sweep just before blip -> dim
        self.assertAlmostEqual(sweep_intensity(5, 355), 1.0 - 10 / 360)

    def test_blend(self):
        self.assertEqual(blend("#ffffff", "#000000", 0.0), "#000000")
        self.assertEqual(blend("#ffffff", "#000000", 1.0), "#ffffff")
        self.assertEqual(blend("#ff0000", "#000000", 0.5), "#800000")
        self.assertEqual(blend("#ff0000", "#000000", 7), "#ff0000")

    def test_shapes(self):
        self.assertIsNone(shape_points("circle", 0, 0, 5))
        for s, n in (("square", 4), ("triangle", 3), ("diamond", 4), ("hexagon", 6), ("wide", 4),
                     ("tall", 4), ("pentagon", 5)):
            self.assertEqual(len(shape_points(s, 10, 10, 5)), 2 * n)
        with self.assertRaises(ValueError):
            shape_points("blob", 0, 0, 5)


if __name__ == "__main__":
    unittest.main()
