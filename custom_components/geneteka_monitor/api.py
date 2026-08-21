"""
Async klient do ogólnokrajowej wyszukiwarki Geneteki (op=se).

Ten sam, sprawdzony regex co w samodzielnym skrypcie geneteka_client.py
(port z działającego kodu TypeScript użytkownika), tylko na aiohttp
zamiast requests, bo integracje HA muszą być asynchroniczne.
"""

import asyncio
import re
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

GENETEKA_SEARCH_URL = "https://geneteka.genealodzy.pl/index.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Popularne nazwiska (np. "Nowak") zwracają dużo większą stronę HTML i strona
# Geneteki bywa dla nich zauważalnie wolniejsza - dajemy więcej czasu niż na
# zwykłe zapytanie.
REQUEST_TIMEOUT_SECONDS = 60

# Geneteka czasem odpowiada 502 albo się zawiesza pod obciążeniem (zwłaszcza
# dla popularnych nazwisk) - ponawiamy z rosnącym odstępem zamiast od razu
# poddawać się do następnego zaplanowanego odświeżenia za godzinę.
RETRY_BACKOFF_SECONDS = (2, 4, 8)

# Kolejność kolumn w tabeli wyników Geneteki: urodzenia, zgony, śluby
REGION_ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td class="gt"><a[^>]*>([^<]+)</a></td>\s*'
    r'<td class="gt" align="right"><a[^>]*>\s*(\d*)\s*</a></td>\s*'
    r'<td class="gt" align="right"><a[^>]*>\s*(\d*)\s*</a></td>\s*'
    r'<td class="gt" align="right"><a[^>]*>\s*(\d*)\s*</a></td>',
    re.IGNORECASE,
)


class GenetekaApiError(Exception):
    """Błąd komunikacji z Geneteką (połączenie, timeout, albo nieoczekiwana odpowiedź)."""


async def _fetch_html(
    session: aiohttp.ClientSession, surname: str, params: dict
) -> tuple[str, str]:
    """Jedna próba pobrania strony wyników. Rzuca GenetekaApiError przy 5xx/timeout/błędzie połączenia."""
    try:
        async with session.get(
            GENETEKA_SEARCH_URL,
            params=params,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
        ) as response:
            if response.status >= 500:
                raise GenetekaApiError(
                    f"Geneteka zwróciła błąd serwera ({response.status}) dla '{surname}' - "
                    f"to zwykle oznacza, że baza nie wyrabia z tak szerokim zapytaniem "
                    f"(bardzo popularne nazwisko). Spróbuj ponownie później."
                )
            response.raise_for_status()
            return await response.text(), str(response.url)
    except asyncio.TimeoutError as err:
        # asyncio.TimeoutError NIE dziedziczy po aiohttp.ClientError, więc musi
        # być łapany osobno - inaczej wypada jako "nieoczekiwany błąd" w UI.
        raise GenetekaApiError(
            f"Przekroczono limit czasu ({REQUEST_TIMEOUT_SECONDS}s) podczas pobierania "
            f"danych dla '{surname}' - to popularne nazwisko zwraca dużą stronę, spróbuj ponownie"
        ) from err
    except aiohttp.ClientError as err:
        raise GenetekaApiError(f"Błąd połączenia z Genetekę: {err}") from err


async def async_get_surname_stats(session: aiohttp.ClientSession, surname: str) -> dict:
    """Pobiera statystyki nazwiska z Geneteki (wszystkie województwa naraz).

    Ponawia próbę (z rosnącym odstępem, RETRY_BACKOFF_SECONDS) na 5xx/timeout/
    błąd połączenia - to najczęstsza przyczyna niepowodzeń przy popularnych
    nazwiskach, i zwykle mija po chwili.
    """
    params = {
        "search_lastname": surname,
        "search_lastname2": "",
        "from_date": "",
        "to_date": "",
        "rpp1": "",
        "bdm": "",
        "w": "",
        "op": "se",
        "lang": "pol",
        "exac": "1",
    }

    total_attempts = len(RETRY_BACKOFF_SECONDS) + 1
    for attempt in range(total_attempts):
        try:
            html, request_url = await _fetch_html(session, surname, params)
            break
        except GenetekaApiError as err:
            if attempt >= len(RETRY_BACKOFF_SECONDS):
                raise
            backoff = RETRY_BACKOFF_SECONDS[attempt]
            _LOGGER.warning(
                "Próba %d/%d dla '%s' nieudana (%s), ponawiam za %ds",
                attempt + 1,
                total_attempts,
                surname,
                err,
                backoff,
            )
            await asyncio.sleep(backoff)

    _LOGGER.debug("Odpowiedź dla '%s': %d znaków HTML", surname, len(html))

    try:
        regions = {}
        for match in REGION_ROW_RE.finditer(html):
            name = match.group(1).strip()
            births = int(match.group(2) or 0)
            deaths = int(match.group(3) or 0)
            marriages = int(match.group(4) or 0)
            regions[name] = {
                "births": births,
                "deaths": deaths,
                "marriages": marriages,
                "total": births + deaths + marriages,
            }
    except Exception as err:  # noqa: BLE001
        # Cokolwiek pójdzie nie tak przy parsowaniu (np. strona zwróciła coś
        # innego niż zwykłą tabelę wyników dla bardzo popularnego nazwiska),
        # niech to będzie czytelny GenetekaApiError, a nie surowy traceback.
        raise GenetekaApiError(f"Błąd parsowania odpowiedzi dla '{surname}': {err}") from err

    if not regions:
        _LOGGER.warning(
            "Brak dopasowań regionów dla nazwiska '%s' (HTML miał %d znaków) - "
            "sprawdź czy format strony Geneteki się nie zmienił, albo czy to "
            "nazwisko w ogóle ma wyniki",
            surname,
            len(html),
        )

    total_births = sum(r["births"] for r in regions.values())
    total_deaths = sum(r["deaths"] for r in regions.values())
    total_marriages = sum(r["marriages"] for r in regions.values())

    return {
        "surname": surname,
        "births": total_births,
        "marriages": total_marriages,
        "deaths": total_deaths,
        "total": total_births + total_marriages + total_deaths,
        "regions": regions,
        "url": request_url,
    }
