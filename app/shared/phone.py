"""Indonesian mobile number normalisation shared by imports and manual edits."""

import math
import re

_NON_DIGITS = re.compile(r"\D")
# After the 62 country code: a mobile number starts with 8 and has 9-12 digits in total.
_NATIONAL_MOBILE = re.compile(r"8\d{8,11}")
_MASK_VISIBLE_DIGITS = 4
_MASK_CHARS = 6


def normalize_phone(raw: str | int | float | None) -> str | None:
    """Return the number as ``+62...`` or ``None`` when it is not an Indonesian mobile number.

    Accepts text with any punctuation, integers and whole floats (spreadsheets often store phone
    numbers as ``6.28123456789e11``). A leading ``0``, ``62`` or ``8`` is recognised as the
    national prefix, the country code and a bare mobile number respectively.
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

    digits = _NON_DIGITS.sub("", text)
    if digits.startswith("62"):
        national = digits[2:]
    elif digits.startswith("0"):
        national = digits[1:]
    elif digits.startswith("8"):
        national = digits
    else:
        return None

    if not _NATIONAL_MOBILE.fullmatch(national):
        return None
    return f"+62{national}"


def mask_phone(phone: str | None) -> str | None:
    """Hide all but the last four digits, for audit details and logs."""
    if not phone:
        return None
    return "*" * _MASK_CHARS + phone[-_MASK_VISIBLE_DIGITS:]
