# -*- coding: utf-8 -*-
"""
SILNIK wtyczki "Pobieranie Sentinel" — cała robota bez okienek:
logowanie do Copernicus, wyszukiwanie scen, pobieranie i produkty.

Celowo NIE ma tu żadnych importów z QGIS — dzięki temu tę część
można testować i używać także poza QGIS-em. Zamiast rasterio używamy
GDAL-a, a zamiast geopandas — QGIS-owej geometrii (w dialogi.py),
bo obie te rzeczy QGIS ma wbudowane i nic nie trzeba doinstalowywać.

© Grzegorz Górniak
"""

import os
import csv
import time
import shutil
import zipfile
import tempfile

import requests                      # QGIS ma requests w zestawie
from osgeo import gdal               # GDAL — serce QGIS, zawsze jest
import numpy as np                   # numpy — też zawsze jest w QGIS

gdal.UseExceptions()

# Adresy serwisów Copernicus — zostaw jak są
ADRES_LOGOWANIA = ("https://identity.dataspace.copernicus.eu/auth/realms/"
                   "CDSE/protocol/openid-connect/token")
ADRES_KATALOGU = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
ADRES_POBIERANIA = "https://download.dataspace.copernicus.eu/odata/v1/Products"

ILE_PROB = 3   # ile razy próbować ściągnąć jedną scenę, zanim się poddamy

# =============================================================================
#  SŁOWNICZEK PASM SENTINEL-2:
#    B02 = niebieskie   (10 m)     B05 = red-edge, skraj czerwieni (20 m)
#    B03 = zielone      (10 m)     B8A = wąska podczerwień         (20 m)
#    B04 = czerwone     (10 m)     B11 = krótkofal. podczerwień 1  (20 m)
#    B08 = podczerwień  (10 m)     B12 = krótkofal. podczerwień 2  (20 m)
#    SCL = gotowa mapa klas terenu od ESA (20 m, tylko w L2A)
# =============================================================================

# Definicje produktów. "pasma" = lista par (pasmo, rozdzielczość).
# PIERWSZE pasmo wyznacza siatkę pikseli — reszta jest dopasowywana.
# Wskaźniki liczone wzorem (A-B)/(A+B) z dwóch pierwszych pasm.
PRODUKTY = {
    "RGB":  {"typ": "kompozycja",
             "pasma": [("B04", "10m"), ("B03", "10m"), ("B02", "10m")],
             "opis": "RGB — kolory naturalne (jak z samolotu)"},
    "CIR":  {"typ": "kompozycja",
             "pasma": [("B08", "10m"), ("B04", "10m"), ("B03", "10m")],
             "opis": "CIR — podczerwień (roślinność na czerwono)"},
    "SWIR": {"typ": "kompozycja",
             "pasma": [("B11", "20m"), ("B08", "10m"), ("B02", "10m")],
             "opis": "SWIR — rolnicza (uprawy, wilgotność gleby)"},
    "GEO":  {"typ": "kompozycja",
             "pasma": [("B12", "20m"), ("B11", "20m"), ("B02", "10m")],
             "opis": "GEO — geologiczna (skały, wyrobiska)"},
    "NDVI": {"typ": "wskaznik",
             "pasma": [("B08", "10m"), ("B04", "10m")],
             "opis": "NDVI — kondycja roślin (klasyk)"},
    "NDWI": {"typ": "wskaznik",
             "pasma": [("B03", "10m"), ("B08", "10m")],
             "opis": "NDWI — woda (jeziora, zalewy)"},
    "NDMI": {"typ": "wskaznik",
             "pasma": [("B8A", "20m"), ("B11", "20m")],
             "opis": "NDMI — uwodnienie roślin (susze)"},
    "NBR":  {"typ": "wskaznik",
             "pasma": [("B8A", "20m"), ("B12", "20m")],
             "opis": "NBR — pogorzeliska (pożary)"},
    "NDSI": {"typ": "wskaznik",
             "pasma": [("B11", "20m"), ("B03", "10m")],
             "odwroc": True,   # NDSI = (B03-B11)/(B03+B11); B11 daje siatkę
             "opis": "NDSI — śnieg i lód"},
    "NDRE": {"typ": "wskaznik",
             "pasma": [("B8A", "20m"), ("B05", "20m")],
             "opis": "NDRE — chlorofil, wczesny stres roślin"},
    "SCL":  {"typ": "scl",
             "pasma": [("SCL", "20m")],
             "opis": "SCL — mapa klas terenu ESA (tylko L2A)"},
}

