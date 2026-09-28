from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def dec(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise ValueError("Value must be finite.")
        return d
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid decimal number.")


def money(value):
    return dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gross_margin(cost, percent):
    p = dec(percent)
    if not 0 <= p < 100:
        raise ValueError("Gross margin must be at least 0% and below 100%.")
    if cost is None:
        raise ValueError("A supplier cost is required before applying margin.")
    return money(dec(cost) / (1 - p / 100))


def markup(cost, percent):
    p = dec(percent)
    if not 0 <= p <= 1000:
        raise ValueError("Markup must be between 0% and 1000%.")
    if cost is None:
        raise ValueError("A supplier cost is required before applying markup.")
    return money(dec(cost) * (1 + p / 100))


def extended(line):
    return money(line.quantity * line.price) if line.price is not None else None
