"""Held-out oracle for the `extend-units` task (agent never sees this)."""
import unittest

from durations import parse_duration
from schedule import slots


class TestParseDuration(unittest.TestCase):
    def test_seconds_and_minutes(self):
        self.assertEqual(parse_duration("10s"), 10)
        self.assertEqual(parse_duration("5m"), 300)

    def test_hours(self):
        self.assertEqual(parse_duration("2h"), 7200)

    def test_days(self):
        self.assertEqual(parse_duration("1d"), 86400)

    def test_unknown_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("5x")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("bogus")


class TestRipple(unittest.TestCase):
    def test_slots_skips_invalid(self):
        # The caller must keep skipping invalid pieces even though
        # parse_duration now raises on them.
        self.assertEqual(slots("10s,5x,5m"), [10, 300])

    def test_slots_skips_blank(self):
        self.assertEqual(slots("10s,,5m"), [10, 300])

    def test_slots_with_new_units(self):
        self.assertEqual(slots("2h,1d"), [7200, 86400])


if __name__ == "__main__":
    unittest.main()