# strategie wyboru zdjęć (współdzielone przez okienka)
STRATEGIE = [
    ("Bierz wszystkie znalezione zdjęcia", "WSZYSTKIE"),
    ("Sam wybiorę z listy (ptaszki)", "RECZNIE"),
    ("Najlepsze 3 z każdego miesiąca (najmniej chmur)", "TOP3"),
]

NAZWY_MIESIECY = {"01": "styczeń", "02": "luty", "03": "marzec",
                  "04": "kwiecień", "05": "maj", "06": "czerwiec",
                  "07": "lipiec", "08": "sierpień", "09": "wrzesień",
                  "10": "październik", "11": "listopad", "12": "grudzień"}


# =============================================================================
#  LOGOWANIE I WYSZUKIWANIE
# =============================================================================

def pobierz_token(login, haslo):
    """
    Loguje się do Copernicus i odbiera token — jednorazową przepustkę
    ważną ok. 10 minut. Każde pobieranie bierze świeżą.
    """
    odpowiedz = requests.post(
        ADRES_LOGOWANIA,
        data={"client_id": "cdse-public", "grant_type": "password",
              "username": login, "password": haslo},
        timeout=30)
    if odpowiedz.status_code != 200:
        raise RuntimeError("Logowanie nie wyszło — sprawdź login i hasło. "
                           f"Serwer: {odpowiedz.text[:200]}")
    return odpowiedz.json()["access_token"]


def wyszukaj_sceny(satelita, obszar_wkt, data_od, data_do, chmury,
                   log=print):
    """
    Pyta katalog Copernicus o zdjęcia pasujące do warunków.
    Sentinel-2: poziom (L2A/L1C) + filtr chmur.
    Sentinel-1: produkty GRD, bez chmur (radar ich nie widzi).
    Wyniki przychodzą porcjami po 100 — kręcimy się, aż zbierzemy całość.
    """
    if satelita.startswith("S2"):
        poziom = "L2A" if satelita == "S2-L2A" else "L1C"
        filtr = (
            f"Collection/Name eq 'SENTINEL-2' "
            f"and contains(Name,'{poziom}') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;"
            f"{obszar_wkt}') "
            f"and ContentDate/Start ge {data_od}T00:00:00.000Z "
            f"and ContentDate/Start le {data_do}T23:59:59.999Z "
            f"and Attributes/OData.CSC.DoubleAttribute/any("
            f"att:att/Name eq 'cloudCover' and "
            f"att/OData.CSC.DoubleAttribute/Value le {chmury})")
    else:
        filtr = (
            f"Collection/Name eq 'SENTINEL-1' "
            f"and contains(Name,'GRD') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;"
            f"{obszar_wkt}') "
            f"and ContentDate/Start ge {data_od}T00:00:00.000Z "
            f"and ContentDate/Start le {data_do}T23:59:59.999Z")

    adres = (f"{ADRES_KATALOGU}?$filter={filtr}"
             f"&$orderby=ContentDate/Start asc&$top=100&$expand=Attributes")

    sceny, porcja = [], 0
    while adres:
        porcja += 1
        log(f"Pytam serwis o porcję nr {porcja} (chwila cierpliwości)...")
        odpowiedz = requests.get(adres, timeout=60)
        if odpowiedz.status_code != 200:
            raise RuntimeError(f"Wyszukiwanie nie wyszło: "
                               f"{odpowiedz.text[:200]}")
        wynik = odpowiedz.json()
        nowe = wynik.get("value", [])
        sceny.extend(nowe)
        log(f"Porcja nr {porcja}: {len(nowe)} zdjęć (razem {len(sceny)}).")
        adres = wynik.get("@odata.nextLink")
    return sceny


