"""Held-out oracle for the `fix-discount` task (agent never sees this)."""
import unittest

from pricing import final_price


class TestFinalPrice(unittest.TestCase):
    def test_basic_discount(self):
        self.assertEqual(final_price(199, 10), 179)

    def test_half_rounds_up(self):
        self.assertEqual(final_price(105, 50), 53)  # 52.5 -> 53

    def test_zero_discount(self):
        self.assertEqual(final_price(1000, 0), 1000)

    def test_clamp_over_100(self):
        self.assertEqual(final_price(1000, 150), 0)

    def test_clamp_negative(self):
        self.assertEqual(final_price(1000, -10), 1000)

    def test_returns_int(self):
        self.assertIsInstance(final_price(199, 10), int)


if __name__ == "__main__":
    unittest.main()
