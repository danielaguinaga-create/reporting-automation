from __future__ import annotations

import json
import shlex
from pathlib import Path

import google.auth
import typer
from google.cloud import bigquery, secretmanager
from googleapiclient.discovery import build as build_drive_service
from pydantic import ValidationError

from reporting_automation.batch import load_batch_manifest, run_batch
from reporting_automation.config.client_import import import_clients_from_csv
from reporting_automation.config.client_registry import ClientRegistry
from reporting_automation.config.loader import Settings, load_settings
from reporting_automation.config.models import (
    ClientConfig,
    DeliveryChannel,
    OutputFormat,
    ReportConfig,
    ReportKind,
)
from reporting_automation.config.registry import ReportRegistry
from reporting_automation.config.scaffold import scaffold_client, scaffold_report
from reporting_automation.delivery.base import Delivery, DeliveryResult
from reporting_automation.delivery.dispatch import dispatch_delivery, resolve_recipients
from reporting_automation.delivery.email_delivery import EmailDelivery
from reporting_automation.delivery.gdrive_delivery import GDriveDelivery
from reporting_automation.exceptions import ClientConfigError, ReportConfigError
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor
from reporting_automation.secrets.secret_manager import SecretManagerClient

app = typer.Typer(add_completion=False)


def _load_settings_and_registry(settings_path: str) -> tuple[Settings, ReportRegistry]:
    settings = load_settings(settings_path)
    registry = ReportRegistry()
    registry.load(settings.reports_dir)
    return settings, registry


def _load_client_registry(settings: Settings) -> ClientRegistry:
    client_registry = ClientRegistry()
    client_registry.load(settings.clients_dir)
    return client_registry


