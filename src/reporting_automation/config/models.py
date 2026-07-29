from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    """Formatos de salida soportados por el diagrama de arquitectura.

    CSV, XLSX, TXT y PDF tienen renderer implementado. GSHEETS queda
    declarado para que la config lo pueda referenciar sin romper, pero
    ``rendering.factory.get_renderer`` levanta ``NotImplementedError``
    para el hasta que haya un caso de uso real (ver README, roadmap).
    """

    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"
    GSHEETS = "gsheets"
    TXT = "txt"


class ReportKind(str, Enum):
    SHARED = "shared"
    CUSTOM = "custom"


class DeliveryChannel(str, Enum):
    """Canales de entrega del diagrama de arquitectura.

    Solo EMAIL y GDRIVE tienen implementacion en Fase 2. FTP queda
    declarado para que la config lo pueda referenciar sin romper, pero
    ``delivery.factory.get_delivery`` levanta ``NotImplementedError`` para
    el (mismo patron que ``OutputFormat.PDF``/``GSHEETS`` en rendering) --
    no hay un servidor FTP real de ningun cliente contra el cual probarlo
    todavia.
    """

    EMAIL = "email"
    GDRIVE = "gdrive"
    FTP = "ftp"


class ReportConfig(BaseModel):
    id: str
    name: str
    kind: ReportKind
    client_id: str | None = None
    sql_file: str
    output_formats: list[OutputFormat]
    default_recipients: list[str] = Field(default_factory=list)
    delivery_channels: list[DeliveryChannel] = Field(default_factory=list)
    params_schema: dict[str, str] = Field(default_factory=dict)
    params_defaults: dict[str, str] = Field(default_factory=dict)
    filename_pattern: str = "{year}{month}_MD_{report_name}"
    filename_date_param: str | None = None
    description: str | None = None


class ClientConfig(BaseModel):
    """Valores por defecto de parametros BQ para un cliente (ej. su idCompany).

    Permite que un reporte `shared` (parametrizado, ej. `@id_company`) se
    ejecute con solo `--client protec` en vez de tener que pegar el hash de
    idCompany a mano en cada corrida (ver orchestrator.resolve_params).
    """

    id: str
    display_name: str
    bq_params: dict[str, str] = Field(default_factory=dict)
