class ReportingAutomationError(Exception):
    """Base para errores del paquete."""


class ReportNotFoundError(ReportingAutomationError):
    pass


class ReportConfigError(ReportingAutomationError):
    """La config de un reporte es invalida o su .sql no existe."""


class ClientConfigError(ReportingAutomationError):
    """La config de un cliente es invalida o esta duplicada."""
