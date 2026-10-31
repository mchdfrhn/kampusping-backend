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
        ("6208123456789", "+628123456789"),  # stray 0 after the country code
        ("+62 0812-3456-789", "+628123456789"),
        ("0062812345678", "+62812345678"),  # international 00 prefix
        ("+0062 812 3456 789", "+628123456789"),
        ("6.28123456789E+11", "+628123456789"),  # Excel/LibreOffice CSV export of a number
        ("6.28123456789e11", "+628123456789"),
        (" 6,28123456789E+11 ", "+628123456789"),
        ("8.12345678E+10", "+6281234567800"),
        ("628123456789E0", "+628123456789"),
        ("62812345678.0", "+62812345678"),  # text of a whole float: ".0" is not a digit to keep
        ("628123456789,0", "+628123456789"),
        ("628123456789.00", "+628123456789"),
        (" 08123456789.0 ", "+628123456789"),
        ("0812.3456.7890", "+6281234567890"),  # dots as digit grouping stay grouping
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
        "62008123456789",  # only one stray 0 is tolerated
        "00812345678",  # 00 is only an international prefix when followed by 62
        "1E+400",  # absurd exponent
        "628123456789.5",  # a real decimal fraction is never digit-stripped into a number
        "62812345678.50",
        "628123456789,5",
        "6.28123456789E+10",  # 62812345678.9 is not a whole number
        "6.28123456789E-11",
        "-6.28123456789E+11",
        "6.28123456789E+",
        "62812E+1x",
        "62812abc34",
        "08123456E78901",  # an e between digits is never digit-stripped into a number
        "0812345e67890",
        "006208123456",  # too short after 62 and the stray 0
        pytest.param(10**5000, id="huge-int"),  # beyond Python's int-to-str digit limit
        pytest.param(-(10**5000), id="huge-negative-int"),
    ],
)
def test_normalize_phone_invalid(raw: str | int | float | None) -> None:
    assert normalize_phone(raw) is None
