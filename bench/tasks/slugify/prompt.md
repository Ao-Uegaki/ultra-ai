Implement `slugify(text: str) -> str` in `slugify.py`.

Rules:
- Lowercase the text.
- Replace any run of characters that are NOT in `[a-z0-9]` with a single hyphen `-`
  (treat everything else — spaces, punctuation, underscores, non-ASCII letters — as a separator).
- Strip leading and trailing hyphens.
- The result must contain only the characters `[a-z0-9-]`.

Examples:
- `"Hello, World!"`        -> `"hello-world"`
- `"  Multiple   spaces "` -> `"multiple-spaces"`
- `"Café_Déjà--vu"`        -> `"caf-d-j-vu"`
- `"already-a-slug"`       -> `"already-a-slug"`
- `"___"`                  -> `""`

Keep the function name and file name unchanged; it must be importable as `from slugify import slugify`.
