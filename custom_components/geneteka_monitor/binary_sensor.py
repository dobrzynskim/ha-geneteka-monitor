"""Binarny sensor dla Geneteka - Monitor Nazwisk."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import device_info as _device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GenetekaNewTodayBinarySensor(coordinator, entry)])


class GenetekaNewTodayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Włączony, gdy dzisiaj przybył choć jeden nowy rekord.

    Wygodniejszy trigger do automatyzacji niż pilnowanie zmiany wartości
    licznika "5. Nowe dzisiaj" - tu wystarczy `to: "on"`.
    """

    _attr_has_entity_name = True
    _attr_name = "Nowy rekord dzisiaj"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_new_today"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.get("deltas", {}).get("total", 0) > 0

    @property
    def icon(self) -> str:
        return "mdi:bell-ring" if self.is_on else "mdi:bell-outline"
