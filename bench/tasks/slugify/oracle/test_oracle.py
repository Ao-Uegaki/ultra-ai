"""Held-out oracle for the `slugify` task (agent never sees this)."""
import unittest

from slugify import slugify


class TestSlugify(unittest.TestCase):
    def test_basic_punctuation(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_collapse_spaces(self):
        self.assertEqual(slugify("  Multiple   spaces "), "multiple-spaces")

    def test_non_ascii_as_separator(self):
        self.assertEqual(slugify("Café_Déjà--vu"), "caf-d-j-vu")

    def test_already_a_slug(self):
        self.assertEqual(slugify("already-a-slug"), "already-a-slug")

    def test_all_separators_empty(self):
        self.assertEqual(slugify("___"), "")

    def test_only_allowed_chars(self):
        out = slugify("A_b!!9 Z")
        self.assertTrue(all(c.isdigit() or ("a" <= c <= "z") or c == "-" for c in out))


if __name__ == "__main__":
    unittest.main()
