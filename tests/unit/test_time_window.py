from datetime import date

import pytest

from reporting_automation.time_window import WINDOW_PRESETS, resolve_window


def test_previous_month_mid_year():
    assert resolve_window("previous_month", date(2026, 6, 15)) == ("2026-05-01", "2026-05-31")


def test_previous_month_january_rolls_back_to_previous_december():
    assert resolve_window("previous_month", date(2026, 1, 10)) == ("2025-12-01", "2025-12-31")


def test_previous_month_handles_leap_february():
    # Marzo 2024 -> febrero 2024, que es bisiesto (29 dias).
    assert resolve_window("previous_month", date(2024, 3, 5)) == ("2024-02-01", "2024-02-29")


def test_previous_month_handles_non_leap_february():
    assert resolve_window("previous_month", date(2025, 3, 5)) == ("2025-02-01", "2025-02-28")


def test_current_month_is_month_to_date():
    assert resolve_window("current_month", date(2026, 6, 15)) == ("2026-06-01", "2026-06-15")


def test_last_7_days_is_inclusive_of_run_date():
    assert resolve_window("last_7_days", date(2026, 6, 15)) == ("2026-06-09", "2026-06-15")


def test_last_30_days_is_inclusive_of_run_date():
    assert resolve_window("last_30_days", date(2026, 6, 15)) == ("2026-05-17", "2026-06-15")


def test_last_90_days_is_inclusive_of_run_date():
    assert resolve_window("last_90_days", date(2026, 6, 15)) == ("2026-03-18", "2026-06-15")


def test_year_to_date():
    assert resolve_window("year_to_date", date(2026, 6, 15)) == ("2026-01-01", "2026-06-15")


def test_all_time_spans_from_epoch_to_run_date():
    assert resolve_window("all_time", date(2026, 6, 15)) == ("1970-01-01", "2026-06-15")


def test_unknown_preset_raises_clear_error():
    with pytest.raises(ValueError, match="Preset de ventana no soportado"):
        resolve_window("does_not_exist", date(2026, 6, 15))


def test_all_presets_in_dict_are_resolvable():
    for preset in WINDOW_PRESETS:
        start, end = resolve_window(preset, date(2026, 6, 15))
        assert start <= end
