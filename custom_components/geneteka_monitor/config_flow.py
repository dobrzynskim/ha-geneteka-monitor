"""Config flow dla Geneteka - Monitor Nazwisk."""

import difflib
import logging
import re
import unicodedata

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import callback

from .api import async_get_surname_stats, GenetekaApiError
from .const import (
    DOMAIN,
    CONF_SURNAME,
    CONF_UPDATE_INTERVAL_HOURS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)

# Próg podobieństwa (difflib SequenceMatcher.ratio) powyżej którego dwa
# nazwiska uznajemy za "prawdopodobnie ta sama literówka" - dobrane tak, żeby
# łapać np. "Krzczot"/"Kszczot" (przestawione litery), a nie zwykłe różne
# nazwiska o podobnej długości.
SIMILAR_SURNAME_THRESHOLD = 0.8


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _find_similar_surname(hass, surname: str) -> str | None:
    """Zwraca już skonfigurowane nazwisko, jeśli jest bardzo podobne do
    podanego (prawdopodobna literówka), albo None."""
    normalized = surname.strip().lower()
    best_match = None
    best_ratio = 0.0
    for entry in hass.config_entries.async_entries(DOMAIN):
        existing = entry.data.get(CONF_SURNAME, "")
        if not existing or existing.strip().lower() == normalized:
            continue
        ratio = difflib.SequenceMatcher(None, normalized, existing.strip().lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing
    return best_match if best_ratio >= SIMILAR_SURNAME_THRESHOLD else None


class GenetekaMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Kreator konfiguracji - jedno nazwisko na jedną instancję integracji."""

    VERSION = 1

    def __init__(self):
        self._pending_surname: str | None = None
        self._pending_similar: str | None = None

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            surname = user_input[CONF_SURNAME].strip()

            if not surname:
                errors["base"] = "empty_surname"
            else:
                await self.async_set_unique_id(_slugify(surname))
                self._abort_if_unique_id_configured()

                session = async_get_clientsession(self.hass)
                try:
                    await async_get_surname_stats(session, surname)
                except GenetekaApiError as err:
                    _LOGGER.warning("Błąd pobierania danych dla '%s': %s", surname, err)
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Nieoczekiwany błąd podczas walidacji nazwiska")
                    errors["base"] = "unknown"
                else:
                    similar = _find_similar_surname(self.hass, surname)
                    if similar:
                        # Nie blokujemy - to często celowe (np. sprawdzanie
                        # obu wariantów pisowni), tylko dajemy szansę się
                        # zorientować, że to może być literówka.
                        self._pending_surname = surname
                        self._pending_similar = similar
                        return await self.async_step_confirm_similar()
                    return self.async_create_entry(
                        title=surname,
                        data={CONF_SURNAME: surname},
                    )

        schema = vol.Schema({vol.Required(CONF_SURNAME): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_confirm_similar(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending_surname,
                data={CONF_SURNAME: self._pending_surname},
            )

        return self.async_show_form(
            step_id="confirm_similar",
            data_schema=vol.Schema({}),
            description_placeholders={
                "surname": self._pending_surname,
                "similar": self._pending_similar,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GenetekaMonitorOptionsFlow(config_entry)


class GenetekaMonitorOptionsFlow(config_entries.OptionsFlow):
    """Pozwala zmienić częstotliwość sprawdzania po dodaniu integracji."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_UPDATE_INTERVAL_HOURS, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=168)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
