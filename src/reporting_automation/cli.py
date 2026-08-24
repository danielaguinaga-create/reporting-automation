from __future__ import annotations

from pathlib import Path

import google.auth
import typer
from google.cloud import bigquery, secretmanager, storage
from googleapiclient.discovery import build as build_drive_service
from pydantic import ValidationError

from reporting_automation.ask import AskCancelled, upload_ask_files_to_drive
from reporting_automation.ask import ask as ask_question
from reporting_automation.batch import build_scheduler_job_command, load_batch_manifest, run_batch
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
from reporting_automation.gcs_landing import try_land_rendered_files
from reporting_automation.llm.anthropic_client import (
    DEFAULT_MODEL,
    AnthropicChatModel,
    resolve_anthropic_api_key,
)
from reporting_automation.llm.sql_generator import GeneratedSql, SqlGenerationError
from reporting_automation.llm.sql_safety import UnsafeSqlError
from reporting_automation.orchestrator import run_report
from reporting_automation.query.bigquery_client import BigQueryExecutor, parse_param_declaration
from reporting_automation.rendering.template_registry import TemplateNotFoundError, TemplateRegistry
from reporting_automation.secrets.secret_manager import SecretManagerClient
from reporting_automation.time_window import WINDOW_PRESETS

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
        try:
            name, bq_type = parse_param_declaration(item)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        schema[name] = bq_type
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
    window: str = typer.Option(
        None,
        "--window",
        help="Preset de ventana de tiempo (ver list-window-presets). Solo aplica si el "
        "reporte declara start_date/end_date en params_schema.",
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
    if window is not None and window not in WINDOW_PRESETS:
        raise typer.BadParameter(
            f"--window debe ser uno de {sorted(WINDOW_PRESETS)}, recibido: {window!r}"
        )

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
        window=window,
    )

    if result.status == "failure":
        typer.echo(f"ERROR ejecutando {report!r}: {result.error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"OK: {result.rows} filas x {result.columns} columnas")
    for rendered in result.rendered_files:
        typer.echo(f"  - {rendered.format.value}: {rendered.local_path}")

    gcs_client = storage.Client(project=settings.gcp_project)
    gcs_uris, gcs_error = try_land_rendered_files(
        gcs_client, settings.trace_bucket, client, result.rendered_files
    )
    if gcs_error:
        typer.echo(f"Aviso: no se pudo copiar a GCS ({settings.trace_bucket}): {gcs_error}", err=True)
    for uri in gcs_uris:
        typer.echo(f"  - gcs: {uri}")

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
    template: str = typer.Option(
        None,
        "--template",
        help="Nombre de una plantilla en config/templates/<nombre>.html.j2 (para pdf/html). "
        "Si se omite, se usa la plantilla default empaquetada.",
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

    if template:
        template_registry = TemplateRegistry()
        template_registry.load(load_settings(settings_path).templates_dir)
        try:
            template_registry.get(template)
        except TemplateNotFoundError as exc:
            raise typer.BadParameter(str(exc)) from exc

    try:
        formats = [OutputFormat(f.strip()) for f in output_formats.split(",") if f.strip()]
    except ValueError as exc:
        raise typer.BadParameter(
            f"--output-formats invalido: {exc}. Validos: {[f.value for f in OutputFormat]}"
        ) from exc

    for fmt in formats:
        if fmt == OutputFormat.GSHEETS:
            typer.echo(
                f"Aviso: el renderer de {fmt.value!r} todavia no esta implementado "
                "(falta decidir Sheets API vs. export a Drive, ver README). El reporte "
                "queda registrado, pero 'run' fallara para ese formato.",
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
            template=template,
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


@app.command("list-window-presets")
def list_window_presets() -> None:
    """Presets validos para `run --window` (solo aplica a reportes que declaren
    start_date/end_date en params_schema -- ver README, seccion Ventanas de tiempo)."""
    for key, label in WINDOW_PRESETS.items():
        typer.echo(f"{key}\t{label}")


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

    gcs_client = storage.Client(project=settings.gcp_project)
    delivery_factories = _build_delivery_factories(settings) if deliver else None

    for entry, result in zip(entries, results):
        if result.status == "success":
            typer.echo(f"[OK]    {result.report_id} ({result.client_id}): {result.rows} filas")
            gcs_uris, gcs_error = try_land_rendered_files(
                gcs_client, settings.trace_bucket, entry.client, result.rendered_files
            )
            if gcs_error:
                typer.echo(f"        Aviso: no se pudo copiar a GCS: {gcs_error}", err=True)
            for uri in gcs_uris:
                typer.echo(f"        gcs: {uri}")
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


@app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="Pregunta en lenguaje natural sobre los datos"),
    formats: str = typer.Option(
        "csv", "--formats", help="Lista separada por comas: csv,xlsx,pdf"
    ),
    output_dir: str = typer.Option("./out/preguntas", "--output-dir"),
    deliver_drive: bool = typer.Option(
        False,
        "--deliver-drive",
        help="Ademas de generar los archivos localmente, subirlos a Drive "
        "(carpeta settings.adhoc_gdrive_folder_id / preguntas_libres / <year><mes>/).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="No pedir confirmacion antes de ejecutar el SQL generado contra BigQuery "
        "(igual se bloquea cualquier cosa que no sea SELECT/WITH).",
    ),
    refresh_schema: bool = typer.Option(
        False,
        "--refresh-schema",
        help="Ignora el cache local del esquema del dataset y lo vuelve a leer de BigQuery.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Modelo de Anthropic a usar"),
    settings_path: str = typer.Option("config/settings.yaml", "--settings"),
) -> None:
    """Traduce una pregunta en espanol a SQL, la corre contra BigQuery (dataset de
    `settings.bigquery_dataset`) y responde en lenguaje natural.

    Requiere `ANTHROPIC_API_KEY` (o el secreto `anthropic-api-key` en Secret Manager)
    y credenciales de BigQuery/Drive validas (`gcloud auth application-default login`).

    Seguridad: el SQL generado se valida dos veces antes de tocar datos -- primero
    por regex (debe empezar con SELECT/WITH, sin DDL/DML, una sola sentencia), y
    despues por el `statement_type` que BigQuery mismo devuelve en un dry run.
    Por defecto, ademas, pide confirmar el SQL (con el costo estimado en bytes)
    antes de ejecutarlo de verdad -- usa `--yes` para saltarte esa confirmacion
    en scripts/automatizacion.
    """
    try:
        output_formats = [OutputFormat(f.strip()) for f in formats.split(",") if f.strip()]
    except ValueError as exc:
        raise typer.BadParameter(f"--formats invalido: {exc}. Validos: csv, xlsx, pdf") from exc
    if OutputFormat.GSHEETS in output_formats or OutputFormat.TXT in output_formats:
        raise typer.BadParameter("--formats solo soporta csv, xlsx, pdf en 'ask'.")

    settings = load_settings(settings_path)

    bq_client = bigquery.Client(project=settings.gcp_project)
    secret_manager = SecretManagerClient(secretmanager.SecretManagerServiceClient(), settings.gcp_project)

    try:
        api_key = resolve_anthropic_api_key(secret_manager)
    except RuntimeError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from None

    chat_model = AnthropicChatModel(api_key=api_key, model=model)

    def _confirm(generated: GeneratedSql, num_bytes: int) -> bool:
        typer.echo("\nSQL generado:")
        typer.echo(generated.sql)
        typer.echo(f"\nExplicacion: {generated.explanation}")
        typer.echo(f"\nBytes estimados a procesar: {num_bytes:,}")
        if yes:
            return True
        return typer.confirm("\nEjecutar esta consulta contra BigQuery?")

    try:
        result = ask_question(
            question,
            bq_client=bq_client,
            schema_runner=bq_client,
            chat_model=chat_model,
            project=settings.gcp_project,
            dataset=settings.bigquery_dataset,
            output_dir=Path(output_dir),
            formats=output_formats,
            schema_cache_path=Path(".cache") / f"schema_{settings.bigquery_dataset}.json",
            confirm=_confirm,
            force_refresh_schema=refresh_schema,
        )
    except AskCancelled:
        typer.echo("Cancelado -- no se ejecuto ninguna consulta.")
        raise typer.Exit(code=1) from None
    except UnsafeSqlError as exc:
        typer.echo(f"ERROR de seguridad: {exc}", err=True)
        raise typer.Exit(code=1) from None
    except SqlGenerationError as exc:
        typer.echo(f"ERROR generando SQL: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"\n{result.df.shape[0]} filas x {result.df.shape[1]} columnas")
    typer.echo(f"\nRespuesta: {result.answer}\n")
    for rendered in result.rendered_files:
        typer.echo(f"  - {rendered.format.value}: {rendered.local_path}")

    if deliver_drive:
        credentials, _ = google.auth.default()
        drive_service = build_drive_service("drive", "v3", credentials=credentials)
        drive_delivery = GDriveDelivery(drive_service, settings.adhoc_gdrive_folder_id)
        delivery_result = upload_ask_files_to_drive(drive_delivery, result.rendered_files, question)
        _echo_delivery_results([delivery_result])
        if delivery_result.status == "failed":
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
        command = build_scheduler_job_command(
            entry,
            topic_path=topic_path,
            location=location,
            default_schedule=schedule,
            timezone=timezone,
            project=settings.gcp_project,
        )
        typer.echo(command)


if __name__ == "__main__":
    app()
