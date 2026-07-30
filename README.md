# Geneteka - Monitor Nazwisk

Integracja Home Assistant monitorująca wybrane nazwiska w ogólnokrajowej
wyszukiwarce [Geneteki](https://geneteka.genealodzy.pl) (Polskie Towarzystwo
Genealogiczne). Ponieważ Geneteka nie ma żadnego mechanizmu powiadomień o
nowych indeksach, ta integracja sama sprawdza cyklicznie i pokazuje w HA,
gdzie i kiedy przybyło coś nowego.

## Instalacja

1. Skopiuj folder `custom_components/geneteka_monitor` do katalogu
   `custom_components` w konfiguracji Home Assistant (obok innych integracji
   niestandardowych).
2. Zrestartuj Home Assistant.
3. **Ustawienia → Urządzenia i usługi → Dodaj integrację → "Geneteka - Monitor Nazwisk"**.
4. Wpisz nazwisko, które chcesz monitorować.
5. Powtórz krok 3–4 dla każdego kolejnego nazwiska — **jedna instancja integracji = jedno nazwisko**.

## Encje tworzone dla każdego nazwiska

Każde dodane nazwisko tworzy osobne urządzenie `Geneteka: <Nazwisko>` z encjami:

| Encja | Co pokazuje |
|---|---|
| 1. Urodzenia | liczba aktów urodzenia |
| 2. Małżeństwa | liczba aktów małżeństwa |
| 3. Zgony | liczba aktów zgonu |
| 4. Suma | łączna liczba rekordów |
| 5. Nowe rekordy | ile przybyło od ostatniego sprawdzenia (na tym oprzyj automatyzację powiadomień) |
| 6. Najczęstszy region | region z największą liczbą rekordów (nazwa + % całości w atrybutach) |
| Region: *nazwa* | jedna encja na każdy region, w którym są jakieś wyniki (liczba + delta) |

**Ograniczenie**: lista regionów tworzy się raz, przy pierwszym dodaniu
integracji dla danego nazwiska. Jeśli później pojawi się zupełnie nowy region
(wcześniej bez żadnego trafienia), nie dostanie automatycznie własnej encji —
trzeba usunąć i dodać integrację ponownie dla tego nazwiska.

## Ustawienia

Po dodaniu integracji można zmienić częstotliwość sprawdzania przez
**Konfiguruj** przy integracji (domyślnie: co 1 godzinę).

## Znane ograniczenia

- Bardzo popularne nazwiska (np. "Nowak") mogą powodować błąd **504** po
  stronie samej Geneteki — serwer nie wyrabia z tak szerokim zapytaniem.
  To nie jest błąd integracji; można spróbować ponownie później.
- Integracja jest nieoficjalna i nie jest w żaden sposób powiązana z PTG ani
  z geneteka.genealodzy.pl.

## Automatyzacja powiadomień

Przykład w `ha_automation_example.yaml` w tym repo — trigger na sensorze
"Nowe rekordy" (`numeric_state`, `above: 0`) wysyłający powiadomienie push.

## Licencja

MIT
