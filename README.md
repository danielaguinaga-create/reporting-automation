# reporting-automation

Motor de generación de reportes para clientes de Meeting Doctors. Reemplaza el
notebook de Colab `Reportes_Clientes_Archivos_Extra.ipynb` (14 queries de
BigQuery hardcodeadas en un diccionario Python) por un registro declarativo de
reportes (YAML + SQL), siguiendo el diagrama de arquitectura interno
"Reporting Automation".

**Estado actual: Fase 1 completa y en uso real; Fases 2 y 3 con código listo,
sin desplegar recursos reales de GCP todavía.** Fase 1 implementa el "Def
Main" del diagrama de forma local y testeable: registro de reportes (20
reportes reales registrados, 183 clientes importados), ejecución contra
BigQuery, renderizado a CSV/XLSX/TXT, `run-batch` para la corrida mensual
completa, y una CLI. Fase 2 (delivery por correo + GDrive) y Fase 3 (disparo
por Cloud Scheduler → Pub/Sub → Cloud Run Service) tienen el código escrito
y probado con mocks (y contra BigQuery real donde aplica), pero **no existen
todavía el secret de SMTP, la carpeta de Drive, el bucket, ni el tema de
Pub/Sub** — son pasos explícitos pendientes, ver
[Roadmap](#roadmap-fases-2-4). FTP no está implementado (sin un cliente real
para probarlo). La **UI** (Streamlit) también tiene código listo y probado
(ver sección UI), pero su despliegue real a Cloud Run + IAP está bloqueado
por permisos de GCP que la cuenta usada en esta sesión no tiene — queda
documentado para que un admin del proyecto lo despliegue. API (capa del
diagrama) sigue fuera de alcance.

## Requisitos

- Python 3.12+
- Para ejecutar reportes contra BigQuery real: credenciales válidas (`gcloud
  auth application-default login`) con acceso de lectura al proyecto
  `data-prd-424213`, dataset `03_BaseModel`.

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

## Reportes reutilizables entre clientes (`shared`) + mapeo de clientes

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
  `{reporte, cliente, receptores, params}` (base64 + JSON), corre
  `orchestrator.run_report` (el mismo que usan `run`/`run-batch`), y sube
  los archivos generados a un bucket de GCS vía `gcs_landing.py`
  (`gs://<bucket>/<cliente>/<year>/<mes>/<archivo>`). `receptores` no dispara
  ningún envío todavía (no hay delivery real, ver Fase 2) — solo queda en el
  log estructurado para trazabilidad futura.
- `logging_setup.py`: usa Google Cloud Logging si detecta que corre en
  Cloud Run (`K_SERVICE` seteado), o logging de consola en local/tests.
- `Dockerfile`: imagen del servicio (gunicorn sirviendo el Flask app).
- `generate-scheduler-jobs`: imprime los 23 comandos
  `gcloud scheduler jobs create pubsub` (uno por línea de
  `config/monthly_batch.yaml`) — no crea nada, solo genera el texto.

**Por qué el bucket de GCS y no delivery real:** Fase 2 (correo/FTP/GDrive
con Secret Manager) todavía no existe. Aterrizar en GCS es la entrega
interina (alguien baja el archivo del bucket) y a la vez el registro de
auditoría que pide el diagrama.

**Región confirmada:** `europe-southwest1` — es la misma región donde vive
el dataset `03_BaseModel` de BigQuery (verificado con
`bq.get_dataset(...).location`), y coincide con "Madrid" del diagrama.

**Probado localmente sin desplegar:** con el test client de Flask contra
BigQuery real (`python /tmp/smoke_entrypoint.py`-style, ver
`tests/unit/test_main_entrypoint.py` para el patrón) — la query corre bien;
solo falla la subida a GCS con un 404 claro porque el bucket todavía no
existe. Eso es exactamente lo esperado sin haber desplegado nada.

**Pendiente — comandos de despliegue, NO ejecutados** (correr uno por uno,
revisando cada paso, cuando se decida desplegar de verdad):

