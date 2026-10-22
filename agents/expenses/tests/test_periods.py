from datetime import date, timedelta

import pytest

from expenses.periods import actual_payday, period_for, period_label


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 10, date(2026, 10, 28)),  # Wednesday
        (2026, 11, date(2026, 11, 27)),  # 28th is Saturday -> Friday
        (2027, 2, date(2027, 2, 26)),  # 28th is Sunday -> Friday
        (2027, 3, date(2027, 3, 25)),  # 28th is Sunday, 26th is Good Friday -> Thursday
    ],
)
def test_actual_payday(year, month, expected):
    assert actual_payday(year, month) == expected


def test_payday_clamped_to_month_end():
    assert actual_payday(2026, 2, payday=31) == date(2026, 2, 27)  # Feb 28 2026 is Saturday


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 22), (date(2026, 9, 28), date(2026, 10, 27))),
        (date(2026, 10, 28), (date(2026, 10, 28), date(2026, 11, 26))),
        (date(2026, 11, 26), (date(2026, 10, 28), date(2026, 11, 26))),
        (date(2026, 11, 27), (date(2026, 11, 27), date(2026, 12, 27))),
        (date(2026, 12, 31), (date(2026, 12, 28), date(2027, 1, 27))),
        (date(2027, 2, 25), (date(2027, 1, 28), date(2027, 2, 25))),
        (date(2027, 2, 26), (date(2027, 2, 26), date(2027, 3, 24))),
    ],
)
def test_period_for(day, expected):
    assert period_for(day) == expected


def test_every_day_belongs_to_exactly_one_contiguous_period():
    day = date(2026, 1, 1)
    _, end = period_for(day)
    for _ in range(5 * 365):
        day += timedelta(days=1)
        start, new_end = period_for(day)
        assert start <= day <= new_end
        if day > end:
            assert start == day  # the next period starts right after the previous one ends
            end = new_end


def test_period_label_fits_excel_sheet_name():
    label = period_label(*period_for(date(2026, 10, 28)))
    assert label == "2026-10-28 → 2026-11-26"
    assert len(label) <= 31