def odczytaj_zachmurzenie(scena):
    """Wyłuskuje z opisu zdjęcia, ile procent zajmują chmury."""
    for atrybut in scena.get("Attributes", []):
        if atrybut.get("Name") == "cloudCover":
            return atrybut.get("Value")
    return None


# =============================================================================
#  POBIERANIE JEDNEJ SCENY
# =============================================================================

def pobierz_scene(scena, numer, ile_wszystkich, folder, login, haslo,
                  log=print):
    """
    Ściąga jedno zdjęcie jako .zip. Odpalane równolegle przez kilku
    "pracowników" — stąd numer w meldunkach. Pomija już pobrane pliki,
    ponawia po błędzie (do 3 prób), a w trakcie ściągania trzyma plik
    pod nazwą .part, żeby przerwane pobieranie nie udawało gotowego.
    Zwraca (ścieżka_zipa_lub_None, opis_statusu, ile_sekund).
    """
    kto = f"[{numer}/{ile_wszystkich}]"
    nazwa_pliku = scena["Name"].replace(".SAFE", "") + ".zip"
    sciezka_pliku = os.path.join(folder, nazwa_pliku)
    sciezka_tymczasowa = sciezka_pliku + ".part"
    start = time.time()

    if os.path.exists(sciezka_pliku):
        log(f"{kto} POMIJAM — już jest na dysku: {nazwa_pliku}")
        return sciezka_pliku, "pominięto (już było)", 0

    for proba in range(1, ILE_PROB + 1):
        try:
            if proba > 1:
                log(f"{kto} Próba {proba}/{ILE_PROB}...")
            token = pobierz_token(login, haslo)
            sesja = requests.Session()
            sesja.headers.update({"Authorization": f"Bearer {token}"})

            adres = f"{ADRES_POBIERANIA}({scena['Id']})/$value"
            odpowiedz = sesja.get(adres, allow_redirects=False,
                                  stream=True, timeout=60)
            while odpowiedz.status_code in (301, 302, 303, 307):
                adres = odpowiedz.headers["Location"]
                odpowiedz = sesja.get(adres, allow_redirects=False,
                                      stream=True, timeout=60)
            if odpowiedz.status_code != 200:
                raise RuntimeError(f"kod {odpowiedz.status_code}: "
                                   f"{odpowiedz.text[:120]}")

            rozmiar = int(odpowiedz.headers.get("Content-Length", 0))
            log(f"{kto} Ściągam: {nazwa_pliku} ({rozmiar/1024**3:.2f} GB)")

            pobrano, ostatni_meldunek = 0, 0
            with open(sciezka_tymczasowa, "wb") as plik:
                for fragment in odpowiedz.iter_content(
                        chunk_size=8 * 1024 * 1024):
                    plik.write(fragment)
                    pobrano += len(fragment)
                    if pobrano - ostatni_meldunek >= 200 * 1024 * 1024:
                        ostatni_meldunek = pobrano
                        procent = 100 * pobrano / rozmiar if rozmiar else 0
                        log(f"{kto}   ...{pobrano/1024**2:.0f} MB "
                            f"({procent:.0f}%)")

            os.rename(sciezka_tymczasowa, sciezka_pliku)
            ile_trwalo = time.time() - start
            log(f"{kto} Mam! {nazwa_pliku} — {ile_trwalo/60:.1f} min")
            return sciezka_pliku, "OK", ile_trwalo

        except Exception as blad:
            log(f"{kto} OJ: próba {proba} nie wyszła ({blad}).")
            if os.path.exists(sciezka_tymczasowa):
                os.remove(sciezka_tymczasowa)
            if proba < ILE_PROB:
                time.sleep(15)

    return None, f"błąd po {ILE_PROB} próbach", time.time() - start


# =============================================================================
#  PRODUKTY — kompozycje, wskaźniki i SCL (na GDAL-u i numpy)
# =============================================================================