```bash
# Una sola vez: habilitar APIs, crear bucket y tema
gcloud services enable run.googleapis.com eventarc.googleapis.com pubsub.googleapis.com --project=data-prd-424213
gcloud storage buckets create gs://reporting-automation-trace --project=data-prd-424213 --location=europe-southwest1
gcloud pubsub topics create reporting-automation-triggers --project=data-prd-424213

# Build + deploy del Cloud Run Service
gcloud run deploy reporting-automation --source . --region=europe-southwest1 \
    --no-allow-unauthenticated --project=data-prd-424213

# Trigger de Eventarc: Pub/Sub -> Cloud Run Service
gcloud eventarc triggers create reporting-automation-trigger \
    --location=europe-southwest1 --destination-run-service=reporting-automation \
    --destination-run-region=europe-southwest1 \
    --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished" \
    --transport-topic=projects/data-prd-424213/topics/reporting-automation-triggers \
    --service-account=<SA>@data-prd-424213.iam.gserviceaccount.com

# Los 23 Cloud Scheduler jobs:
python -m reporting_automation generate-scheduler-jobs   # imprime los comandos, no los corre
```

### Fase 4 — CI/CD (config lista, sin trigger real)

Lo que ya existe:

- `cloudbuild.yaml`: `lint` (ruff) → `test` (pytest) → `build` (imagen
  Docker) → `push` (Artifact Registry) → `deploy` (Cloud Run Service). Se
  puede correr a mano desde ya: `gcloud builds submit --config=cloudbuild.yaml`.
- El proyecto ya es un repo git local (`git init` + primer commit hecho).
- `ruff` configurado como linter (`pyproject.toml`, reglas `E`/`F`/`I`,
  `line-length=110`) — `ruff check src tests` corre limpio.

Lo que falta (requiere una decisión de dónde vive el repo, no inventada
aquí): conectar un **remoto** (GitHub, GitLab, o Cloud Source Repos) —
`git remote add origin <url> && git push -u origin main` — y crear el
trigger de Cloud Build apuntando a ese remoto
(`gcloud builds triggers create github/gitlab/...`). Sin remoto, un trigger
no tiene de dónde disparar; mientras tanto, `cloudbuild.yaml` sirve para
correr el pipeline a mano.

### TODOs abiertos (decisiones pendientes de la empresa, no inventadas aquí)

1. Nombre final del bucket GCS de trace (`reporting-automation-trace` es
   propuesto, no creado).
2. Nombre final del tema de Pub/Sub (y si hay uno por ambiente dev/prod).
3. Detalles del trigger de Cloud Build (host del repo, estrategia de ramas,
   nombre del Artifact Registry) — este proyecto ni siquiera es un repo git
   todavía.
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
archivo. **No** dispara delivery (correo/GDrive) desde la UI — eso sigue
siendo solo CLI (`run --deliver`), para que una herramienta usada por varias
personas no pueda mandarle algo real a un cliente por accidente.

- `src/reporting_automation/ui_app.py`: un solo script de Streamlit, reusa
  `ReportRegistry`, `ClientRegistry`, `orchestrator.run_report`,
  `BigQueryExecutor` tal cual — ninguna lógica nueva, la UI es una capa de
  presentación sobre lo que ya existe.
- `Dockerfile.ui`: imagen para un Cloud Run Service separado del de Fase 3
  (`reporting-automation-ui` vs `reporting-automation`).
- Probado: `tests/unit/test_ui_app.py` (con `streamlit.testing.v1.AppTest`,
  BigQuery mockeado) y en vivo contra BigQuery real (20 reportes listados,
  corrida real de `chats_detalle`, 24 filas, botón de descarga generado).

### Correrla localmente

```bash
./run_ui_local.sh   # o: streamlit run src/reporting_automation/ui_app.py
```
Abre `http://localhost:8501`. Usa tus credenciales ADC ya configuradas
(`gcloud auth application-default login`), igual que `run`/`run-batch`.

### Por qué el despliegue real no se hizo en esta sesión

Intenté los primeros pasos reales (listar Artifact Registry, leer el IAM del
proyecto, listar servicios de Cloud Run) y los tres fallaron por permisos:
la cuenta usada (`daniel.aguinaga@meetingdoctors.com`) tiene acceso a
BigQuery pero **ningún permiso de Cloud Run / Artifact Registry / IAM** en
`data-prd-424213`. No es algo que se pueda resolver desde el código — hace
falta que alguien con rol de administrador en el proyecto otorgue esos
permisos, o corra el despliegue directamente. Número de proyecto (obtenido
sin permisos especiales): `901461160778`.

### Guía de despliegue para quien tenga permisos de admin

Roles necesarios en `data-prd-424213`: `roles/run.admin`,
`roles/artifactregistry.admin`, `roles/iam.serviceAccountUser`, y para IAP
`roles/iap.admin` + `roles/iap.settingsAdmin` + `roles/oauthconfig.editor`.

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
