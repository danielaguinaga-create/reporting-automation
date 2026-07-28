from __future__ import annotations

from reporting_automation.config.models import DeliveryChannel
from reporting_automation.delivery.base import Delivery

_NOT_YET_IMPLEMENTED = {DeliveryChannel.FTP}


def get_delivery(
    channel: DeliveryChannel, delivery_factories: dict[DeliveryChannel, Delivery]
) -> Delivery:
    if channel in _NOT_YET_IMPLEMENTED:
        raise NotImplementedError(
            f"Delivery para {channel.value!r} no esta implementado en Fase 2 "
            "(no hay un servidor FTP real contra el cual probarlo, ver README)."
        )
    try:
        return delivery_factories[channel]
    except KeyError:
        raise ValueError(f"Canal de entrega desconocido: {channel!r}") from None
