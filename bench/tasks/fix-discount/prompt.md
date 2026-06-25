`pricing.py` has a function `final_price(cents, discount_percent)` that is supposed to apply a
percentage discount to a price given in integer cents, but it has a bug.

Spec:
- `discount_percent` is clamped to the range `0..100`.
- The discounted price is `cents * (100 - discount_percent) / 100`, rounded to the nearest whole
  cent, with halves rounding **up**.
- Returns an `int` (cents).

Examples:
- `final_price(199, 10)  == 179`
- `final_price(105, 50)  == 53`     # 52.5 rounds up
- `final_price(1000, 0)  == 1000`
- `final_price(1000, 150) == 0`     # clamped to 100% off
- `final_price(1000, -10) == 1000`  # clamped to 0%

Find and fix the bug. Keep the function name and signature; it must be importable as
`from pricing import final_price`.
