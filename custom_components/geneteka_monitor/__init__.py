"""Geneteka - Monitor Nazwisk - setup integracji."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import homeassistant.util.dt as dt_util
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

# Geneteka zwraca 502 dla popularnych nazwisk, gdy trafi ją kilka
# jednoczesnych zapytań naraz (np. przy starcie HA, kiedy wszystkie wpisy
# konfiguracyjne tej integracji odświeżają się równolegle) - stąd wspólna
# blokada między wszystkimi coordinatorami tej integracji, żeby zapytania do
# geneteka.genealodzy.pl szły pojedynczo, z odstępem, a nie wszystkie naraz.
REQUEST_LOCK_KEY = f"{DOMAIN}_request_lock"
REQUEST_STAGGER_SECONDS = 3


class GenetekaCoordinator(DataUpdateCoordinator):
    """Koordynator pobierający dane i liczący dzienny licznik nowych rekordów.

    Licznik "nowe dzisiaj" nie zeruje się przy każdym odświeżeniu - odnosi się
    do stanu zapamiętanego na początek bieżącej doby (czasu lokalnego HA) i
    rośnie przez cały dzień, aż zostanie zresetowany o północy.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.surname = entry.data[CONF_SURNAME]
        self.session = async_get_clientsession(hass)
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._previous: dict | None = None
        self._request_lock = hass.data.setdefault(REQUEST_LOCK_KEY, asyncio.Lock())

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

        async with self._request_lock:
            try:
                stats = await async_get_surname_stats(self.session, self.surname)
            except GenetekaApiError as err:
                raise UpdateFailed(str(err)) from err
            finally:
                # Odstęp trzymany WEWNĄTRZ blokady, żeby kolejne oczekujące
                # zapytanie też ruszyło dopiero po nim - inaczej stagger nie
                # miałby sensu.
                await asyncio.sleep(REQUEST_STAGGER_SECONDS)

        previous = self._previous or {}
        stat_keys = ("total", "births", "marriages", "deaths")

        today = dt_util.now().date().isoformat()
        if previous.get("baseline_date") != today:
            # Nowy dzień (albo pierwsze uruchomienie integracji) - licznik
            # dzienny startuje od zera. Punktem odniesienia jest ostatni
            # zapamiętany stan sprzed zmiany daty (a przy pierwszym
            # uruchomieniu - stan bieżący, żeby nie pokazać sztucznie dużej
            # liczby "nowych" rekordów).
            baseline = previous.get("last_totals") or {key: stats[key] for key in stat_keys}
            baseline_regions = previous.get("last_regions", {})
        else:
            baseline = previous.get("baseline", {key: stats[key] for key in stat_keys})
            baseline_regions = previous.get("baseline_regions", {})

        stats["deltas"] = {
            key: max(0, stats[key] - baseline.get(key, stats[key])) for key in stat_keys
        }

        # Dorzucamy deltę per region - żeby było widać KONKRETNIE gdzie przybyło,
        # nie tylko w sumie krajowej. Liczona tak samo, od początku doby.
        for region_name, region_data in stats["regions"].items():
            prev_region_total = baseline_regions.get(region_name, {}).get(
                "total", region_data["total"]
            )
            region_data["zmiana"] = max(0, region_data["total"] - prev_region_total)

        self._previous = {
            "baseline": baseline,
            "baseline_date": today,
            "baseline_regions": baseline_regions,
            "last_totals": {key: stats[key] for key in stat_keys},
            "last_regions": {
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
