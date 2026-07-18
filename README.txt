POBIERANIE SENTINEL — wtyczka QGIS
© Grzegorz Górniak (gorniakgrzegorz@gmail.com)

CO TO JEST
Wtyczka do masowego pobierania zdjęć satelitarnych z oficjalnego
serwisu Copernicus Data Space Ecosystem:
 - Sentinel-2 L2A (zdjęcia optyczne z korekcją atmosferyczną)
 - Sentinel-2 L1C (zdjęcia optyczne surowe)
 - Sentinel-1 GRD (obrazy radarowe — widzą przez chmury i w nocy)
Z każdej sceny Sentinel-2 potrafi od razu zrobić produkty GeoTIFF:
 - kompozycje barwne: RGB, CIR, SWIR (rolnicza), GEO (geologiczna)
 - wskaźniki: NDVI, NDWI, NDMI, NBR, NDSI, NDRE
 - mapę klas terenu SCL (ESA) — do maskowania chmur

INSTALACJA
1. QGIS -> menu Wtyczki -> Zarządzanie wtyczkami...
2. Zakładka "Zainstaluj z pliku ZIP"
3. Wskaż plik pobieranie_sentinel.zip -> Zainstaluj
4. Ikona satelity pojawi się na pasku narzędzi

WYMAGANIA
- QGIS 3.16 lub nowszy (nic nie trzeba doinstalowywać — wtyczka
  korzysta z GDAL i numpy wbudowanych w QGIS)
- darmowe konto na https://dataspace.copernicus.eu

JAK UŻYWAĆ
1. Kliknij ikonę satelity
2. Wybierz satelitę, obszar (warstwa z projektu albo plik .gpkg),
   daty, zachmurzenie, login/hasło Copernicus i folder docelowy
3. Zaznacz produkty i kliknij SZUKAJ I POBIERZ
4. W okienku wyboru odhacz zdjęcia (albo daj "najlepsze 3 z miesiąca"),
   potwierdź — i patrz jak leci pasek postępu
5. Gotowe produkty mogą wskoczyć do projektu automatycznie

Przerwane pobieranie? Uruchom ponownie — wtyczka pominie to, co już
jest na dysku, i dokończy resztę. W folderze docelowym znajdziesz też
raport CSV z podsumowaniem.