def _rozciagnij_do_8bit(pasmo):
    """
    Surowe wartości satelity (tysiące) -> zakres 0-255 jak w obrazku.
    Odcinamy 2% skrajnych pikseli i rozciągamy resztę — obraz robi się
    kontrastowy zamiast ciemnej plamy.
    """
    dodatnie = pasmo[pasmo > 0]
    if dodatnie.size == 0:
        return np.zeros(pasmo.shape, dtype="uint8")
    dol, gora = np.percentile(dodatnie, (2, 98))
    if gora <= dol:
        gora = dol + 1
    znorm = np.clip((pasmo.astype("float32") - dol) / (gora - dol), 0, 1)
    wynik = (znorm * 254 + 1).astype("uint8")  # dane: 1-255
    wynik[pasmo == 0] = 0                      # brak danych: 0 (nodata)
    return wynik


# =============================================================================
#  GOTOWA SYMBOLIZACJA — palety kolorów dla wskaźników i mapy SCL.
#  Obok każdego pliku .tif zapisujemy plik stylu .qml o tej samej nazwie:
#  QGIS wczytuje go AUTOMATYCZNIE, więc laik od razu widzi kolorową,
#  opisaną mapę zamiast szarej płachty.
# =============================================================================

PALETY = {
    "NDVI": [(-1.0, "#d7191c", "woda / brak roślin"),
             (0.0, "#fdae61", "gleba, zabudowa"),
             (0.2, "#ffffbf", "słaba roślinność"),
             (0.5, "#a6d96a", "dobra roślinność"),
             (1.0, "#1a9641", "bujna roślinność")],
    "NDRE": [(-1.0, "#d7191c", "brak roślin"),
             (0.0, "#fdae61", "silny stres"),
             (0.2, "#ffffbf", "umiarkowany stres"),
             (0.5, "#a6d96a", "dobra kondycja"),
             (1.0, "#1a9641", "wysoki chlorofil")],
    "NDWI": [(-1.0, "#8c510a", "suchy ląd"),
             (0.0, "#f6e8c3", "ląd"),
             (0.2, "#80cdc1", "wilgotne / płycizny"),
             (1.0, "#01665e", "woda")],
    "NDMI": [(-1.0, "#d73027", "silna susza"),
             (0.0, "#fee090", "sucho"),
             (0.4, "#91bfdb", "wilgotno"),
             (1.0, "#4575b4", "bardzo wilgotno")],
    "NBR":  [(-1.0, "#67001f", "świeże pogorzelisko"),
             (0.0, "#f7f7f7", "teren neutralny"),
             (0.4, "#a6dba0", "roślinność"),
             (1.0, "#1b7837", "bujna roślinność")],
    "NDSI": [(-1.0, "#a6611a", "brak śniegu"),
             (0.0, "#f5f5f5", "przejściowe"),
             (0.4, "#9ecae1", "śnieg"),
             (1.0, "#2171b5", "śnieg / lód")],
}

SCL_KLASY = [(1, "#ff0004", "piksel wadliwy"),
             (2, "#868686", "ciemne / cienie"),
             (3, "#774b0a", "cień chmury"),
             (4, "#10d22c", "roślinność"),
             (5, "#ffff52", "goła gleba"),
             (6, "#0000ff", "woda"),
             (7, "#818181", "niepewne"),
             (8, "#c0c0c0", "chmura (średnie prawdop.)"),
             (9, "#f1f1f1", "chmura gęsta"),
             (10, "#bac5eb", "chmura wysoka (cirrus)"),
             (11, "#52fff9", "śnieg / lód")]


