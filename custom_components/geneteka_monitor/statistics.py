"""Long-term statistics dla Geneteka - Monitor Nazwisk.

Wypycha co odświeżenie jeden punkt (suma rekordów narastająco) do
wbudowanych Statystyk Home Assistanta - tym samym mechanizmem co liczniki
energii. HA sam liczy z tego przyrosty dzień/tydzień/miesiąc do wykresu
słupkowego w Historii/Statystykach, bez potrzeby budowania własnego.
"""

from __future__ import annotations

import re
import unicodedata

import homeassistant.util.dt as dt_util
from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def statistic_id_for(surname: str) -> str:
    return f"{DOMAIN}:{_slugify(surname)}_total"


def async_push_statistics(hass: HomeAssistant, surname: str, total: int) -> None:
    """Dopisz jeden punkt (suma narastająco) - HA sam policzy z tego resztę.

    Zaokrąglenie do pełnej godziny odpowiada granulacji statystyk HA
    (godzinowe kubełki); wielokrotne wywołanie w tej samej godzinie po
    prostu nadpisuje ten sam punkt, więc można wołać to przy każdym
    odświeżeniu bez obawy o duplikaty.
    """
    metadata = StatisticMetaData(
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=f"Geneteka {surname} - suma",
        source=DOMAIN,
        statistic_id=statistic_id_for(surname),
        unit_class=None,
        unit_of_measurement="rekordów",
    )
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    async_add_external_statistics(hass, metadata, [StatisticData(start=start, sum=total)])
