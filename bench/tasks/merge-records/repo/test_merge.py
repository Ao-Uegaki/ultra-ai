import unittest

from merge import merge


class TestMerge(unittest.TestCase):
    def test_last_write_wins(self):
        self.assertEqual(
            merge([{"id": 1, "a": 1}, {"id": 1, "a": 2}]),
            [{"id": 1, "a": 2}],
        )

    def test_order_preserved(self):
        self.assertEqual(
            merge([{"id": 2, "x": 1}, {"id": 1, "x": 2}]),
            [{"id": 2, "x": 1}, {"id": 1, "x": 2}],
        )

    def test_none_does_not_overwrite(self):
        self.assertEqual(
            merge([{"id": 1, "a": 5}, {"id": 1, "a": None}]),
            [{"id": 1, "a": 5}],
        )


if __name__ == "__main__":
    unittest.main()
