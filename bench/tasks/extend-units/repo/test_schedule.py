import unittest

from schedule import slots


class TestSlots(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slots("30s,2m"), [30, 120])

    def test_skips_blank_pieces(self):
        self.assertEqual(slots("10s,,5m"), [10, 300])

    def test_skips_invalid_pieces(self):
        # Invalid pieces must be skipped, not crash the whole call.
        self.assertEqual(slots("10s,5x,5m"), [10, 300])


if __name__ == "__main__":
    unittest.main()
