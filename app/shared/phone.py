"""Indonesian mobile number normalisation shared by imports and manual edits."""

import math
import re
from decimal import Decimal, InvalidOperation

_NON_DIGITS = re.compile(r"\D")
# Text exported from a numeric spreadsheet column: "6.28123456789E+11" (comma decimals too).
_SCIENTIFIC = re.compile(r"\d+(?:[.,]\d+)?[eE][+-]?\d+")
_EXPONENT_MARK = re.compile(r"\d[eE]")
_MAX_SCIENTIFIC_MAGNITUDE = 30
_COUNTRY_CODE = "62"
_INTERNATIONAL_PREFIX = "00"
# After the 62 country code: a mobile number starts with 8 and has 9-12 digits in total.
_NATIONAL_MOBILE = re.compile(r"8\d{8,11}")
_MASK_VISIBLE_DIGITS = 4
_MASK_CHARS = 6


def normalize_phone(raw: str | int | float | None) -> str | None:
    """Return the number as ``+62...`` or ``None`` when it is not an Indonesian mobile number.

    Accepts text with any punctuation, integers and whole floats (spreadsheets often store phone
    numbers as ``6.28123456789e11``). A leading ``0``, ``62`` or ``8`` is recognised as the
    national prefix, the country code and a bare mobile number respectively. Two real-world
    quirks are tolerated: the international ``00`` prefix (``0062812...``) and a single stray
    ``0`` after the country code (``62 0812...``).
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float) and raw < 0:
        return None
    if isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            return None
        raw = int(raw)
    try:
        text = str(raw)
    except ValueError:  # an int beyond Python's int-to-str digit limit
        return None

    text = text.strip()
    if _SCIENTIFIC.fullmatch(text):
        digits = _scientific_digits(text)
        if digits is None:
            return None
    elif _EXPONENT_MARK.search(text):  # never digit-strip an exponent into a different number
        return None
    else:
        digits = _NON_DIGITS.sub("", text)
    if digits.startswith(_INTERNATIONAL_PREFIX + _COUNTRY_CODE):
        digits = digits[len(_INTERNATIONAL_PREFIX) :]
    if digits.startswith(_COUNTRY_CODE):
        national = digits[len(_COUNTRY_CODE) :]
        if national.startswith("0"):  # "+62 0812...": the national 0 kept by mistake
            national = national[1:]
    elif digits.startswith("0"):
        national = digits[1:]
    elif digits.startswith("8"):
        national = digits
    else:
        return None

    if not _NATIONAL_MOBILE.fullmatch(national):
        return None
    return f"+62{national}"


def _scientific_digits(text: str) -> str | None:
    """Digits of a whole, non-negative number written as ``6.28123456789E+11``; else ``None``."""
    try:
        number = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    if not number.is_finite() or number.adjusted() > _MAX_SCIENTIFIC_MAGNITUDE:
        return None
    if number != number.to_integral_value():
        return None
    return str(int(number))


def mask_phone(phone: str | None) -> str | None:
    """Hide all but the last four digits, for audit details and logs."""
    if not phone:
        return None
    return "*" * _MASK_CHARS + phone[-_MASK_VISIBLE_DIGITS:]
