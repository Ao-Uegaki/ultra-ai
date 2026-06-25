`durations.py` has `parse_duration(s)` which parses a duration string like `'10s'` or
`'5m'` into a number of seconds. Right now it only understands seconds (`s`) and
minutes (`m`), and it silently returns `0` for anything it does not understand.

Make two changes to `parse_duration`:
- Also support `h` (hours) and `d` (days). So `'2h'` is `7200` seconds and `'1d'` is `86400`.
- Instead of silently returning `0` for an unrecognized unit or a malformed value, raise
  `ValueError`.

Keep the function name and signature; it must remain importable as
`from durations import parse_duration`.

**All existing tests must keep passing.**
