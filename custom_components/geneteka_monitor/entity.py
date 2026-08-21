"""Wspólne dla wszystkich platform tej integracji."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def device_info(coordinator, entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Geneteka: {coordinator.surname}",
        model="Monitor nazwiska",
    )
