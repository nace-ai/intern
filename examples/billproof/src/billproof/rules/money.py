"""Integer-cent arithmetic; pct_of(100, 20) is twenty cents."""
from decimal import Decimal, ROUND_HALF_EVEN


def fmt(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{fraction:02d}"


def pct_of(cents: int, pct: int | float) -> int:
    return int((Decimal(cents) * Decimal(str(pct)) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))
