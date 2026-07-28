from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    gcp_project: str
    bigquery_dataset: str
    reports_dir: str
    clients_dir: str = "config/clients"
    trace_bucket: str = "reporting-automation-trace"
    gdrive_root_folder_id: str | None = None


def load_settings(path: str | Path = "config/settings.yaml") -> Settings:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)
