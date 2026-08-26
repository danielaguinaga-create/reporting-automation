# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (requires Python 3.12+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
gcloud auth application-default login   # needed for anything that hits BigQuery

# Tests (no network calls; safe to run without GCP credentials)
pytest tests/ -q
pytest tests/unit/test_orchestrator.py -v          # single file
pytest tests/unit/test_orchestrator.py::test_name  # single test

# Lint
ruff check .

# CLI (installed as `reporting-automation`, or `python -m reporting_automation`)
python -m reporting_automation list-reports
python -m reporting_automation run --report <id> --client <id> --output-dir ./out
python -m reporting_automation run-batch --manifest config/monthly_batch.yaml --output-dir ./out
python -m reporting_automation new-report --id <id> --name <name> --sql-file <path> --kind shared|custom ...
python -m reporting_automation list-window-presets

# UI (Streamlit)
streamlit run src/reporting_automation/ui_app.py   # or ./run_ui_local.sh
```

**macOS (Apple Silicon) only**: WeasyPrint (PDF/HTML rendering) needs system
libs not bundled with the Python wheel: `brew install pango`, then
`export DYLD_LIBRARY_PATH=/opt/homebrew/lib` before running `pytest` or the
CLI directly. `ui_app.py` sets this itself at import time
(`os.environ.setdefault(...)`) because Streamlit relaunches the script via
macOS's `Python.app` launcher, which strips shell-level `DYLD_*` vars —
CLI/pytest invocations don't go through that launcher, so they still need
the env var set manually. Not needed on Linux/Docker (`DYLD_*` is ignored
there).

## Architecture

**Ports-and-adapters around one stable core.** `orchestrator.run_report()`
(`src/reporting_automation/orchestrator.py`) is the "Def Main": resolve a
report's config, resolve its params, execute SQL against BigQuery, render
to each declared output format, return a `ReportRunResult`. Everything else
— the CLI, the Streamlit UI, the batch runner, the Pub/Sub-triggered Cloud
Run entrypoint — is a thin caller of this same function. New capabilities
get added as `Protocol`-typed layers (`Renderer`, `Delivery`) rather than by
editing the orchestrator.

**Reports are data, not code.** Each report is a `<id>.yaml` +
`<id>.sql` pair under `config/reports/shared/` (parametrized, runs for any
client via `@id_company`) or `config/reports/custom/` (SQL hardcodes one
client). `ReportRegistry.load()` (`config/registry.py`) discovers them
recursively; there is no other place a report gets registered. `new-report`
(CLI) and the Streamlit "Crear reporte nuevo" tab (`report_wizard.py`) both
just write this same YAML+SQL pair via `config/scaffold.py`.

**Params resolution precedence** (`orchestrator.resolve_params`, in this
order, later wins): `params_defaults` sentinels (currently only
`previous_month_first_day`, resolved via `time_window.py`) → `window` preset
→ `client_params` (auto-injected `ClientConfig.bq_params`, e.g.
`id_company`) → explicit `params` (CLI `--param`, or UI form input). SQL
params are always bound as BigQuery native query parameters
(`bigquery_client.py`, `@name` placeholders) — never string
interpolation/f-strings into SQL.

**Time windows are convention, not config.** If a report declares
`start_date`/`end_date` (type `DATE`) in `params_schema`, the system
auto-recognizes it as a time window: `--window <preset>` on the CLI, a
preset dropdown + custom date range in the UI, both resolved via
`time_window.resolve_window()`. No new `ReportConfig` field needed — this
mirrors how `id_company` is auto-detected as "the client param" by name.
There is no equivalent auto-detection for other param names; anything else
declared in `params_schema` gets a plain text input in the UI.

**Rendering**: `rendering/factory.get_renderer()` dispatches by
`OutputFormat`. CSV/XLSX/TXT/PDF/HTML are implemented; `GSHEETS`/`FTP`-style
formats are declared in the enum but raise `NotImplementedError` until a
real use case exists — don't build those out speculatively. PDF/HTML share
one templating layer: `template_registry.py` discovers
`config/templates/*.html.j2` (opt-in per report via `ReportConfig.template`;
falls back to a packaged default under `rendering/templates/`),
`template_engine.py` builds the Jinja2 context, WeasyPrint converts HTML to
PDF. `pdf_renderer.build_pdf()` has a signature frozen for `ask.py`
compatibility (see below) — don't change it without checking that call site.

**Two front-ends, one core, diverging client-resolution strategies.** The
CLI/`run-batch` resolve clients via `ClientConfig`/`config/clients/*.yaml`
(curated, ~180 pre-registered clients, carries branding/display_name used
by templates). The Streamlit UI instead queries BigQuery's `DimCompanies`
live (`company_catalog.py`) so any real company shows up, not just
pre-registered ones — as a consequence the UI never passes `client_registry`
into `run_report()`, so **PDF/HTML branding is empty for UI-driven runs**
even when a `ClientConfig.branding` exists for that client. This is a
known, accepted tradeoff, not a bug.

**Batch runner** (`batch.py`, driven by `config/monthly_batch.yaml`) writes
each entry to `output_dir/<client>/`, never directly to `output_dir` — a
`shared` report run for two different clients in the same batch would
otherwise silently overwrite one client's file with the other's (this
happened for real during development).

**Delivery (email/GDrive) fires from two places, never from the UI.** The
CLI needs an explicit opt-in (`--deliver` flag on `run`/`run-batch`).
`main_entrypoint.py` (Fase 3, not deployed) has no such flag — it dispatches
delivery unconditionally whenever the pushed report's `delivery_channels` is
non-empty, since a Pub/Sub push has no interactive user to ask. The
Streamlit UI can never trigger delivery either way — deliberate, so a
multi-user web form can't accidentally email/upload something to a real
client.

**`ask.py`** is a separate natural-language-to-SQL path (Anthropic LLM +
`llm/sql_safety.py` guard against unsafe generated SQL) that also calls
`pdf_renderer.build_pdf()` directly — this is why that function's signature
is frozen (see Rendering above).

**Fase 3 (not deployed)**: `main_entrypoint.py` is a Flask app meant to sit
behind Eventarc/Pub/Sub on Cloud Run, calling the same `run_report()`.
Deployment commands are documented in the README but have not been run —
originally blocked on GCP IAM permissions the account didn't have. All the
permissions needed (Cloud Run, Artifact Registry, IAM, Eventarc, Cloud
Scheduler, Cloud Build) have since been granted and verified one by one
with `testIamPermissions` (see README, "Por qué el despliegue real no se
hizo en esta sesión") — deployment is now a decision to make, not a
permissions gap, and still hasn't been run for real in any session.

## Testing conventions

- Fake the BigQuery client at the `.query()`/`.to_dataframe()` boundary
  (see `FakeExecutor`/`FakeBigQueryClient`/`FakeQueryJob` patterns repeated
  across `tests/unit/test_orchestrator.py`, `test_bigquery_client.py`,
  `test_company_catalog.py`) — never mock deeper than that.
- Streamlit UI is tested with `streamlit.testing.v1.AppTest`
  (`tests/unit/test_ui_app.py`). **This test file runs against the real
  `config/` tree**, not fixtures — `ui_app.py` calls `load_settings()` with
  its hardcoded default path. Any test that exercises the "save a new
  report" wizard flow must monkeypatch `report_wizard.save_new_report`
  (not `ui_app.save_new_report` — `ui_app.py` does a `from ... import`, so
  patching the source module is what actually takes effect), never let it
  actually write into `config/reports/`.
- `st.data_editor` has no `AppTest` support as of the Streamlit version
  pinned here — avoid it for anything that needs test coverage; prefer
  plain widgets (`text_area`, `selectbox`, etc.).
- `AppTest` selectbox `.select(v)` takes the *raw* option value, not the
  `format_func`-displayed label — `.options` on the widget reflects the
  formatted labels, which is a different, easy-to-confuse thing.
- `BigQueryExecutor.run()` requires every name in `params_schema` to be
  present in resolved params or it raises — there's no concept of an
  optional query param.
- `main_entrypoint.py` builds its registries/BigQuery client/delivery
  factories lazily on first request (`_get_state()`, a module-level
  singleton), not at import time, so tests can mock `bigquery.Client`/
  `storage.Client`/`load_settings` before the first request. Any test
  touching `handle_push` must reset `main_entrypoint._state = None` in its
  fixture (see `tests/unit/test_main_entrypoint.py`'s `_patch_gcp_clients`)
  or it'll reuse whatever state the first test in the process happened to
  build.
