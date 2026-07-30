"""The default FEC pull period is derived from the date, not hardcoded."""
import pull


def test_current_period_is_even_upper_year():
    assert pull._current_period(2025) == 2026
    assert pull._current_period(2026) == 2026
    assert pull._current_period(2027) == 2028
    assert pull._current_period(2028) == 2028


def test_current_period_always_even_and_not_behind():
    for y in range(2020, 2041):
        p = pull._current_period(y)
        assert p % 2 == 0          # FEC two-year period is the even upper year
        assert p in (y, y + 1)     # never behind the calendar year
