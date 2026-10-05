"""Invoice totals in integer cents."""


def line_cents(price, qty):
    return int(price * 100) * qty


def total_cents(lines, discount_percent=0):
    """Sum (price, qty) lines and apply a whole-percent discount, rounding the discount down."""
    subtotal = sum(line_cents(price, qty) for price, qty in lines)
    return subtotal - subtotal * discount_percent // 100
