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
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

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
        now = dt_util.now()

        if "periods" in previous:
            periods_state = previous["periods"]
        elif "baseline_date" in previous:
            # Migracja ze starego, płaskiego formatu (tylko licznik dzienny)
            # - dzień przenosimy 1:1, żeby nie zerować już zebranego
            # dzisiejszego licznika. Tydzień/miesiąc to nowa funkcja, więc
            # startują od zera, tak jak przy pierwszym uruchomieniu.
            periods_state = {
                "day": {
                    "key": previous["baseline_date"],
                    "baseline": previous.get("baseline", {}),
                    "last_totals": previous.get("last_totals", {}),
                }
            }
        else:
            periods_state = {}

        baseline_regions = previous.get("baseline_regions", {})

        # Ten sam mechanizm baseline/reset co przy liczniku dziennym,
        # uogólniony na trzy długości okna: dzień/tydzień/miesiąc. Klucz
        # okresu (np. "2026-08-21", "2026-W34", "2026-08") zmienia się z
        # kalendarzem, co samo w sobie wyzwala reset - nie trzeba osobnego
        # harmonogramu.
        new_periods_state = {}
        for period, period_key in (
            ("day", now.date().isoformat()),
            ("week", f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"),
            ("month", now.strftime("%Y-%m")),
        ):
            state = periods_state.get(period, {})
            if state.get("key") != period_key:
                baseline = state.get("last_totals") or {key: stats[key] for key in stat_keys}
                if period == "day":
                    baseline_regions = previous.get("last_regions", {})
            else:
                baseline = state.get("baseline", {key: stats[key] for key in stat_keys})
            new_periods_state[period] = {
                "key": period_key,
                "baseline": baseline,
                "last_totals": {key: stats[key] for key in stat_keys},
            }

        stats["deltas"] = {
            key: max(0, stats[key] - new_periods_state["day"]["baseline"].get(key, stats[key]))
            for key in stat_keys
        }
        stats["deltas_week"] = {
            key: max(0, stats[key] - new_periods_state["week"]["baseline"].get(key, stats[key]))
            for key in stat_keys
        }
        stats["deltas_month"] = {
            key: max(0, stats[key] - new_periods_state["month"]["baseline"].get(key, stats[key]))
            for key in stat_keys
        }

        # Dorzucamy deltę per region (tylko dzienną) - żeby było widać
        # KONKRETNIE gdzie przybyło, nie tylko w sumie krajowej.
        for region_name, region_data in stats["regions"].items():
            prev_region_total = baseline_regions.get(region_name, {}).get(
                "total", region_data["total"]
            )
            region_data["zmiana"] = max(0, region_data["total"] - prev_region_total)

        self._previous = {
            "periods": new_periods_state,
            "baseline_regions": baseline_regions,
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Usuń zapisany stan licznika, żeby po usunięciu integracji nie zostawał
    osierocony plik w .storage (nic go już wtedy nie czyta ani nie kasuje)."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}").async_remove()
