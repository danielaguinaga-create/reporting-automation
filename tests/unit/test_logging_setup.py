from reporting_automation.logging_setup import is_running_on_cloud_run


def test_is_running_on_cloud_run_false_when_no_k_service(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    assert is_running_on_cloud_run() is False


def test_is_running_on_cloud_run_true_when_k_service_set(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "reporting-automation")
    assert is_running_on_cloud_run() is True


def test_configure_logging_does_not_import_cloud_logging_locally(monkeypatch):
    """Localmente (sin K_SERVICE) configure_logging no debe requerir
    `google-cloud-logging` ni tocar credenciales -- solo logging.basicConfig.
    """
    monkeypatch.delenv("K_SERVICE", raising=False)
    from reporting_automation.logging_setup import configure_logging

    configure_logging()  # no debe lanzar excepcion