def zapisz_qml(sciezka_tif, produkt):
    """Zapisuje obok .tif plik stylu .qml z paletą kolorów i legendą —
    QGIS stosuje go automatycznie przy wczytaniu warstwy."""
    if produkt in PALETY:
        pozycje = "\n".join(
            f'            <item alpha="255" value="{wartosc}" '
            f'label="{wartosc:+.1f}  {opis}" color="{kolor}"/>'
            for wartosc, kolor, opis in PALETY[produkt])
        xml = (
            '<!DOCTYPE qgis>\n<qgis version="3.28.0">\n  <pipe>\n'
            '    <rasterrenderer type="singlebandpseudocolor" band="1" '
            'opacity="1" classificationMin="-1" classificationMax="1">\n'
            '      <rastershader>\n'
            '        <colorrampshader colorRampType="INTERPOLATED" '
            'classificationMode="1" clip="0">\n'
            f'{pozycje}\n'
            '        </colorrampshader>\n      </rastershader>\n'
            '    </rasterrenderer>\n  </pipe>\n</qgis>\n')
    elif produkt == "SCL":
        pozycje = "\n".join(
            f'          <paletteEntry value="{wartosc}" color="{kolor}" '
            f'label="{wartosc}: {opis}" alpha="255"/>'
            for wartosc, kolor, opis in SCL_KLASY)
        xml = (
            '<!DOCTYPE qgis>\n<qgis version="3.28.0">\n  <pipe>\n'
            '    <rasterrenderer type="paletted" band="1" opacity="1">\n'
            '      <colorPalette>\n'
            f'{pozycje}\n'
            '      </colorPalette>\n    </rasterrenderer>\n'
            '  </pipe>\n</qgis>\n')
    else:
        return  # kompozycje mają kolory w samym pliku
    with open(sciezka_tif.replace(".tif", ".qml"), "w",
              encoding="utf-8") as plik:
        plik.write(xml)


def _znajdz_pasma_w_zipie(zip_, potrzebne):
    """
    Szuka plików pasm w zipie. L2A: "..._B11_20m.jp2",
    L1C: "..._B11.jp2" — sprawdzamy obie konwencje.
    """
    znalezione = {para: None for para in potrzebne}
    for nazwa_w_zipie in zip_.namelist():
        for (pasmo, rozdz) in znalezione:
            if (nazwa_w_zipie.endswith(f"_{pasmo}_{rozdz}.jp2")
                    or nazwa_w_zipie.endswith(f"_{pasmo}.jp2")):
                znalezione[(pasmo, rozdz)] = nazwa_w_zipie
    return znalezione


def _wczytaj(sciezka, ksztalt=None):
    """
    Czyta pasmo przez GDAL. Jak podasz ksztalt (wysokość, szerokość),
    pasmo jest w locie przeliczane do tej siatki — tak godzimy pasma
    10 m z 20 m. Zwraca (macierz, zbiór_gdal_do_georeferencji).
    """
    zbior = gdal.Open(sciezka)
    pas = zbior.GetRasterBand(1)
    if ksztalt and (zbior.RasterYSize, zbior.RasterXSize) != ksztalt:
        macierz = pas.ReadAsArray(buf_xsize=ksztalt[1],
                                  buf_ysize=ksztalt[0])
    else:
        macierz = pas.ReadAsArray()
    return macierz, zbior


def _zapisz(cel, warstwy, wzorzec, typ_gdal, nodata=None):
    """Zapisuje gotowy produkt jako GeoTIFF (układ współrzędnych
    i położenie przepisujemy ze wzorcowego pasma)."""
    sterownik = gdal.GetDriverByName("GTiff")
    wyjscie = sterownik.Create(
        cel, wzorzec.RasterXSize, wzorzec.RasterYSize, len(warstwy),
        typ_gdal, options=["COMPRESS=DEFLATE", "TILED=YES"])
    wyjscie.SetGeoTransform(wzorzec.GetGeoTransform())
    wyjscie.SetProjection(wzorzec.GetProjection())
    for numer, warstwa in enumerate(warstwy, start=1):
        pas = wyjscie.GetRasterBand(numer)
        pas.WriteArray(warstwa)
        if nodata is not None:
            pas.SetNoDataValue(nodata)
    wyjscie.FlushCache()
    wyjscie = None  # zamknięcie pliku (tak się to robi w GDAL-u)


