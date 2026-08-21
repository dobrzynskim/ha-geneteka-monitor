"""Sensory dla Geneteka - Monitor Nazwisk."""

from __future__ import annotations

import re
import unicodedata

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

# Numerowane etykiety, żeby wymusić kolejność w domyślnej karcie urządzenia HA
# (encje są tam sortowane alfabetycznie po nazwie - cyfry sortują się przed
# literami, więc to jest najprostszy sposób na wymuszenie kolejności bez
# ingerencji we frontend).
STAT_TYPES = [
    ("births", "1. Urodzenia", "mdi:baby-face-outline"),
    ("marriages", "2. Małżeństwa", "mdi:ring"),
    ("deaths", "3. Zgony", "mdi:cross"),
    ("total", "4. Suma", "mdi:account-search"),
]


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _device_info(coordinator, entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Geneteka: {coordinator.surname}",
        model="Monitor nazwiska",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        GenetekaStatSensor(coordinator, entry, key, label, icon)
        for key, label, icon in STAT_TYPES
    ]
    entities.append(GenetekaNewRecordsSensor(coordinator, entry))
    entities.append(GenetekaTopRegionSensor(coordinator, entry))

    # Jedna encja per region, w którym w ogóle są jakieś wyniki - to jest
    # "konkretne" rozbicie, o które prosiłeś, a nie zagrzebany atrybut JSON.
    #
    # WAŻNE ograniczenie: lista regionów jest tworzona RAZ, na podstawie
    # pierwszego pobrania przy dodawaniu integracji. Jeśli kiedyś pojawi się
    # zupełnie nowy region (parafia w województwie, gdzie wcześniej nie było
    # żadnego trafienia), nie dostanie automatycznie własnej encji - trzeba
    # by usunąć i dodać integrację ponownie. Dla istniejących regionów
    # aktualizacja liczby i delty działa normalnie przy każdym odświeżeniu.
    regions = coordinator.data.get("regions", {})
    for region_name in regions:
        entities.append(GenetekaRegionSensor(coordinator, entry, region_name))

    async_add_entities(entities)


class GenetekaStatSensor(CoordinatorEntity, SensorEntity):
    """Jeden licznik (Urodzenia/Małżeństwa/Zgony/Suma) dla danego nazwiska."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, stat_key: str, label: str, icon: str):
        super().__init__(coordinator)
        self._stat_key = stat_key
        self._entry = entry
        self._attr_name = label
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{stat_key}"
        self._attr_native_unit_of_measurement = "rekordów"
        self._attr_state_class = "total"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def native_value(self):
        return self.coordinator.data.get(self._stat_key)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "nowe_dzisiaj": data.get("deltas", {}).get(self._stat_key, 0),
            "nazwisko": data.get("surname"),
            "link_do_wyszukiwania": data.get("url"),
        }


class GenetekaNewRecordsSensor(CoordinatorEntity, SensorEntity):
    """Ile przybyło rekordów od początku bieżącej doby (zeruje się o północy) -
    widoczne wprost jako stan, a nie zagrzebane w atrybutach. Na tym
    najwygodniej oprzeć automatyzację."""

    _attr_has_entity_name = True
    _attr_name = "5. Nowe dzisiaj"
    _attr_icon = "mdi:bell-alert"
    _attr_native_unit_of_measurement = "rekordów"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_new_records"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def native_value(self):
        return self.coordinator.data.get("deltas", {}).get("total", 0)

    @property
    def extra_state_attributes(self):
        deltas = self.coordinator.data.get("deltas", {})
        return {
            "nowe_urodzenia": deltas.get("births", 0),
            "nowe_malzenstwa": deltas.get("marriages", 0),
            "nowe_zgony": deltas.get("deaths", 0),
        }


class GenetekaTopRegionSensor(CoordinatorEntity, SensorEntity):
    """Region z największą liczbą rekordów - szybki podgląd, oprócz pełnego
    rozbicia dostępnego jako osobne encje per region (GenetekaRegionSensor)."""

    _attr_has_entity_name = True
    _attr_name = "6. Najczęstszy region"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_top_region"
        self._attr_device_info = _device_info(coordinator, entry)

    def _sorted_regions(self):
        regions = self.coordinator.data.get("regions", {})
        return sorted(regions.items(), key=lambda kv: kv[1]["total"], reverse=True)

    @property
    def native_value(self):
        sorted_regions = self._sorted_regions()
        if not sorted_regions:
            return "brak danych"
        return sorted_regions[0][0]

    @property
    def extra_state_attributes(self):
        sorted_regions = self._sorted_regions()
        grand_total = self.coordinator.data.get("total", 0) or 1

        if not sorted_regions:
            return {}

        top_name, top_data = sorted_regions[0]
        return {
            "rekordy": top_data["total"],
            "procent_calosci": round(top_data["total"] / grand_total * 100, 1),
            "nowe_dzisiaj": top_data.get("zmiana", 0),
        }


class GenetekaRegionSensor(CoordinatorEntity, SensorEntity):
    """Konkretne rozbicie: ile wystąpień w danym regionie, plus delta.

    Region name z Geneteki wygląda np. "Łódzkie, Bogdanów" - zostawiamy to
    jako nazwę encji, żeby było od razu jasne, o który region chodzi.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:map-marker"
    _attr_native_unit_of_measurement = "rekordów"
    _attr_state_class = "total"

    def __init__(self, coordinator, entry: ConfigEntry, region_name: str):
        super().__init__(coordinator)
        self._region_name = region_name
        self._attr_name = f"Region: {region_name}"
        self._attr_unique_id = f"{entry.entry_id}_region_{_slugify(region_name)}"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def _region_data(self) -> dict:
        return self.coordinator.data.get("regions", {}).get(self._region_name, {})

    @property
    def native_value(self):
        return self._region_data.get("total")

    @property
    def extra_state_attributes(self):
        data = self._region_data
        return {
            "urodzenia": data.get("births", 0),
            "malzenstwa": data.get("marriages", 0),
            "zgony": data.get("deaths", 0),
            "nowe_dzisiaj": data.get("zmiana", 0),
        }
