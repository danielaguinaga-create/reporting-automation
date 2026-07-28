from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def reports_fixtures_dir() -> Path:
    return FIXTURES_DIR / "reports"


@pytest.fixture
def clients_fixtures_dir() -> Path:
    return FIXTURES_DIR / "clients"