def zrob_produkty(sciezka_zip, folder, produkty, log=print, kto=""):
    """
    Wyciąga z zipa tylko potrzebne pasma i robi wybrane produkty.
    Produkty już istniejące pomija (wznawialność).
    Zwraca listę ścieżek do utworzonych plików .tif.
    """
    utworzone = []
    wybrane = [nazwa for nazwa, tak in produkty.items() if tak]
    if not wybrane:
        return utworzone
    nazwa = os.path.basename(sciezka_zip).replace(".zip", "")

    do_zrobienia = [
        produkt for produkt in wybrane
        if not os.path.exists(os.path.join(folder,
                                           f"{nazwa}_{produkt}.tif"))]
    if not do_zrobienia:
        log(f"{kto} Produkty dla {nazwa[:30]} już są — pomijam.")
        return utworzone

    log(f"{kto} Robię produkty ({', '.join(do_zrobienia)})...")

    # jakich pasm potrzebujemy (bez powtórek)
    potrzebne = []
    for produkt in do_zrobienia:
        for para in PRODUKTY[produkt]["pasma"]:
            if para not in potrzebne:
                potrzebne.append(para)

    with zipfile.ZipFile(sciezka_zip) as zip_:
        znalezione = _znajdz_pasma_w_zipie(zip_, potrzebne)
        folder_tymczasowy = tempfile.mkdtemp()
        wypakowane = {}
        for para, gdzie in znalezione.items():
            if gdzie:
                wypakowane[para] = zip_.extract(gdzie, folder_tymczasowy)

    try:
        for produkt in do_zrobienia:
            przepis = PRODUKTY[produkt]
            pasma = przepis["pasma"]
            if any(pasmo not in wypakowane for pasmo in pasma):
                log(f"{kto}   Brak pasm dla {produkt} — pomijam "
                    f"(np. SCL jest tylko w L2A).")
                continue
            cel = os.path.join(folder, f"{nazwa}_{produkt}.tif")
            try:
                if przepis["typ"] == "kompozycja":
                    pierwsza, wzorzec = _wczytaj(wypakowane[pasma[0]])
                    ksztalt = pierwsza.shape
                    warstwy = [_rozciagnij_do_8bit(pierwsza)]
                    for para in pasma[1:]:
                        macierz, _ = _wczytaj(wypakowane[para], ksztalt)
                        warstwy.append(_rozciagnij_do_8bit(macierz))
                    # nodata=0 -> obrzeża sceny przezroczyste w QGIS
                    _zapisz(cel, warstwy, wzorzec, gdal.GDT_Byte,
                            nodata=0)

                elif przepis["typ"] == "wskaznik":
                    a, wzorzec = _wczytaj(wypakowane[pasma[0]])
                    b, _ = _wczytaj(wypakowane[pasma[1]], a.shape)
                    a = a.astype("float32")
                    b = b.astype("float32")
                    if przepis.get("odwroc"):     # patrz opis NDSI wyżej
                        a, b = b, a
                    suma = a + b
                    wskaznik = np.where(
                        suma > 0,
                        (a - b) / np.where(suma > 0, suma, 1),
                        -9999).astype("float32")
                    _zapisz(cel, [wskaznik], wzorzec, gdal.GDT_Float32,
                            nodata=-9999)
                    zapisz_qml(cel, produkt)  # paleta kolorów

                elif przepis["typ"] == "scl":
                    # gotowa mapa klas ESA; wartości pikseli:
                    # 0 brak danych, 3 cień chmury, 4 roślinność,
                    # 5 gleba, 6 woda, 8-10 chmury, 11 śnieg/lód
                    macierz, wzorzec = _wczytaj(wypakowane[pasma[0]])
                    _zapisz(cel, [macierz.astype("uint8")], wzorzec,
                            gdal.GDT_Byte, nodata=0)
                    zapisz_qml(cel, produkt)  # legenda klas SCL

                utworzone.append(cel)
                log(f"{kto}   gotowe: {os.path.basename(cel)}")
            except Exception as blad:
                log(f"{kto}   OJ: {produkt} nie wyszedł ({blad}).")
    finally:
        shutil.rmtree(folder_tymczasowy, ignore_errors=True)
    return utworzone


# =============================================================================
#  RAPORT
# =============================================================================

def zapisz_raport(folder, raport):
    """Zapisuje raport CSV (średniki i kodowanie, które lubi Excel)."""
    sciezka = os.path.join(folder,
                           f"raport_{time.strftime('%Y%m%d_%H%M')}.csv")
    with open(sciezka, "w", newline="", encoding="utf-8-sig") as plik:
        piszacy = csv.DictWriter(plik, fieldnames=raport[0].keys(),
                                 delimiter=";")
        piszacy.writeheader()
        piszacy.writerows(raport)
    return sciezka
