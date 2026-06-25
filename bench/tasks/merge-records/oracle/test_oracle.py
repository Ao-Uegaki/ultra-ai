"""Held-out oracle for the `merge-records` task (agent never sees this)."""
import unittest

from merge import merge


class TestMerge(unittest.TestCase):
    def test_last_write_wins(self):
        self.assertEqual(merge([{"id": 1, "a": 1}, {"id": 1, "a": 2}]), [{"id": 1, "a": 2}])

    def test_order_preserved(self):
        self.assertEqual(
            merge([{"id": 2, "x": 1}, {"id": 1, "x": 2}]),
            [{"id": 2, "x": 1}, {"id": 1, "x": 2}],
        )

    def test_none_does_not_overwrite(self):
        self.assertEqual(merge([{"id": 1, "a": 5}, {"id": 1, "a": None}]), [{"id": 1, "a": 5}])

    def test_value_overwrites_existing_none(self):
        # A real value SHOULD replace an existing None.
        self.assertEqual(merge([{"id": 1, "a": None}, {"id": 1, "a": 7}]), [{"id": 1, "a": 7}])

    def test_drops_records_without_id(self):
        self.assertEqual(merge([{"a": 1}, {"id": 1, "a": 2}]), [{"id": 1, "a": 2}])

    def test_empty(self):
        self.assertEqual(merge([]), [])

    def test_field_only_in_later_record(self):
        self.assertEqual(
            merge([{"id": 1, "a": 1}, {"id": 1, "b": 2}]),
            [{"id": 1, "a": 1, "b": 2}],
        )

    def test_three_ids_order_and_update(self):
        self.assertEqual(
            merge([{"id": 3, "v": 1}, {"id": 1, "v": 2}, {"id": 2, "v": 3}, {"id": 1, "v": 9}]),
            [{"id": 3, "v": 1}, {"id": 1, "v": 9}, {"id": 2, "v": 3}],
        )


if __name__ == "__main__":
    unittest.main()
