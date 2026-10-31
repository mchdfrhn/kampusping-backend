import pytest

from app.shared.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0812-3456-7890", "+6281234567890"),
        ("+62 812 3456 7890", "+6281234567890"),
        ("62812345678901", "+62812345678901"),
        ("812345678", "+62812345678"),
        (628123456789, "+628123456789"),
        (6.28123456789e11, "+628123456789"),
        ("(0812) 3456.7890", "+6281234567890"),
        ("08123456789", "+628123456789"),
        ("62812345678", "+62812345678"),
    ],
)
def test_normalize_phone_valid(raw: str | int | float, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "abc",
        "12345",
        "+6512345678",
        "0",
        "0212345678",  # landline, not mobile
        "628123456",  # too short after 62
        "6281234567890123",  # too long after 62
        628.5,  # not an integer float
        float("nan"),
        float("inf"),
        True,
        -628123456789,  # negative numbers are never phone numbers
        -6.28123456789e11,
        pytest.param(10**5000, id="huge-int"),  # beyond Python's int-to-str digit limit
        pytest.param(-(10**5000), id="huge-negative-int"),
    ],
)
def test_normalize_phone_invalid(raw: str | int | float | None) -> None:
    assert normalize_phone(raw) is None