def _parse_params(raw_params: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw_params:
        if "=" not in item:
            raise typer.BadParameter(f"--param debe ser key=value, recibido: {item!r}")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def _parse_param_schema(raw: list[str]) -> dict[str, str]:
    schema: dict[str, str] = {}
    for item in raw:
        if ":" not in item:
            raise typer.BadParameter(
                f"--param debe ser nombre:TIPO_BQ (ej. billing_month_date:DATE), recibido: {item!r}"
            )
        name, bq_type = item.split(":", 1)
        schema[name] = bq_type.upper()
    return schema


def _build_delivery_factories(settings: Settings) -> dict[DeliveryChannel, Delivery]:
    """Construye los `Delivery` reales (SMTP via Secret Manager, GDrive via
    ADC) -- no toca red hasta que `.send()` se invoque de verdad."""
    secret_manager = SecretManagerClient(secretmanager.SecretManagerServiceClient(), settings.gcp_project)
    credentials, _ = google.auth.default()
    drive_service = build_drive_service("drive", "v3", credentials=credentials)
    return {
        DeliveryChannel.EMAIL: EmailDelivery(secret_manager),
        DeliveryChannel.GDRIVE: GDriveDelivery(drive_service, settings.gdrive_root_folder_id),
    }


def _echo_delivery_results(results: list[DeliveryResult]) -> None:
    for delivery_result in results:
        prefix = "[ENVIADO]" if delivery_result.status == "sent" else "[ENTREGA FALLIDA]"
        typer.echo(f"  {prefix} {delivery_result.channel.value}: {delivery_result.detail}")


@app.command()
def run(
    report: str = typer.Option(..., "--report", help="id del reporte (ver list-reports)"),
    client: str = typer.Option(..., "--client", help="id del cliente"),
    output_dir: str = typer.Option("./out", "--output-dir"),
    param: list[str] = typer.Option(
        [], "--param", help="Parametro key=value para la query. Repetible."
    ),
    deliver: bool = typer.Option(
        False,
        "--deliver",
        help="Ademas de generar el archivo, entregarlo por los delivery_channels del reporte "
        "(correo/GDrive). Apagado por defecto -- 'run' sin este flag nunca manda nada.",
    ),
    recipients: str = typer.Option(
        None,
        "--recipients",
        help="Destinatarios separados por coma, override de default_recipients. Solo aplica con --deliver.",
    ),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Ejecuta un reporte contra BigQuery real y lo renderiza localmente.

    Si `--client` tiene un `ClientConfig` registrado (ver `new-client`), sus
    `bq_params` (ej. `id_company`) se usan automaticamente para cualquier
    parametro del reporte que coincida en nombre — no hace falta pasarlo con
    `--param` a mano.

    Requiere credenciales validas (ej. `gcloud auth application-default
    login`) — no cubierto por los tests unitarios, que mockean el cliente.
    """
    settings, registry = _load_settings_and_registry(settings_path)
    client_registry = _load_client_registry(settings)
    params = _parse_params(param)

    bq_client = bigquery.Client(project=settings.gcp_project)
    executor = BigQueryExecutor(bq_client)

    result = run_report(
        report_id=report,
        client_id=client,
        params=params,
        output_dir=output_dir,
        registry=registry,
        executor=executor,
        client_registry=client_registry,
    )

    if result.status == "failure":
        typer.echo(f"ERROR ejecutando {report!r}: {result.error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"OK: {result.rows} filas x {result.columns} columnas")
    for rendered in result.rendered_files:
        typer.echo(f"  - {rendered.format.value}: {rendered.local_path}")

    if deliver:
        report_config = registry.get(report)
        override = [r.strip() for r in recipients.split(",") if r.strip()] if recipients else None
        final_recipients = resolve_recipients(report_config, override)
        delivery_factories = _build_delivery_factories(settings)
        delivery_results = dispatch_delivery(
            report_config, result.rendered_files, final_recipients, client, delivery_factories
        )
        _echo_delivery_results(delivery_results)


@app.command("list-reports")
def list_reports(
    client: str = typer.Option(None, "--client", help="Filtra por id de cliente"),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    _, registry = _load_settings_and_registry(settings_path)
    reports = registry.list_by_client(client) if client else registry.list_all()

    if not reports:
        typer.echo("No hay reportes registrados.")
        return

    for r in reports:
        typer.echo(f"{r.id}\t{r.kind.value}\t{r.client_id or '-'}\t{r.name}")


@app.command("new-report")
def new_report(
    id: str = typer.Option(..., "--id", help="Identificador unico del reporte"),
    name: str = typer.Option(..., "--name", help="Usado en el nombre de archivo generado"),
    sql_file: Path = typer.Option(
        ..., "--sql-file", help="Ruta a un .sql local con la query ya escrita"
    ),
    kind: str = typer.Option("custom", "--kind", help="shared|custom"),
    client: str = typer.Option(
        None, "--client", help="id del cliente. Requerido si --kind=custom"
    ),
    output_formats: str = typer.Option(
        "csv", "--output-formats", help="Lista separada por comas: csv,xlsx,txt,pdf,gsheets"
    ),
    param: list[str] = typer.Option(
        [],
        "--param",
        help="nombre:TIPO_BQ, repetible. Tipos: STRING,INT64,FLOAT64,BOOL,DATE,DATETIME,TIMESTAMP",
    ),
    param_default: list[str] = typer.Option(
        [],
        "--param-default",
        help="nombre=valor (o 'previous_month_first_day'), repetible",
    ),
    filename_date_param: str = typer.Option(
        None, "--filename-date-param", help="Que param usar para {year}{month} en el nombre"
    ),
    delivery_channel: list[str] = typer.Option(
        [], "--delivery-channel", help="email|gdrive|ftp, repetible. Ver --deliver en 'run'."
    ),
    default_recipient: list[str] = typer.Option(
        [], "--default-recipient", help="Destinatario por defecto si no se pasa --recipients. Repetible."
    ),
    description: str = typer.Option(None, "--description"),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Registra un reporte ad-hoc nuevo (YAML + SQL) sin tocar codigo Python.

    La query en `--sql-file` no se modifica: si tiene parametros, deben ser
    placeholders nativos de BigQuery (`@nombre`), nunca `.format()`/f-strings.
    """
    if kind not in (ReportKind.CUSTOM.value, ReportKind.SHARED.value):
        raise typer.BadParameter(f"--kind debe ser 'shared' o 'custom', recibido: {kind!r}")
    if kind == ReportKind.CUSTOM.value and not client:
        raise typer.BadParameter("--client es requerido cuando --kind=custom")
    if not sql_file.is_file():
        raise typer.BadParameter(f"No existe el archivo --sql-file: {sql_file}")

    try:
        formats = [OutputFormat(f.strip()) for f in output_formats.split(",") if f.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            f"--output-formats invalido: {exc}. Validos: {[f.value for f in OutputFormat]}"
        ) from exc

    for fmt in formats:
        if fmt in (OutputFormat.PDF, OutputFormat.GSHEETS):
            typer.echo(
                f"Aviso: el renderer de {fmt.value!r} todavia no esta implementado "
                "(Fase 2). El reporte queda registrado, pero 'run' fallara para ese formato.",
                err=True,
            )

    try:
        channels = [DeliveryChannel(c.strip()) for c in delivery_channel if c.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            f"--delivery-channel invalido: {exc}. Validos: {[c.value for c in DeliveryChannel]}"
        ) from exc

    if DeliveryChannel.FTP in channels:
        typer.echo(
            "Aviso: el delivery de 'ftp' todavia no esta implementado "
            "(no hay servidor FTP real para probarlo). El reporte queda registrado, "
            "pero 'run --deliver' fallara para ese canal.",
            err=True,
        )

    settings = load_settings(settings_path)

    try:
        report = ReportConfig(
            id=id,
            name=name,
            kind=ReportKind(kind),
            client_id=client,
            sql_file=f"{id}.sql",
            output_formats=formats,
            delivery_channels=channels,
            default_recipients=default_recipient,
            params_schema=_parse_param_schema(param),
            params_defaults=_parse_params(param_default),
            filename_date_param=filename_date_param,
            description=description,
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc

    sql_text = sql_file.read_text(encoding="utf-8")

    try:
        yaml_path, sql_path = scaffold_report(Path(settings.reports_dir), report, sql_text)
    except ReportConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Reporte {id!r} creado:")
    typer.echo(f"  - {yaml_path}")
    typer.echo(f"  - {sql_path}")
    typer.echo(
        f"Probar con: reporting-automation run --report {id} --client {client or '<cliente>'} "
        "--output-dir ./out"
    )


@app.command("list-clients")
def list_clients(
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    settings = load_settings(settings_path)
    client_registry = _load_client_registry(settings)
    clients = client_registry.list_all()

    if not clients:
        typer.echo("No hay clientes registrados (--client sigue funcionando como etiqueta libre).")
        return

    for c in clients:
        bq_params_preview = ", ".join(f"{k}={v}" for k, v in c.bq_params.items())
        typer.echo(f"{c.id}\t{c.display_name}\t{bq_params_preview}")


@app.command("new-client")
def new_client(
    id: str = typer.Option(..., "--id", help="Mismo valor que se pasa en run --client"),
    display_name: str = typer.Option(..., "--display-name"),
    bq_param: list[str] = typer.Option(
        [],
        "--bq-param",
        help="nombre=valor, repetible. Ej: id_company=498cb81c5ba7325f",
    ),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Registra un cliente: valores de parametros BQ que se auto-inyectan en
    reportes `shared` cuando corres `run --client <id>` (ver README).
    """
    settings = load_settings(settings_path)

    try:
        client = ClientConfig(id=id, display_name=display_name, bq_params=_parse_params(bq_param))
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc

    try:
        yaml_path = scaffold_client(Path(settings.clients_dir), client)
    except ClientConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Cliente {id!r} creado: {yaml_path}")


@app.command("import-clients")
def import_clients(
    csv_file: Path = typer.Option(
        ..., "--csv", help="Catalogo maestro: idCompanyMD,idCompany,CompanyName,..."
    ),
    only_active: bool = typer.Option(
        True, "--only-active/--all", help="Omitir filas con CompanyIsActive=false"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Sobreescribir clientes ya registrados con el mismo id"
    ),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Registra clientes en bloque desde el catalogo maestro (idCompany por cliente).

    El `id` de cada cliente sale de slugificar `CompanyName` (ej. "Avanza
    Seguros" -> "avanza_seguros"). Usa `--overwrite` para corregir clientes
    ya registrados a mano si el catalogo tiene el dato correcto.
    """
    if not csv_file.is_file():
        raise typer.BadParameter(f"No existe el archivo --csv: {csv_file}")

    settings = load_settings(settings_path)
    result = import_clients_from_csv(
        csv_path=csv_file,
        clients_dir=Path(settings.clients_dir),
        only_active=only_active,
        overwrite=overwrite,
    )

    typer.echo(f"Creados: {len(result.created)}")
    typer.echo(f"Omitidos (ya existian, usa --overwrite para reemplazar): {len(result.skipped_existing)}")
    typer.echo(f"Omitidos (inactivos): {len(result.skipped_inactive)}")


@app.command("run-batch")
def run_batch_cmd(
    manifest: str = typer.Option(
        "config/monthly_batch.yaml", "--manifest", help="YAML: lista de {report, client, params?}"
    ),
    output_dir: str = typer.Option("./out", "--output-dir"),
    deliver: bool = typer.Option(
        False,
        "--deliver",
        help="Ademas de generar cada archivo, entregarlo por los delivery_channels del reporte. "
        "Apagado por defecto -- 'run-batch' sin este flag nunca manda nada.",
    ),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Corre todos los reportes de un manifiesto en una sola invocacion (ej. la corrida mensual).

    Preview local de lo que en Fase 3 dispararia Cloud Scheduler -> Pub/Sub
    por cada linea del manifiesto. No se detiene en el primer error.
    """
    settings, registry = _load_settings_and_registry(settings_path)
    client_registry = _load_client_registry(settings)
    entries = load_batch_manifest(manifest)

    bq_client = bigquery.Client(project=settings.gcp_project)
    executor = BigQueryExecutor(bq_client)

    results = run_batch(entries, output_dir, registry, executor, client_registry)

    delivery_factories = _build_delivery_factories(settings) if deliver else None

    for entry, result in zip(entries, results):
        if result.status == "success":
            typer.echo(f"[OK]    {result.report_id} ({result.client_id}): {result.rows} filas")
            if deliver:
                report_config = registry.get(entry.report)
                final_recipients = resolve_recipients(report_config, entry.recipients or None)
                delivery_results = dispatch_delivery(
                    report_config, result.rendered_files, final_recipients, entry.client, delivery_factories
                )
                _echo_delivery_results(delivery_results)
        else:
            typer.echo(
                f"[ERROR] {result.report_id} ({result.client_id}): {result.error}", err=True
            )

    ok = sum(1 for r in results if r.status == "success")
    fail = len(results) - ok
    typer.echo(f"\n{ok} exitosos, {fail} con error, de {len(results)} totales.")

    if fail:
        raise typer.Exit(code=1)


@app.command("generate-scheduler-jobs")
def generate_scheduler_jobs(
    manifest: str = typer.Option(
        "config/monthly_batch.yaml", "--manifest", help="Mismo manifiesto que usa run-batch"
    ),
    topic: str = typer.Option(
        "reporting-automation-triggers", "--topic", help="Nombre del tema de Pub/Sub (sin crear)"
    ),
    location: str = typer.Option(
        "europe-southwest1", "--location", help="Region de Cloud Scheduler (Madrid)"
    ),
    schedule: str = typer.Option("0 6 1 * *", "--schedule", help="Cron: por defecto, dia 1 a las 6am"),
    timezone: str = typer.Option("Europe/Madrid", "--timezone"),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Imprime los comandos `gcloud scheduler jobs create pubsub` para cada
    linea del manifiesto -- no crea nada, solo genera el texto.

    No ejecuta ningun comando de gcloud: esto es deliberado (crear recursos
    de GCP reales requiere confirmacion explicita, ver README seccion
    Roadmap/Fase 3). Copia y corre los comandos que necesites cuando el
    tema de Pub/Sub ya exista.
    """
    settings = load_settings(settings_path)
    entries = load_batch_manifest(manifest)
    topic_path = f"projects/{settings.gcp_project}/topics/{topic}"

    for entry in entries:
        job_id = f"{entry.report}_{entry.client}"
        message_body = json.dumps(
            {
                "reporte": entry.report,
                "cliente": entry.client,
                "receptores": entry.recipients,
                "params": entry.params,
            }
        )
        command = (
            f"gcloud scheduler jobs create pubsub {job_id} "
            f"--location={location} --schedule={shlex.quote(schedule)} "
            f"--topic={topic_path} --time-zone={shlex.quote(timezone)} "
            f"--message-body={shlex.quote(message_body)} "
            f"--project={settings.gcp_project}"
        )
        typer.echo(command)


if __name__ == "__main__":
    app()
