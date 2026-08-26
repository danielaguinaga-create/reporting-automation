# reporting-automation

Motor de generación de reportes para clientes de Meeting Doctors. Reemplaza el
notebook de Colab `Reportes_Clientes_Archivos_Extra.ipynb` (14 queries de
BigQuery hardcodeadas en un diccionario Python) por un registro declarativo de
reportes (YAML + SQL), siguiendo el diagrama de arquitectura interno
"Reporting Automation".

**Estado actual: Fase 1 completa y en uso real; Fases 2 y 3 con código listo,
sin desplegar recursos reales de GCP todavía.** Fase 1 implementa el "Def
Main" del diagrama de forma local y testeable: registro de reportes (25
reportes reales registrados, 183 clientes importados), ejecución contra
BigQuery, renderizado a CSV/XLSX/TXT/PDF, `run-batch` para la corrida mensual
completa, y una CLI. Además de los reportes registrados, el comando `ask`
(ver sección [Preguntas en lenguaje natural](#preguntas-en-lenguaje-natural-ask))
traduce preguntas libres en español a SQL vía Claude, con validación de
solo-lectura, y exporta a CSV/XLSX/PDF o Drive. Fase 2 (delivery por correo
+ GDrive) y Fase 3 (disparo
por Cloud Scheduler → Pub/Sub → Cloud Run Service) tienen el código escrito
y probado con mocks (y contra BigQuery real donde aplica), pero **no existen
todavía el secret de SMTP, la carpeta de Drive, ni el tema de
Pub/Sub** (el bucket de trace sí — ya está creado, ver
[Roadmap](#roadmap-fases-2-4)) — son pasos explícitos pendientes. FTP no
está implementado (sin un cliente real para probarlo). La **UI** (Streamlit)
también tiene código listo y probado (ver sección UI); su despliegue real a
Cloud Run + IAP tiene ya otorgados los roles de Cloud Run/Artifact
Registry/IAM/IAP que necesita, pero sigue bloqueado por el permiso de Cloud
Build (falta incluso `cloudbuild.builds.create`, ver sección UI para el
detalle) — no hace falta un admin distinto, solo ese permiso puntual. API
(capa del diagrama) sigue fuera de alcance.

## Requisitos

- Python 3.12+
- Para ejecutar reportes contra BigQuery real: credenciales válidas (`gcloud
  auth application-default login`) con acceso de lectura al proyecto
  `data-prd-424213`, dataset `03_BaseModel`.
- Para el comando `ask` (ver [Preguntas en lenguaje
  natural](#preguntas-en-lenguaje-natural-ask)): además, una API key de
  Anthropic en `ANTHROPIC_API_KEY` (o el secreto `anthropic-api-key` en
  Secret Manager).

## Instalación (desarrollo local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Tests

```bash
pytest tests/unit -v
```

Todos los tests usan fakes/mocks (sin `google.cloud.bigquery.Client` real, sin
red, sin credenciales) — corren en cualquier máquina sin `gcloud init`.

## Uso de la CLI

```bash
# Listar reportes registrados
python -m reporting_automation list-reports
python -m reporting_automation list-reports --client protec

# Ejecutar un reporte sin parámetros
python -m reporting_automation run --report protec_usuarios_activos --client protec --output-dir ./out

# Ejecutar un reporte con parámetro de fecha explícito
python -m reporting_automation run --report runa_colaboradores_chats_mensual --client runa \
    --param billing_month_date=2026-06-01 --output-dir ./out

# Sin --param, los reportes con billing_month_date usan por defecto
# el primer día del mes anterior a la fecha de ejecución.
```

## Ventanas de tiempo (`--window`)

Para reportes que necesitan correr sobre un rango de fechas variable (no un
único mes fijo), la convención es declarar dos params DATE llamados
exactamente `start_date` y `end_date` en `params_schema`, y usarlos en la
SQL con `BETWEEN @start_date AND @end_date` (envolviendo la columna en
`DATE(...)` si es un TIMESTAMP, ej. `DATE(c.ChatSentAtUTC)`). No hace falta
declarar nada más en la config del reporte -- el sistema reconoce esos dos
nombres automáticamente, igual que reconoce `id_company` como param de
cliente.

Un rango 100% custom ya funciona hoy sin nada nuevo:

```bash
python -m reporting_automation run --report chats_detalle_rango --client protec \
    --param start_date=2026-01-01 --param end_date=2026-01-31 --output-dir ./out
```

Para no tener que calcular fechas a mano, `--window <preset>` resuelve un
rango relativo a la fecha de ejecución:

```bash
python -m reporting_automation list-window-presets

python -m reporting_automation run --report chats_detalle_rango --client protec \
    --window last_30_days --output-dir ./out
```

| preset | rango |
|---|---|
| `previous_month` | mes calendario anterior completo |
| `current_month` | del 1° del mes actual a la fecha de ejecución |
| `last_7_days` | últimos 7 días (incluye la fecha de ejecución) |
| `last_30_days` | últimos 30 días |
| `last_90_days` | últimos 90 días |
| `year_to_date` | del 1° de enero a la fecha de ejecución |

Un `--param start_date=...`/`end_date=...` explícito siempre gana por
encima de `--window` (mismo criterio que con cualquier otro param). En el
manifiesto de `run-batch` (`config/monthly_batch.yaml`), cada entrada puede
declarar `window: <preset>` en vez de fechas fijas en `params`. En la UI de
Streamlit, un reporte con `start_date`/`end_date` muestra un selector de
presets (con "Rango personalizado" para elegir fechas a mano) en vez de
pedirlos como texto libre.

`chats_detalle_rango` (en `config/reports/shared/`) es la plantilla de
referencia para copiar este patrón en un reporte nuevo -- no está en
`config/monthly_batch.yaml` a propósito, es solo un ejemplo.

## Corrida mensual (`run-batch`)

Generar todos los reportes recurrentes de un jalón, en vez de invocar `run`
reporte por reporte:

```bash
python -m reporting_automation run-batch --manifest config/monthly_batch.yaml --output-dir ./out
```

`config/monthly_batch.yaml` es la lista declarativa de qué reporte corre para
qué cliente (incluye los 17 reportes migrados del notebook + los agregados
después). Cada línea es:

```yaml
- report: chats_detalle
  client: avanza_seguros
- report: chats_detalle
  client: cobee   # el mismo reporte "shared" puede repetirse para otro cliente
- report: chats_detalle_rango
  client: protec
  window: previous_month   # ver seccion "Ventanas de tiempo" mas arriba
```

Cada entrada escribe en `output_dir/<client>/` (no directo en `output_dir`):
un reporte `shared` corrido para dos clientes en el mismo lote generaría el
mismo nombre de archivo para ambos, y sin la subcarpeta el segundo
sobreescribiría silenciosamente el archivo del primero — esto pasó de verdad
durante el desarrollo (Avanza Seguros y Cobee comparten `chats_detalle`,
`usuarios_detalle`, `videollamadas_finalizadas_detalle`) y quedó corregido.

`run-batch` no se detiene en el primer error: corre todas las líneas y al
final imprime cuántas tuvieron éxito y cuántas fallaron (igual que el resumen
final del notebook original). Termina con código de salida distinto de cero
si alguna falló, para que se pueda detectar en un cron/CI.

Para agregar un reporte nuevo a la corrida mensual, agrega su línea a
`config/monthly_batch.yaml` — no hace falta tocar código.

Requiere credenciales de BigQuery reales (ver Requisitos) — no está cubierto
por los tests unitarios.

## Preguntas en lenguaje natural (`ask`)

Para preguntas puntuales que no justifican registrar un reporte nuevo:
traduce una pregunta en español al SQL de BigQuery necesario, lo corre contra
el dataset configurado (`settings.bigquery_dataset`), y responde en lenguaje
natural. Exporta a CSV/XLSX/PDF y puede subir el resultado a Drive.

```bash
python -m reporting_automation ask "¿Cuántos usuarios activos tuvo Protec el mes pasado?"

python -m reporting_automation ask "Top 10 clientes por número de videollamadas en 2026" \
    --formats csv,xlsx,pdf --deliver-drive

# Para scripts/automatización, sin el prompt de confirmación interactivo:
python -m reporting_automation ask "cuantos chats hubo ayer" --yes
```

**Requiere** `ANTHROPIC_API_KEY` en el entorno (o el secreto `anthropic-api-key`
en Secret Manager, misma convención que `internal-smtp` — ver sección
Roadmap/Fase 2) y credenciales de BigQuery/Drive válidas (`gcloud auth
application-default login`). No cubierto por los tests unitarios (que usan
fakes para el LLM y BigQuery) — probarlo de verdad requiere ambas
credenciales.

**Cómo funciona:**

1. Lee el esquema del dataset (`INFORMATION_SCHEMA.COLUMNS`) y lo cachea
   localmente 24h (`.cache/schema_<dataset>.json`; `--refresh-schema` para
   forzar releerlo si el esquema cambió).
2. Le pasa la pregunta + esquema a Claude, que devuelve una única consulta
   SQL y una explicación.
3. **Seguridad — el SQL generado se valida dos veces antes de tocar datos:**
   primero por regex (debe empezar con `SELECT`/`WITH`, sin `INSERT`,
   `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `MERGE` ni otras sentencias
   de escritura/DDL, y una sola sentencia — nunca varias separadas por
   `;`); después, al hacer un *dry run* contra BigQuery para estimar el
   costo, se verifica el `statement_type` que BigQuery mismo asigna a la
   query (autoritativo, no depende de que el texto "parezca" un SELECT).
4. Muestra el SQL generado y los bytes estimados a procesar, y **pide
   confirmación antes de ejecutar la query de verdad** (`--yes` la salta,
   pensado para uso en scripts — la validación de solo-lectura sigue
   aplicando igual).
5. Ejecuta la query, le pasa el resultado a Claude para que responda la
   pregunta original en 2-4 frases citando cifras concretas.
6. Renderiza los formatos pedidos (`--formats csv,xlsx,pdf`, default `csv`)
   en `--output-dir` (default `./out/preguntas`). El PDF incluye la
   pregunta, el SQL generado, la respuesta y la tabla de resultados
   (recortada a las primeras 500 filas — para más, usar CSV o XLSX).
7. Con `--deliver-drive`, sube los archivos generados a
   `<settings.adhoc_gdrive_folder_id>/preguntas_libres/<year><mes>/`,
   reutilizando `GDriveDelivery` (mismo código que `run --deliver`) —
   requiere configurar `adhoc_gdrive_folder_id` en `config/settings.yaml`
   (deliberadamente separado de `gdrive_root_folder_id`, para no mezclar
   estos exports ad-hoc con las entregas de reportes de cliente).

## Crear un reporte ad-hoc nuevo (lo que pide un cliente)

Este es el caso de uso principal de la herramienta: cualquier reporte nuevo
que pida un cliente se agrega sin tocar código Python ni el orquestador. Solo
hace falta la query SQL:

```bash
python -m reporting_automation new-report \
    --id clientex_ventas_mensuales \
    --name VentasMensuales \
    --client clientex \
    --sql-file ./mi_query.sql \
    --output-formats csv,xlsx \
    --param billing_month_date:DATE \
    --param-default billing_month_date=previous_month_first_day \
    --filename-date-param billing_month_date \
    --description "Reporte ad-hoc pedido por ClienteX"
```

Esto genera `config/reports/custom/clientex_ventas_mensuales.{yaml,sql}` y el
reporte queda disponible de inmediato:

```bash
python -m reporting_automation list-reports
python -m reporting_automation run --report clientex_ventas_mensuales --client clientex --output-dir ./out
```

`--sql-file` debe apuntar a un `.sql` ya escrito por ti (con parámetros
nativos de BigQuery `@nombre` si los necesita, nunca `.format()`/f-strings —
`new-report` no reescribe la query, solo la registra). `--param` y
`--param-default` son opcionales y repetibles; se omiten si el reporte no
necesita parámetros. `--kind shared` en vez de `custom` es para un reporte
reutilizable entre varios clientes (el `client_id` se pasa en `run --client`
en vez de fijarse en la config).

## Cómo definir un reporte a mano (alternativa a `new-report`)

Cada reporte es un par de archivos bajo `config/reports/{shared,custom}/` —
`new-report` simplemente automatiza escribir estos dos archivos:

`mi_reporte.yaml`:
```yaml
id: mi_reporte
name: MiReporte
kind: custom                # o "shared" si es reutilizable entre clientes
client_id: mi_cliente        # None/omitido para reportes "shared"
sql_file: mi_reporte.sql
output_formats: [csv, xlsx]  # csv | xlsx | txt (pdf y gsheets: ver Roadmap)
default_recipients: []
params_schema:
  billing_month_date: DATE   # tipos BigQuery: STRING, INT64, FLOAT64, BOOL, DATE, DATETIME, TIMESTAMP
params_defaults:
  billing_month_date: previous_month_first_day  # unico sentinel soportado hoy
filename_date_param: billing_month_date         # opcional: de dónde sale {year}{month} en el nombre de archivo
description: "..."
```

`mi_reporte.sql`: la query tal cual, usando **parámetros nativos de BigQuery**
(`@billing_month_date`), nunca `str.format()` ni f-strings — así se elimina la
superficie de inyección que tenía el notebook original.

Dos ejemplos reales migrados del notebook están en
`config/reports/custom/protec_usuarios_activos.*` (sin parámetros) y
`config/reports/custom/runa_colaboradores_chats_mensual.*` (con
`billing_month_date`). Los 12 reportes restantes del notebook siguen uno de
estos dos patrones y se pueden migrar de la misma forma.

## Plantillas de layout (PDF/HTML)

Inspirado en Oracle BI Publisher: separa **datos** (la query) de **layout**
(cómo se ve el PDF/HTML) — una plantilla se escribe una vez y se reutiliza
entre reportes/clientes, sin tocar código Python.

- Formatos `pdf` y `html` usan una plantilla Jinja2
  (`config/templates/<nombre>.html.j2`). Sin `template:` en la config del
  reporte, usan una plantilla default empaquetada (tabla simple con título y
  fecha — mismo look genérico de siempre).
- `html` es un formato nuevo: preview rápido sin generar PDF, o para
  destinos que aceptan HTML directamente.
- Variables disponibles en cualquier plantilla (ver
  `rendering/template_engine.py`): `title`, `report` (`id`/`name`/
  `description`), `client` (`id`/`display_name`/`branding` — `None` si no
  hay cliente registrado), `params` (resueltos), `generated_at`, `rows`,
  `columns`, `row_count`, `truncated` (PDF recorta a 500 filas, igual que
  antes).
- Branding por cliente: `ClientConfig.branding` (ej. `logo_url`,
  `primary_color`, ver `new-client --bq-param` no aplica aquí — se edita el
  YAML del cliente directamente por ahora) para que una sola plantilla sirva
  a varios clientes sin duplicarla.

**Ejemplo incluido:** `config/templates/branded_summary.html.j2` — logo +
nombre del cliente, parámetros resueltos, tabla de datos. Cópialo/adáptalo
para un reporte real:

```bash
python -m reporting_automation new-report --id ... --output-formats html,pdf \
    --template branded_summary ...
```

**Nota de compatibilidad:** el comando `ask` (preguntas en lenguaje natural)
sigue usando `pdf_renderer.build_pdf()` con la misma firma de siempre — por
dentro ahora renderiza vía la plantilla default + WeasyPrint en vez de
ReportLab, pero no requirió ningún cambio en `ask.py`.

**Setup local (WeasyPrint necesita librerías de sistema):**
```bash
brew install pango   # macOS -- trae cairo/gdk-pixbuf como dependencias
```
En Homebrew sobre Apple Silicon, además hay que decirle a macOS dónde están
esas librerías (si no, `import weasyprint` falla con
`cannot load library 'libgobject-2.0-0'`):
```bash
export DYLD_LIBRARY_PATH=/opt/homebrew/lib   # agregar a ~/.zshrc para que sea permanente
```
En Docker (`Dockerfile`/`Dockerfile.ui`) ya está resuelto con
`apt-get install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info`
(Linux no tiene este problema de rutas).

## Reportes reutilizables entre clientes (`shared`) + mapeo de clientes

Todo lo de esta sección (`ClientConfig`/`config/clients/*.yaml`) es CLI y
`run-batch` — la UI de Streamlit resuelve el cliente directamente desde
BigQuery (`DimCompanies`), ver sección "UI (Streamlit)" más abajo.

Un reporte `custom` esta atado a un `client_id` fijo en su config (como los
dos ejemplos de arriba). Cuando el mismo reporte debe poder correr para
*cualquier* compañía cliente (ej. "videollamadas completadas en el mes"), en
vez de duplicar el reporte por cliente:

1. Escribe el filtro de compañía como parámetro nativo de BigQuery
   (`@id_company`) en vez de hardcodearlo.
2. Regístralo con `--kind shared` (sin `--client` fijo):
   ```bash
   python -m reporting_automation new-report \
       --id videollamadas_completadas_mes \
       --name VideollamadasCompletadas \
       --kind shared \
       --sql-file ./videollamadas_mes.sql \
       --output-formats csv \
       --param id_company:STRING \
       --param billing_month_date:DATE \
       --param-default billing_month_date=previous_month_first_day \
       --filename-date-param billing_month_date
   ```
3. Registra los clientes con su `idCompany` real. Uno por uno:
   ```bash
   python -m reporting_automation new-client --id protec --display-name "Protec" \
       --bq-param id_company=498cb81c5ba7325f
   ```
   O todos de una vez desde el catálogo maestro de compañías
   (`idCompanyMD,idCompany,CompanyName,CompanyFiscalName,CompanyIsActive`):
   ```bash
   python -m reporting_automation import-clients --csv /ruta/a/id_clientes.csv
   python -m reporting_automation list-clients
   ```
   `import-clients` slugifica `CompanyName` para el id (`"Avanza Seguros"` →
   `avanza_seguros`), omite filas inactivas por defecto (`--all` para
   incluirlas) y no pisa clientes ya registrados salvo que pases
   `--overwrite`. `config/clients/` está en este repo con **183 clientes
   reales** importados así del catálogo de la compañía.
4. Corre el mismo reporte para cualquiera de ellos sin repetir el hash a mano:
   ```bash
   python -m reporting_automation run --report videollamadas_completadas_mes --client protec --output-dir ./out
   python -m reporting_automation run --report videollamadas_completadas_mes --client avanza_seguros --output-dir ./out
   ```

`--client` sigue funcionando como etiqueta libre si no hay `ClientConfig`
registrado para ese id (comportamiento de Fase 1 sin cambios); el mapeo solo
se activa cuando existe `config/clients/<id>.yaml`. Prioridad de valores para
cada parámetro: `--param` explícito > `bq_params` del cliente > default del
reporte (`orchestrator.resolve_params`).

**Una cuenta (`idCompany`) puede contener varias marcas/segmentos internos**
distinguidos por `UserCompanyGroupCode` — esto es normal en el modelo de
datos, no un error. `id_company` en un `ClientConfig` siempre representa "la
cuenta completa"; si algún día se necesita un reporte scoped a una marca o
segmento específico dentro de una cuenta (ej. "solo Runa" o "solo TuDoc"
dentro de "Meeting-doctors", "solo Yoigo" dentro de "DoctorGo"), ese reporte
debe filtrar además por `UserCompanyGroupCode` en su SQL — como ya hace
`runa_colaboradores_chats_mensual` — en vez de depender solo de `id_company`.

## Arquitectura (Fase 1)

```
config/reports/**/*.{yaml,sql}  →  ReportRegistry  →  orchestrator.run_report()
                                                          │
                                        ┌─────────────────┼─────────────────┐
                                        ▼                                   ▼
                              BigQueryExecutor.run()             rendering.factory.get_renderer()
                              (parámetros nativos BQ)             (csv / xlsx / txt → disco local)
```

`orchestrator.run_report()` es el equivalente al "Def Main" del diagrama. Está
escrito contra `Protocol`s (`QueryRunner`, `Renderer`) para que Fase 2/3 puedan
añadir delivery, secretos y tracing sin tocar esta capa.

## Roadmap (Fases 2-4)

### Fase 2 — Delivery (correo + GDrive) + Secret Manager (código listo, sin desplegar)

Un reporte generado (local, `run-batch`, o vía Fase 3) no se manda a nadie
solo — hace falta pedirlo explícitamente. Detrás de un flag `--deliver`
(apagado por defecto en `run`/`run-batch`, para que correr un reporte de
prueba nunca dispare un envío real):

- `delivery/email_delivery.py`: manda los archivos generados como adjuntos
  por SMTP. Credenciales via Secret Manager, secret `internal-smtp`
  (`{host, port, user, password, from_address}`) — leído solo al momento de
  enviar, así que construir el objeto no requiere que el secret ya exista.
- `delivery/gdrive_delivery.py`: porta tal cual la lógica del notebook
  original (buscar/crear carpeta, subir o actualizar archivo si ya existe)
  a `<gdrive_root_folder_id>/<cliente>/<year><mes>/<archivo>`. Usa las
  mismas credenciales ADC que BigQuery/GCS, sin secret propio.
- `delivery/dispatch.py`: el "glue" — `resolve_recipients()` (prioridad:
  `--recipients`/`receptores` explícito > `default_recipients` del reporte)
  y `dispatch_delivery()`, que recorre `report.delivery_channels`. No toca
  `orchestrator.py` (mismo principio de siempre: capas nuevas se agregan por
  fuera, sin tocar el core).
- **FTP no está implementado a propósito** — no hay un servidor FTP real de
  ningún cliente contra el cual probarlo. Queda declarado en
  `DeliveryChannel.FTP` (igual que `pdf`/`gsheets` en `rendering/factory.py`)
  y falla con un mensaje claro (`"no esta implementado"`) si se configura.
- Mecanismo de correo elegido: **SMTP con contraseña de aplicación** (no
  Gmail API/domain-wide delegation) — no requiere admin de Workspace, se
  configura en minutos.

**Cómo activarlo en un reporte:**
```bash
python -m reporting_automation new-report --id ... --delivery-channel email --delivery-channel gdrive \
    --default-recipient cliente@empresa.com ...
python -m reporting_automation run --report ... --client ... --deliver
python -m reporting_automation run-batch --deliver   # aplica a todo config/monthly_batch.yaml
```

**Probado en vivo (sin secret/carpeta reales configurados):** el reporte se
genera bien contra BigQuery real, y ambos canales fallan con mensajes claros
(`secret internal-smtp not found`, `falta gdrive_root_folder_id`) — exactamente
lo esperado sin haber creado esos recursos todavía. Ninguno de los 20
reportes actuales tiene `delivery_channels` configurado, así que nada de
esto dispara un envío real hasta que alguien lo configure a propósito.

**Pendiente — comandos para más adelante (NO ejecutados en esta ronda):**
```bash
# Secret de SMTP (cuando exista la cuenta/contraseña de app)
echo -n '{"host":"smtp.gmail.com","port":587,"user":"...","password":"...","from_address":"reportes@meetingdoctors.com"}' | \
  gcloud secrets create internal-smtp --data-file=- --project=data-prd-424213

# Carpeta raiz de Drive: crear/elegir carpeta o Shared Drive, tomar su ID,
# y ponerlo en config/settings.yaml como gdrive_root_folder_id
```

### Fase 3 — Pub/Sub → Cloud Run Service (código listo, sin desplegar)

**Cambio respecto al plan original:** la Fase 1 había elegido Cloud Run
*Job* como cómputo. Al llegar a esta fase se confirmó que **Eventarc no
soporta Cloud Run Jobs como destino de un trigger de Pub/Sub** (no existe
`--destination-run-job`, solo `--destination-run-service`). Como cada
mensaje dispara un solo reporte (segundos), un **Cloud Run Service** es la
opción correcta — es el destino nativo de Eventarc, sin necesitar un
servicio "dispatcher" intermedio.

Lo que ya existe y está probado (con mocks, sin tocar GCP real):

- `main_entrypoint.py`: Flask app que recibe el push de Pub/Sub, decodifica
  `{reporte, cliente, receptores, params, window}` (base64 + JSON), corre
  `orchestrator.run_report` (el mismo que usan `run`/`run-batch`), y sube
  los archivos generados a un bucket de GCS vía `gcs_landing.py`
  (`gs://<bucket>/<cliente>/<year>/<mes>/<archivo>`). Si el reporte tiene
  `delivery_channels` configurado, despues despacha `receptores` por
  correo/GDrive via `delivery/dispatch.py` (`EmailDelivery`/`GDriveDelivery`)
  -- un fallo de entrega se loguea pero no tumba el 200 (ver
  `main_entrypoint.handle_push`), para no reprocesar el reporte solo porque
  el envio fallo.
- `logging_setup.py`: usa Google Cloud Logging si detecta que corre en
  Cloud Run (`K_SERVICE` seteado), o logging de consola en local/tests.
- `Dockerfile`: imagen del servicio (gunicorn sirviendo el Flask app).
- `generate-scheduler-jobs`: imprime los 23 comandos
  `gcloud scheduler jobs create pubsub` (uno por línea de
  `config/monthly_batch.yaml`) — no crea nada, solo genera el texto.

**El codigo de entrega (Fase 2) esta escrito y anda, pero hoy no hace nada
de verdad, por dos motivos distintos:**

1. Ningun reporte tiene `delivery_channels` configurado todavia (se puede
   declarar via `new-report --delivery-channel` en la CLI, o desde la
   pestaña "Crear reporte nuevo" de la UI). Sin eso, `main_entrypoint.py`
   ni siquiera intenta despachar nada.
2. Aunque un reporte lo tuviera, la infraestructura que ese codigo necesita
   todavia no existe: el secret `internal-smtp` en Secret Manager (host,
   port, user, password, from_address) no esta creado, y
   `gdrive_root_folder_id` en `config/settings.yaml` sigue en `null`.

Hasta que se resuelvan esos dos puntos, aterrizar en GCS sigue siendo la
entrega real (alguien baja el archivo del bucket) y el registro de
auditoría que pide el diagrama.

**Región confirmada:** `europe-southwest1` — es la misma región donde vive
el dataset `03_BaseModel` de BigQuery (verificado con
`bq.get_dataset(...).location`), y coincide con "Madrid" del diagrama.

**Probado localmente sin desplegar:** con el test client de Flask contra
BigQuery real (`python /tmp/smoke_entrypoint.py`-style, ver
`tests/unit/test_main_entrypoint.py` para el patrón) — la query corre bien.

**Bucket de trace ya creado:** `gs://reporting-automation-trace`
(`europe-southwest1`, uniform bucket-level access), verificado subiendo y
borrando un archivo de prueba con el mismo código que usa `gcs_landing.py`.
Ya no es un nombre propuesto — el aterrizaje en GCS funciona de punta a
punta desde la CLI y la UI.

**Pendiente — comandos de despliegue, NO ejecutados** (correr uno por uno,
revisando cada paso, cuando se decida desplegar de verdad):

**Sobre la service account:** `iam.serviceAccounts.create` no esta otorgado
hoy (verificado con `testIamPermissions`), asi que crear una SA dedicada
para esto no es una opcion todavia. Los comandos de abajo usan la
**default compute service account**
(`901461160778-compute@developer.gserviceaccount.com`) para todo: correr
el Cloud Run Service Y invocarlo desde Eventarc. Esto evita el gap real que
tenia esta seccion antes: `gcloud run deploy` sin `--service-account`
corre como esa SA por default, pero el trigger de Eventarc necesitaba una
SA explicita con `run.invoker` sobre el servicio -- sin ese paso (que
faltaba aca) Eventarc recibe un 403 al intentar invocar el servicio
`--no-allow-unauthenticated`.

```bash
# Una sola vez: habilitar APIs y crear el tema (el bucket ya existe, ver arriba)
# NOTA: esto tambien necesita permisos de Cloud Build que hoy no estan
# otorgados (ni siquiera cloudbuild.builds.create) -- confirmar antes.
gcloud services enable run.googleapis.com eventarc.googleapis.com pubsub.googleapis.com --project=data-prd-424213
gcloud pubsub topics create reporting-automation-triggers --project=data-prd-424213

# Build + deploy del Cloud Run Service -- SA explicita (ver nota arriba)
gcloud run deploy reporting-automation --source . --region=europe-southwest1 \
    --no-allow-unauthenticated --project=data-prd-424213 \
    --service-account=901461160778-compute@developer.gserviceaccount.com

# Dar a esa misma SA permiso para invocar el servicio -- sin esto, el
# trigger de mas abajo recibe 403 al intentar disparar el Cloud Run Service.
gcloud run services add-iam-policy-binding reporting-automation \
    --region=europe-southwest1 --project=data-prd-424213 \
    --member="serviceAccount:901461160778-compute@developer.gserviceaccount.com" \
    --role="roles/run.invoker"

# Trigger de Eventarc: Pub/Sub -> Cloud Run Service (misma SA de arriba)
gcloud eventarc triggers create reporting-automation-trigger \
    --location=europe-southwest1 --destination-run-service=reporting-automation \
    --destination-run-region=europe-southwest1 \
    --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished" \
    --transport-topic=projects/data-prd-424213/topics/reporting-automation-triggers \
    --service-account=901461160778-compute@developer.gserviceaccount.com

# Los 23 Cloud Scheduler jobs:
python -m reporting_automation generate-scheduler-jobs   # imprime los comandos, no los corre
```

### Fase 4 — CI/CD (config lista, sin trigger real)

Lo que ya existe:

- `cloudbuild.yaml`: `lint` (ruff) → `test` (pytest) → `build` (imagen
  Docker) → `push` (Artifact Registry) → `deploy` (Cloud Run Service).
  **No se puede correr todavia** con los permisos actuales: ni
  `cloudbuild.builds.create` esta otorgado (verificado con
  `testIamPermissions`), asi que `gcloud builds submit` fallaria con un
  permiso denegado antes de llegar a ejecutar un solo paso.
- El proyecto ya tiene remoto conectado en GitHub
  (`github.com/danielaguinaga-create/reporting-automation`, rama `main`
  trackeando `origin/main`) y viene pusheando ahi durante todo el
  desarrollo.
- `ruff` configurado como linter (`pyproject.toml`, reglas `E`/`F`/`I`,
  `line-length=110`) — `ruff check src tests` corre limpio.

Lo que falta: (1) un rol de Cloud Build (ej. `roles/cloudbuild.builds.editor`)
para poder correr `cloudbuild.yaml` siquiera a mano, y (2) crear el trigger
apuntando al remoto que ya existe (`gcloud builds triggers create github ...`,
tambien bloqueado por el mismo permiso faltante). El repo con remoto ya no
es el bloqueo -- el permiso de Cloud Build si lo es.

### TODOs abiertos (decisiones pendientes de la empresa, no inventadas aquí)

1. ~~Nombre final del bucket GCS de trace~~ — resuelto:
   `reporting-automation-trace`, ya creado en `europe-southwest1`.
2. Nombre final del tema de Pub/Sub (y si hay uno por ambiente dev/prod).
3. Nombre del repositorio de Artifact Registry (`cloudbuild.yaml` asume
   uno llamado `reporting-automation`, que todavia no existe -- solo existe
   `data-jobs`, de otra pipeline) y estrategia de ramas para el trigger.
   El repo/remoto ya existen (GitHub); lo que falta es el permiso de Cloud
   Build para poder crear el trigger o siquiera probar `cloudbuild.yaml`
   a mano.
4. Dirección exacta de alertas de fallo ("data/logging") — todavía no hay
   mecanismo de alertas, solo logging.
5. Estrategia de auth para GDrive: Shared Drive con la service account como
   miembro (recomendado) vs. impersonation — necesita confirmación de un admin
   de Workspace. También falta confirmar si el ADC de quien corre esto tiene
   scope de Drive (`gcloud auth application-default login --scopes=...,drive`
   si hace falta agregarlo).
6. `gdrive_root_folder_id` real — hoy `null`, nadie ha creado/elegido la
   carpeta o Shared Drive raíz.
7. Rotación de la contraseña de aplicación del SMTP, y si conviene migrar a
   Gmail API/domain-wide delegation más adelante.
8. FTP: cuando exista un cliente real que lo pida, con sus datos de conexión
   (host/puerto, FTP vs FTPS vs SFTP).
9. Roles IAM finales para la service account del Cloud Run Service
   (BigQuery dataViewer/jobUser sobre `03_BaseModel`, Storage objectAdmin
   sobre el bucket de trace, Secret Manager secretAccessor sobre
   `internal-smtp`).
10. Clasificación de errores retryable vs. permanentes en
    `main_entrypoint.py` — hoy cualquier fallo de `run_report` o de subida a
    GCS devuelve 500 (Pub/Sub reintenta), sin distinguir un reporte que nunca
    va a funcionar (ej. id inexistente) de un fallo transitorio.
11. Semántica exacta de `gsheets` como formato de salida: ¿Google Sheet en
    vivo (Sheets API/`gspread`) o un export alternativo a Drive?
12. Librería de PDF: WeasyPrint (necesita Pango/Cairo en la imagen del
    contenedor, layout vía HTML/CSS) vs. ReportLab (puro Python, layout
    manual).
13. Contenido/formato exacto de la salida `txt` — ninguno de los reportes
    actuales lo usa, no hay precedente que replicar.

## UI (Streamlit) — código listo y probado, pendiente de que un admin la despliegue

Capa de UI del diagrama, alcance v1: elegir reporte + cliente (+ parámetros)
desde un formulario web, correrlo contra BigQuery real, y descargar el
archivo. **No** dispara delivery (correo/GDrive) desde la UI, sin
excepción — eso solo pasa desde la CLI (`run --deliver`) o, una vez
desplegada, automáticamente desde Fase 3 (Pub/Sub → Cloud Run, ver esa
sección más arriba), para que una herramienta usada por varias personas no
pueda mandarle algo real a un cliente por accidente.

- `src/reporting_automation/ui_app.py`: un solo script de Streamlit, reusa
  `ReportRegistry`, `orchestrator.run_report`, `BigQueryExecutor` tal cual —
  ninguna lógica nueva, la UI es una capa de presentación sobre lo que ya
  existe.
- `Dockerfile.ui`: imagen para un Cloud Run Service separado del de Fase 3
  (`reporting-automation-ui` vs `reporting-automation`).
- Probado: `tests/unit/test_ui_app.py` (con `streamlit.testing.v1.AppTest`,
  BigQuery mockeado) y en vivo contra BigQuery real (listado de reportes
  poblado desde `ReportRegistry`, corrida real de `chats_detalle`, 24
  filas, botón de descarga generado).

### Catálogo de compañías: BigQuery, no `config/clients/*.yaml`

El selector de cliente en la UI (`src/reporting_automation/company_catalog.py`,
función `fetch_active_companies`) se llena en vivo desde
`data-prd-424213.03_BaseModel.DimCompanies` (`idCompany`/`CompanyName`,
solo `CompanyIsActive`), no desde `config/clients/*.yaml` — así aparece
cualquier compañía real, no solo las ~183 ya registradas como `ClientConfig`.
**Esto es solo en la UI**: la CLI (`run --client`, `run-batch`,
`new-client`, `import-clients`) sigue usando `ClientConfig`/
`config/clients/*.yaml` exactamente igual que antes.

Trade-off aceptado: como la UI ya no consulta `ClientConfig`, los PDF/HTML
generados desde la UI **no llevan branding** (`logo_url`/`primary_color`)
aunque el cliente sí tenga uno configurado para la CLI — las plantillas ya
manejan branding vacío con un fallback prolijo, así que no rompe nada, solo
se pierde el look personalizado en corridas hechas desde la UI.

### Crear un reporte nuevo desde la UI (wizard)

La pestaña "Crear reporte nuevo" registra un reporte ad-hoc (YAML + SQL)
sin pasar por la CLI: id/nombre/descripción, alcance (`shared` o
`custom` con una compañía elegida del catálogo de BigQuery), variables
(una por línea, `nombre:TIPO_BQ` -- mismo formato que `--param` en
`new-report`), un checkbox para agregar la ventana de tiempo
(`start_date`/`end_date`, ver sección "Ventanas de tiempo" más arriba), la
SQL pegada a mano, formatos de salida, y canales de entrega + destinatarios
por defecto (opcional -- solo declara `delivery_channels`/
`default_recipients` en la config, no dispara ningún envío desde la UI, ver
más abajo). El wizard no tiene selector de plantilla de layout (`template`)
-- un reporte creado desde la UI siempre usa la plantilla default; para una
plantilla especifica hay que usar `new-report --template` en la CLI.

**La SQL se guarda tal cual se pega, sin ningún chequeo de seguridad** —
mismo nivel de confianza que ya tiene `new-report` en la CLI hoy (que
tampoco valida la SQL). Al guardar, el reporte queda disponible de
inmediato en la pestaña "Correr un reporte" (no hace falta reiniciar el
proceso de Streamlit). Lógica pura, testeable sin Streamlit/BigQuery, en
`src/reporting_automation/report_wizard.py`.

### Correrla localmente

```bash
./run_ui_local.sh   # o: streamlit run src/reporting_automation/ui_app.py
```
Abre `http://localhost:8501`. Usa tus credenciales ADC ya configuradas
(`gcloud auth application-default login`), igual que `run`/`run-batch`.

### Por qué el despliegue real no se hizo en esta sesión

Los primeros intentos (listar Artifact Registry, leer el IAM del proyecto,
listar servicios de Cloud Run) fallaron por permisos: en ese momento la
cuenta usada (`daniel.aguinaga@meetingdoctors.com`) tenía acceso a BigQuery
pero ningún permiso de Cloud Run / Artifact Registry / IAM en
`data-prd-424213`. **Desde entonces se otorgaron `run.admin`,
`artifactregistry.admin`, `iam.serviceAccountUser`, `iap.admin`,
`iap.settingsAdmin` y `oauthconfig.editor`** -- confirmado permiso por
permiso con `testIamPermissions` (no solo por nombre de rol): `run.admin` y
`artifactregistry.admin` habilitan de verdad `run.services.create/update` y
`artifactregistry.repositories.uploadArtifacts/create`, y de paso quedaron
otorgados (sin pedirlos explícitamente) `pubsub.topics.create` y
`serviceusage.services.enable`.

Lo que **sigue** bloqueando un despliegue real hoy:

- **Cloud Build**: ningún permiso otorgado, ni `cloudbuild.builds.create`
  -- el paso 1 de la guía de abajo (`gcloud builds submit`) fallaría con
  permiso denegado antes de ejecutar un solo paso. Sin esto no se puede
  usar `cloudbuild.yaml`/`cloudbuild.ui.yaml` ni a mano ni con trigger.
- **Solo para Fase 3** (no para la UI): faltan `eventarc.triggers.create`
  y `cloudscheduler.jobs.create` -- sin el primero no se puede conectar
  Pub/Sub con Cloud Run, sin el segundo no se pueden crear los jobs
  programados. `iam.serviceAccounts.create` tampoco está otorgado, por eso
  la sección de Fase 3 más arriba usa la default compute service account
  en vez de una dedicada.

Número de proyecto: `901461160778`.

### Guía de despliegue de la UI (roles ya otorgados a esta cuenta)

Roles necesarios en `data-prd-424213`, **ya otorgados** a la cuenta que usa
esta sesión: `roles/run.admin`, `roles/artifactregistry.admin`,
`roles/iam.serviceAccountUser`, y para IAP `roles/iap.admin` +
`roles/iap.settingsAdmin` + `roles/oauthconfig.editor`. Sigue faltando
Cloud Build (ver arriba) para poder correr el paso 1 de abajo.

```bash
# 0. Si es la primera vez que se usa Artifact Registry en el proyecto:
gcloud artifacts repositories create reporting-automation \
    --repository-format=docker --location=europe-southwest1 \
    --project=data-prd-424213

# 1. Build + push de la imagen de la UI (usa Dockerfile.ui, no el Dockerfile de Fase 3)
gcloud builds submit --project=data-prd-424213 \
    --config=cloudbuild.ui.yaml .
# (cloudbuild.ui.yaml hace: docker build -f Dockerfile.ui -t <imagen> . && push)

# 2. Deploy del Cloud Run Service
gcloud run deploy reporting-automation-ui \
    --image=europe-southwest1-docker.pkg.dev/data-prd-424213/reporting-automation/ui:latest \
    --region=europe-southwest1 --no-allow-unauthenticated --project=data-prd-424213

# 3. La cuenta de servicio con la que corre el servicio (por defecto, la de
#    Compute Engine del proyecto) necesita permisos de BigQuery -- sin esto
#    la UI se despliega pero "Ejecutar reporte" falla para todo el equipo:
gcloud projects add-iam-policy-binding data-prd-424213 \
    --member=serviceAccount:901461160778-compute@developer.gserviceaccount.com \
    --role=roles/bigquery.dataViewer
gcloud projects add-iam-policy-binding data-prd-424213 \
    --member=serviceAccount:901461160778-compute@developer.gserviceaccount.com \
    --role=roles/bigquery.jobUser

# 4. Habilitar IAP sobre el servicio
gcloud run services update reporting-automation-ui \
    --region=europe-southwest1 --iap --project=data-prd-424213

# 5. Dar al agente de servicio de IAP permiso para invocar el Cloud Run Service
gcloud run services add-iam-policy-binding reporting-automation-ui \
    --region=europe-southwest1 \
    --member=serviceAccount:service-901461160778@gcp-sa-iap.iam.gserviceaccount.com \
    --role=roles/run.invoker --project=data-prd-424213

# 6. Dar acceso a todo el dominio @meetingdoctors.com (intentar primero;
#    la sintaxis exacta de restriccion por dominio en IAP no la pude
#    confirmar en la documentacion -- si este comando falla, usar el
#    siguiente, repetido por cada persona del equipo):
gcloud iap web add-iam-policy-binding \
    --member=domain:meetingdoctors.com --role=roles/iap.httpsResourceAccessor \
    --region=europe-southwest1 --resource-type=cloud-run \
    --service=reporting-automation-ui --project=data-prd-424213

# Alternativa por usuario, si el comando de dominio falla:
gcloud iap web add-iam-policy-binding \
    --member=user:persona@meetingdoctors.com --role=roles/iap.httpsResourceAccessor \
    --region=europe-southwest1 --resource-type=cloud-run \
    --service=reporting-automation-ui --project=data-prd-424213
```

**Nota:** si es la primera vez que se usa IAP en este proyecto, GCP pedirá
configurar la pantalla de consentimiento OAuth (tipo "Internal" si el
proyecto pertenece a la organización de Google Workspace de
meetingdoctors.com — así el login queda restringido al dominio
automáticamente; "External" si no).
