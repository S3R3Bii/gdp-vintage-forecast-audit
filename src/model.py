"""Deterministic benchmark rules and exact arithmetic on declared fixed-point growth."""

from decimal import Decimal, localcontext
from fractions import Fraction
import re

SCALE = 10**12
WEIGHTS = [Fraction(0), Fraction(1, 4), Fraction(1, 2), Fraction(3, 4), Fraction(1)]
CONFIGS = ["zero", "mean20", "blend25", "blend50", "blend75", "last_growth"]
REGIMES = [
    ("real_time", "early"),
    ("real_time", "later"),
    ("revised", "early"),
    ("revised", "later"),
]


def quarter(text):
    match = re.fullmatch(r"(\d{4})Q([1-4])", text)
    if not match:
        raise ValueError("Expected YYYYQ1 through YYYYQ4")
    return int(match[1]) * 4 + int(match[2]) - 1


def label(index):
    return f"{index//4}Q{index%4+1}"


def fixed(value, digits=12):
    """Round a rational exactly, including negative ties and zero."""
    value = Fraction(value)
    factor = 10**digits
    sign = -1 if value < 0 else 1
    quotient, remainder = divmod(abs(value.numerator) * factor, value.denominator)
    if 2 * remainder > value.denominator or (
        2 * remainder == value.denominator and quotient % 2
    ):
        quotient += 1
    return Fraction(sign * quotient, factor)


def display(value, digits=12):
    value = fixed(value, digits)
    scaled = value * 10**digits
    assert scaled.denominator == 1
    number = scaled.numerator
    sign = "-" if number < 0 else ""
    number = abs(number)
    return f"{sign}{number//10**digits}.{number%10**digits:0{digits}d}"


def exact(value):
    value = Fraction(value)
    return f"{value.numerator}/{value.denominator}"


def growth(current, previous):
    current, previous = Fraction(current), Fraction(previous)
    if current <= 0 or previous <= 0:
        raise ValueError("GDP levels must be positive")
    return 100 * ((current / previous) ** 4 - 1)


def forecast(history, config):
    if len(history) != 20:
        raise ValueError("Exactly twenty available growth rates are required")
    if config == "zero":
        return Fraction(0)
    index = CONFIGS.index(config) - 1
    weight = WEIGHTS[index]
    return (1 - weight) * sum(history, Fraction(0)) / 20 + weight * history[-1]


def metrics(predictions, targets):
    if not predictions or len(predictions) != len(targets):
        raise ValueError("Nonempty matched observations are required")
    errors = [p - y for p, y in zip(predictions, targets)]
    n = len(errors)
    mae = sum(map(abs, errors), Fraction(0)) / n
    mse = sum((e * e for e in errors), Fraction(0)) / n
    with localcontext() as context:
        context.prec = 50
        rmse = format(
            (Decimal(mse.numerator) / Decimal(mse.denominator)).sqrt(), ".12f"
        )
    return dict(
        n=n,
        mae=display(mae),
        mse=display(mse),
        rmse=rmse,
        mae_exact=exact(mae),
        mse_exact=exact(mse),
    )


def choose(scores):
    return min(range(len(scores)), key=lambda i: (Fraction(scores[i]["mae_exact"]), i))
