from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from reporting_automation.config.models import ClientConfig
from reporting_automation.config.scaffold import scaffold_client

_TRUE_VALUES = {"true", "1", "si", "yes"}


def slugify(text: str) -> str:
    """Convierte un CompanyName en un id valido para ClientConfig/nombre de archivo."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return slug or "cliente"


@dataclass(frozen=True)
class ImportResult:
    created: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_inactive: list[str] = field(default_factory=list)


def import_clients_from_csv(
    csv_path: str | Path,
    clients_dir: str | Path,
    only_active: bool = True,
    overwrite: bool = False,
) -> ImportResult:
    """Registra clientes en bloque desde un catalogo maestro tipo:

    `idCompanyMD,idCompany,CompanyName,CompanyFiscalName,CompanyIsActive`

    El `id` de cada `ClientConfig` sale de slugificar `CompanyName` (ej.
    "Avanza Seguros" -> "avanza_seguros"); si dos nombres producen el mismo
    slug, al segundo se le agrega el `idCompanyMD` como sufijo para no
    pisarse. Filas sin `idCompany`/`CompanyName`, o inactivas cuando
    `only_active=True`, se omiten.
    """
    clients_dir = Path(clients_dir)
    existing_ids = {p.stem for p in clients_dir.glob("*.yaml")} if clients_dir.is_dir() else set()

    result = ImportResult()
    seen_slugs: set[str] = set()

    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            id_company = (row.get("idCompany") or "").strip()
            name = (row.get("CompanyName") or "").strip()
            id_company_md = (row.get("idCompanyMD") or "").strip()
            is_active = (row.get("CompanyIsActive") or "").strip().lower() in _TRUE_VALUES

            if not id_company or not name:
                continue
            if only_active and not is_active:
                result.skipped_inactive.append(name)
                continue

            slug = slugify(name)
            if slug in seen_slugs:
                slug = f"{slug}_{id_company_md}" if id_company_md else f"{slug}_{id_company}"
            seen_slugs.add(slug)

            if slug in existing_ids and not overwrite:
                result.skipped_existing.append(slug)
                continue

            client = ClientConfig(id=slug, display_name=name, bq_params={"id_company": id_company})
            scaffold_client(clients_dir, client, overwrite=overwrite)
            existing_ids.add(slug)
            result.created.append(slug)

    return result
