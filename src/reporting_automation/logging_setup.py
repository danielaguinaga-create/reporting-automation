from __future__ import annotations

import logging
import os


def is_running_on_cloud_run() -> bool:
    """Cloud Run inyecta K_SERVICE automaticamente en el entorno del contenedor."""
    return bool(os.environ.get("K_SERVICE"))


def configure_logging() -> None:
    """Instala el handler de Google Cloud Logging si corre en Cloud Run.

    Localmente (K_SERVICE ausente, ej. tests o `run`/`run-batch` desde la
    terminal) se deja el logging estandar de consola -- no tiene sentido
    exportar logs de una corrida manual a Cloud Logging.
    """
    if not is_running_on_cloud_run():
        logging.basicConfig(level=logging.INFO)
        return

    import google.cloud.logging as cloud_logging

    client = cloud_logging.Client()
    client.setup_logging(log_level=logging.INFO)
