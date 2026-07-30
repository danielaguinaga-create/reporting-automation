from __future__ import annotations

import calendar
from datetime import date, timedelta

# Presets de ventana de tiempo, reconocidos cuando un reporte declara
# `start_date`/`end_date` (tipo DATE) en su `params_schema` -- ver
# `orchestrator.resolve_params`. Todos los rangos son inclusivos en ambos
# extremos y se calculan con aritmetica naive (sin timezone), igual que el
# sentinel `previous_month_first_day` que ya existia para `billing_month_date`.
WINDOW_PRESETS: dict[str, str] = {
    "previous_month": "Mes anterior",
    "current_month": "Mes actual (a la fecha)",
    "last_7_days": "Ultimos 7 dias",
    "last_30_days": "Ultimos 30 dias",
    "last_90_days": "Ultimos 90 dias",
    "year_to_date": "Ano a la fecha",
}


def previous_month_first_day(run_date: date) -> str:
    year, month = run_date.year, run_date.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    return date(year, month, 1).isoformat()


def resolve_window(preset: str, run_date: date) -> tuple[str, str]:
    """Resuelve un preset de ventana a `(start_date, end_date)` ISO, ambos
    inclusive, relativos a `run_date`."""
    if preset == "previous_month":
        year, month = run_date.year, run_date.month
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
        start = date(year, month, 1)
        _, last_day = calendar.monthrange(year, month)
        end = date(year, month, last_day)
    elif preset == "current_month":
        start = date(run_date.year, run_date.month, 1)
        end = run_date
    elif preset == "last_7_days":
        start = run_date - timedelta(days=6)
        end = run_date
    elif preset == "last_30_days":
        start = run_date - timedelta(days=29)
        end = run_date
    elif preset == "last_90_days":
        start = run_date - timedelta(days=89)
        end = run_date
    elif preset == "year_to_date":
        start = date(run_date.year, 1, 1)
        end = run_date
    else:
        raise ValueError(
            f"Preset de ventana no soportado: {preset!r}. Soportados: {sorted(WINDOW_PRESETS)}"
        )
    return start.isoformat(), end.isoformat()
