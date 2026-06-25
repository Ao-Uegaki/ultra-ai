def final_price(cents, discount_percent):
    """Apply an integer-percent discount to a price in cents.

    See the task prompt for the exact spec (clamping + half-up rounding).
    """
    # BUG: no clamping, and integer floor division gives the wrong rounding.
    return cents - cents * discount_percent // 100
