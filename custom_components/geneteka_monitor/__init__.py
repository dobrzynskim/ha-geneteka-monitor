"""Geneteka - Monitor Nazwisk - setup integracji."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import async_get_surname_stats, GenetekaApiError
from .const import (
    DOMAIN,
    CONF_SURNAME,
    CONF_UPDATE_INTERVAL_HOURS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

STORAGE_VERSION = 1


class GenetekaCoordinator(DataUpdateCoordinator):
    """Koordynator pobierający dane i liczący zmianę względem poprzedniego sprawdzenia."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.surname = entry.data[CONF_SURNAME]
        self.session = async_get_clientsession(hass)
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._previous: dict | None = None

        interval_hours = entry.options.get(
            CONF_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.surname}",
            update_interval=timedelta(hours=interval_hours),
        )

    async def _async_load_previous(self):
        if self._previous is None:
            self._previous = await self._store.async_load() or {}

    async def _async_update_data(self) -> dict:
        await self._async_load_previous()

        try:
            stats = await async_get_surname_stats(self.session, self.surname)
        except GenetekaApiError as err:
            raise UpdateFailed(str(err)) from err

        previous = self._previous or {}
        previous_regions = previous.get("regions", {})

        stats["deltas"] = {
            "total": max(0, stats["total"] - previous.get("total", stats["total"])),
            "births": max(0, stats["births"] - previous.get("births", stats["births"])),
            "marriages": max(0, stats["marriages"] - previous.get("marriages", stats["marriages"])),
            "deaths": max(0, stats["deaths"] - previous.get("deaths", stats["deaths"])),
        }

        # Dorzucamy deltę per region - żeby było widać KONKRETNIE gdzie przybyło,
        # nie tylko w sumie krajowej.
        for region_name, region_data in stats["regions"].items():
            prev_region_total = previous_regions.get(region_name, {}).get(
                "total", region_data["total"]
            )
            region_data["zmiana"] = max(0, region_data["total"] - prev_region_total)

        # Zapisz bieżący wynik (łącznie z regionami) jako punkt odniesienia
        self._previous = {
            "total": stats["total"],
            "births": stats["births"],
            "marriages": stats["marriages"],
            "deaths": stats["deaths"],
            "regions": {
                name: {"total": r["total"]} for name, r in stats["regions"].items()
            },
        }
        await self._store.async_save(self._previous)

        return stats


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = GenetekaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Restart integracji po zmianie opcji (np. interwału odświeżania)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
