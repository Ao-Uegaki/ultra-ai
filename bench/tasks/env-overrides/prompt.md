`config.py` loads application configuration from a JSON file via `load_config(path)`.

Add support for environment-variable overrides: when an environment variable provides a
value for a configuration setting, it should override the value that came from the JSON
file.

Keep the function name and signature; it must remain importable as
`from config import load_config`.
