"""Held-out oracle for the `env-overrides` task (agent never sees this)."""
import json
import os
import tempfile
import unittest

from config import load_config

_ENV_KEYS = ("APP_PORT", "APP_DEBUG", "APP_HOST", "APP_EXTRA")


class TestEnvOverrides(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"port": 8080, "host": "localhost", "debug": False}, tmp)
        tmp.close()
        self.path = tmp.name
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        os.unlink(self.path)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_env_returns_file_values(self):
        self.assertEqual(
            load_config(self.path),
            {"port": 8080, "host": "localhost", "debug": False},
        )

    def test_env_overrides_with_prefix_and_int_coercion(self):
        os.environ["APP_PORT"] = "9090"
        cfg = load_config(self.path)
        self.assertEqual(cfg["port"], 9090)
        self.assertIsInstance(cfg["port"], int)

    def test_bool_coercion(self):
        os.environ["APP_DEBUG"] = "true"
        self.assertIs(load_config(self.path)["debug"], True)

    def test_env_wins_over_file(self):
        os.environ["APP_HOST"] = "example.com"
        self.assertEqual(load_config(self.path)["host"], "example.com")

    def test_unknown_env_var_is_ignored(self):
        os.environ["APP_EXTRA"] = "x"
        self.assertNotIn("extra", load_config(self.path))


if __name__ == "__main__":
    unittest.main()
