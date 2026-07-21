# -*- coding: utf-8 -*-
"""
=============================================================================
 POBIERACZKA ZDJĘĆ SENTINEL — wersja masowa, jedno okienko
 (z oficjalnego serwisu Copernicus Data Space)
 © Grzegorz Górniak
=============================================================================

 O co chodzi? Skrypt znajduje i MASOWO ściąga zdjęcia satelitarne dla
 obszaru z Twojego pliku .gpkg, a z każdego zdjęcia od razu robi wybrane
 przez Ciebie produkty. Wszystko ustawiasz w okienkach — zero kodowania.

 Co potrafi:
   - Sentinel-2 (zdjęcia optyczne) i Sentinel-1 (radar — widzi przez
     chmury i w nocy, ale wygląda jak szara mapa, nie jak zdjęcie)
   - ściąga KILKA ZDJĘĆ NARAZ (maks. 4 — tyle pozwala serwis)
   - po wyszukaniu pokazuje LISTĘ Z PTASZKAMI: bierzesz wszystko,
     wybierasz ręcznie, filtrujesz miesiące albo każesz wybrać po
     3 najmniej zachmurzone zdjęcia z każdego miesiąca
   - błędy? sam ponawia; przerwałeś? odpal ponownie — dokończy
   - na koniec raport CSV: co się udało, co nie i ile trwało
   - na starcie sam sprawdza, czy masz potrzebne biblioteki — jak nie,
     pokazuje okienko z gotową komendą do skopiowania

 PRODUKTY z każdego zdjęcia Sentinel-2 (wybierasz ptaszkami):
   Kompozycje barwne: RGB (naturalne), CIR (roślinność na czerwono),
     SWIR (rolnicza), GEO (geologiczna)
   Wskaźniki (-1 do 1): NDVI (kondycja roślin), NDWI (woda),
     NDMI (susza), NBR (pogorzeliska), NDSI (śnieg), NDRE (chlorofil)
   Ekstra: SCL — gotowa mapa klas terenu ESA (tylko L2A) — super do
     odsiewania chmur w analizach

 Zanim odpalisz pierwszy raz:
   - załóż darmowe konto na https://dataspace.copernicus.eu
   - biblioteki zainstalujesz, jak skrypt Cię o nie poprosi :)

 Odpalanie:  python pobierz_sentinel2.py
=============================================================================
"""

import os          # do grzebania w plikach i folderach
import sys         # do awaryjnego zatrzymania skryptu
import json        # do zapamiętywania loginu i hasła w małym pliku
import base64      # do zasłonięcia hasła w tym pliku (to NIE szyfrowanie)
import csv         # do zapisania raportu z pobierania
import time        # do mierzenia, ile co trwało
import shutil      # do sprzątania folderów tymczasowych
import zipfile     # do wyciągania plików z pobranych zipów
import tempfile    # do tworzenia folderu tymczasowego na pasma
import webbrowser  # do otwierania maila po kliknięciu w podpis autora

# do ściągania kilku plików naraz (wątki = kilku "pracowników" naraz)
from concurrent.futures import ThreadPoolExecutor, as_completed

# tkinter = wbudowana w Pythona biblioteka do okienek (nic nie instalujesz)
import tkinter as tk
from tkinter import filedialog, messagebox

# --- biblioteki do doinstalowania — sprawdzamy ostrożnie, po jednej, ------
# --- żeby brak którejś nie wywalił skryptu, tylko włączył ładny komunikat -
try:
    import requests             # do rozmawiania z internetem
    JEST_REQUESTS = True
except ImportError:
    JEST_REQUESTS = False
try:
    import geopandas as gpd     # do otwierania plików z mapami (.gpkg)
    JEST_GEOPANDAS = True
except ImportError:
    JEST_GEOPANDAS = False
try:
    import rasterio             # do czytania pasm satelitarnych (.jp2)
    from rasterio.enums import Resampling  # dopasowywanie rozdzielczości
    import numpy as np          # do liczenia na obrazach
    JEST_RASTERIO = True
except ImportError:
    JEST_RASTERIO = False       # bez tego nie będzie produktów,
                                # ale samo ściąganie zadziała


# =============================================================================
#  USTAWIENIA DOMYŚLNE — to, co pojawi się w okienku na start.
# =============================================================================
AUTOR = "© Grzegorz Górniak"                # podpis w okienkach
EMAIL_AUTORA = "gorniakgrzegorz@gmail.com"  # klik w podpis = mail do autora
KOLOR = "#00bde7"        # kolor wiodący (przyciski, nagłówki sekcji)
KOLOR_HOVER = "#009ec2"  # ciemniejszy odcień po najechaniu na przycisk

# plik z zapamiętanym loginem/hasłem — leży w Twoim folderze domowym
PLIK_PAMIECI = os.path.join(os.path.expanduser("~"),
                            ".pobieraczka_sentinel.json")


def wczytaj_pamiec():
    """Odczytuje zapamiętany login i hasło (jeśli kiedyś je zapisano)."""
    try:
        with open(PLIK_PAMIECI, encoding="utf-8") as plik:
            dane = json.load(plik)
        haslo = base64.b64decode(dane.get("haslo", "")).decode("utf-8")
        return dane.get("login", ""), haslo
    except Exception:
        return "", ""


def zapisz_pamiec(login, haslo):
    """
    Zapisuje login i hasło do pliku w folderze domowym. Hasło jest
    kodowane base64 — to tylko zasłona przed przypadkowym podejrzeniem,
    NIE szyfrowanie. Nie zaznaczaj "zapamiętaj" na wspólnym komputerze!
    """
    try:
        with open(PLIK_PAMIECI, "w", encoding="utf-8") as plik:
            json.dump({"login": login,
                       "haslo": base64.b64encode(
                           haslo.encode("utf-8")).decode("ascii")}, plik)
    except Exception:
        pass  # brak zapisu to nie powód, żeby przerywać pobieranie


def wyczysc_pamiec():
    """Usuwa plik z zapamiętanymi danymi (gdy odznaczysz ptaszek)."""
    try:
        os.remove(PLIK_PAMIECI)
    except Exception:
        pass
DOMYSLNY_LOGIN = "twoj_email@przyklad.pl"   # e-mail z konta Copernicus
DOMYSLNA_DATA_OD = "2024-01-01"
DOMYSLNA_DATA_DO = "2024-12-31"
DOMYSLNE_ZACHMURZENIE = 20    # maks. % chmur (dotyczy tylko Sentinel-2)
DOMYSLNY_LIMIT_SCEN = 200     # hamulec bezpieczeństwa (1 scena = 0,5-1 GB!)
DOMYSLNIE_ILE_NARAZ = 3       # ile pobierań równocześnie (serwis: maks. 4)
# =============================================================================

KOMENDA_INSTALACJI = "pip install geopandas requests rasterio numpy"

# Adresy serwisów Copernicus — zostaw jak są
ADRES_LOGOWANIA = ("https://identity.dataspace.copernicus.eu/auth/realms/"
                   "CDSE/protocol/openid-connect/token")
ADRES_KATALOGU = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
ADRES_POBIERANIA = "https://download.dataspace.copernicus.eu/odata/v1/Products"

ILE_PROB = 3  # ile razy próbować ściągnąć jedną scenę, zanim się poddamy

NAZWY_MIESIECY = {"01": "styczeń", "02": "luty", "03": "marzec",
                  "04": "kwiecień", "05": "maj", "06": "czerwiec",
                  "07": "lipiec", "08": "sierpień", "09": "wrzesień",
                  "10": "październik", "11": "listopad", "12": "grudzień"}


# =============================================================================
#  SŁOWNICZEK PASM SENTINEL-2 (co "widzi" satelita):
#    B02 = niebieskie   (10 m)     B05 = red-edge, skraj czerwieni (20 m)
#    B03 = zielone      (10 m)     B8A = wąska podczerwień         (20 m)
#    B04 = czerwone     (10 m)     B11 = krótkofal. podczerwień 1  (20 m)
#    B08 = podczerwień  (10 m)     B12 = krótkofal. podczerwień 2  (20 m)
#    SCL = gotowa mapa klas terenu od ESA (20 m, tylko w L2A)
#  Gdy produkt miesza pasma o różnej rozdzielczości, skrypt sam
#  dopasowuje je do wspólnej siatki.
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
             "odwroc": True,  # NDSI = (B03-B11)/(B03+B11); B11 daje siatkę
             "opis": "NDSI — śnieg i lód"},
    "NDRE": {"typ": "wskaznik",
             "pasma": [("B8A", "20m"), ("B05", "20m")],
             "opis": "NDRE — chlorofil, wczesny stres roślin"},
    "SCL":  {"typ": "scl",
             "pasma": [("SCL", "20m")],
             "opis": "SCL — mapa klas terenu ESA (tylko L2A)"},
}

DOMYSLNE_PRODUKTY = {"RGB": True, "NDVI": True}

# strategie wyboru zdjęć (używane w obu okienkach)
STRATEGIE = [
    ("Bierz wszystkie znalezione zdjęcia", "WSZYSTKIE"),
    ("Sam wybiorę z listy (ptaszki przy każdym zdjęciu)", "RECZNIE"),
    ("Najlepsze 3 z każdego miesiąca (najmniej chmur)", "TOP3"),
]


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
    """
    Zapisuje obok .tif plik stylu .qml z gotową paletą kolorów i legendą.
    QGIS przy wczytywaniu warstwy sam znajduje plik .qml o tej samej
    nazwie i stosuje styl — zero klikania po właściwościach warstwy.
    """
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
        return  # kompozycje mają kolory w samym pliku — QML zbędny
    with open(sciezka_tif.replace(".tif", ".qml"), "w",
              encoding="utf-8") as plik:
        plik.write(xml)


def gadaj(tekst):
    """
    Wypisuje komunikat z godziną z przodu, np. "[14:03:21] Szukam zdjęć...".
    Dzięki temu zawsze widać, że skrypt żyje i kiedy co zrobił.
    flush=True zmusza konsolę do pokazania tekstu OD RAZU.
    """
    print(f"[{time.strftime('%H:%M:%S')}] {tekst}", flush=True)


def stopka(okno):
    """
    Dokleja na dole okienka mały podpis autora, wyrównany do prawej.
    Kliknięcie w podpis otwiera program pocztowy z mailem do autora
    (bez podkreślenia, czarna czcionka — dyskretnie, ale klikalnie).
    """
    napis = tk.Label(okno, text=AUTOR, font=("Segoe UI", 6),
                     fg="black", cursor="hand2")
    napis.pack(side="bottom", anchor="e", padx=10, pady=(0, 3))
    napis.bind("<Button-1>",
               lambda _: webbrowser.open(f"mailto:{EMAIL_AUTORA}"))


# =============================================================================
#  SPRAWDZANIE BIBLIOTEK — miły komunikat zamiast brzydkiego błędu
# =============================================================================

def sprawdz_biblioteki():
    """
    Sprawdza, czy są zainstalowane potrzebne biblioteki. Jak czegoś
    brakuje — pokazuje okienko z instrukcją i komendą GOTOWĄ DO
    SKOPIOWANIA (przycisk "Kopiuj"), zamiast sypać niezrozumiałym
    błędem na pół ekranu.

    Bez geopandas/requests skrypt nie ma jak działać — kończymy.
    Bez rasterio/numpy da się ściągać, tylko nie będzie produktów —
    dajemy wybór: doinstaluj albo jedź dalej bez produktów.
    """
    brakujace_krytyczne = []
    if not JEST_GEOPANDAS:
        brakujace_krytyczne.append("geopandas")
    if not JEST_REQUESTS:
        brakujace_krytyczne.append("requests")
    brakujace_opcjonalne = [] if JEST_RASTERIO else ["rasterio", "numpy"]

    if not brakujace_krytyczne and not brakujace_opcjonalne:
        return  # wszystko jest — lecimy dalej

    krytyczne = bool(brakujace_krytyczne)
    wszystkie_brakujace = brakujace_krytyczne + brakujace_opcjonalne

    okno = tk.Tk()
    okno.title("Brakuje bibliotek")
    okno.attributes("-topmost", True)

    tk.Label(okno, text="Zanim ruszymy — doinstaluj brakujące dodatki",
             font=("Segoe UI", 11, "bold"), padx=20,
             pady=8).pack(anchor="w")
    tk.Label(okno,
             text=(f"Brakuje: {', '.join(wszystkie_brakujace)}\n\n"
                   "Jak to naprawić (2 minuty):\n"
                   "1. Wciśnij klawisz Windows, wpisz cmd i wciśnij Enter\n"
                   "2. Wklej do czarnego okienka poniższą komendę "
                   "(przycisk Kopiuj)\n"
                   "3. Wciśnij Enter i poczekaj, aż skończy\n"
                   "4. Uruchom ten skrypt jeszcze raz"),
             font=("Segoe UI", 9), padx=20, justify="left").pack(anchor="w")

    # pole z komendą — tylko do odczytu, żeby nikt jej nie popsuł,
    # ale można ją zaznaczyć i skopiować także ręcznie
    ramka = tk.Frame(okno, padx=20, pady=8)
    ramka.pack(anchor="w")
    pole = tk.Entry(ramka, font=("Consolas", 10), width=48)
    pole.insert(0, KOMENDA_INSTALACJI)
    pole.configure(state="readonly")
    pole.pack(side="left")

    def kopiuj():
        """Wrzuca komendę do schowka — potem Ctrl+V w czarnym okienku."""
        okno.clipboard_clear()
        okno.clipboard_append(KOMENDA_INSTALACJI)
        przycisk_kopiuj.configure(text="Skopiowano!")

    przycisk_kopiuj = tk.Button(ramka, text="Kopiuj", command=kopiuj,
                                width=12)
    przycisk_kopiuj.pack(side="left", padx=6)

    decyzja = {"dalej": False}

    ramka_przyciski = tk.Frame(okno, pady=10)
    ramka_przyciski.pack()
    if not krytyczne:
        # brakuje tylko rasterio/numpy — można jechać bez produktów
        tk.Label(okno, text="(bez rasterio/numpy pobieranie zadziała, "
                            "ale nie zrobię produktów RGB/NDVI itd.)",
                 font=("Segoe UI", 8), fg="gray", padx=20).pack(anchor="w")

        def kontynuuj():
            decyzja["dalej"] = True
            okno.destroy()

        tk.Button(ramka_przyciski, text="Kontynuuj bez produktów",
                  command=kontynuuj, width=22).pack(side="left", padx=5)
    tk.Button(ramka_przyciski, text="Zamknij",
              command=okno.destroy, width=12).pack(side="left", padx=5)

    stopka(okno)
    okno.mainloop()

    if not decyzja["dalej"]:
        sys.exit("Doinstaluj biblioteki i odpal skrypt jeszcze raz. "
                 f"Komenda: {KOMENDA_INSTALACJI}")


# =============================================================================
#  OKIENKO GŁÓWNE — wszystkie ustawienia w jednym formularzu
# =============================================================================

def okienko_glowne():
    """
    Buduje JEDNO duże okienko z całym formularzem:
      1. wybór satelity (kropeczki)
      2. plik .gpkg z obszarem (przycisk "Przeglądaj...")
      3. parametry: daty, chmury, limit, ile naraz, login, hasło
      4. folder docelowy (przycisk "Przeglądaj...")
      5. które zdjęcia brać (wszystkie / ręcznie / top 3 z miesiąca)
      6. produkty (ptaszki, w dwóch kolumnach)
      + przycisk START i mały podpis autora z prawej (klik = mail)

    Sprytny bajer: przełączysz na Sentinel-1 (radar) — pole chmur
    i ptaszki produktów same się wyszarzają, bo dla radaru nie mają
    sensu. Wracasz na Sentinel-2 — odzyskują kolory.

    Zwraca słownik ze wszystkimi ustawieniami, albo kończy skrypt,
    jak zamkniesz okienko bez klikania START.
    """
    okno = tk.Tk()
    okno.title("Pobieraczka Sentinel — ustawienia")
    okno.attributes("-topmost", True)  # okienko zawsze na wierzchu

    # ----- sekcja 1: satelita -----------------------------------------------
    tk.Label(okno, text="1. Co ściągać?", font=("Segoe UI", 10, "bold"), fg=KOLOR,
             padx=15, pady=4).pack(anchor="w")
    satelita = tk.StringVar(value="S2-L2A")
    opcje_satelity = [
        ("Sentinel-2 L2A — zdjęcia optyczne, poprawione (polecane)",
         "S2-L2A"),
        ("Sentinel-2 L1C — zdjęcia optyczne, surowe", "S2-L1C"),
        ("Sentinel-1 GRD — radar (widzi przez chmury i w nocy)", "S1-GRD")]
    for opis, wartosc in opcje_satelity:
        tk.Radiobutton(okno, text=opis, variable=satelita, value=wartosc,
                       font=("Segoe UI", 9), padx=25).pack(anchor="w")

    # ----- sekcja 2: plik z obszarem ----------------------------------------
    tk.Label(okno, text="2. Obszar — plik .gpkg:",
             font=("Segoe UI", 10, "bold"), fg=KOLOR, padx=15, pady=4).pack(anchor="w")
    ramka_plik = tk.Frame(okno, padx=25)
    ramka_plik.pack(anchor="w")
    pole_gpkg = tk.Entry(ramka_plik, font=("Segoe UI", 9), width=52)
    pole_gpkg.pack(side="left")

    def wybierz_gpkg():
        sciezka = filedialog.askopenfilename(
            parent=okno, title="Wskaż plik GeoPackage (.gpkg)",
            filetypes=[("GeoPackage", "*.gpkg"),
                       ("Wszystkie pliki", "*.*")])
        if sciezka:
            pole_gpkg.delete(0, "end")
            pole_gpkg.insert(0, sciezka)

    tk.Button(ramka_plik, text="Przeglądaj...",
              command=wybierz_gpkg).pack(side="left", padx=6)

    # ----- sekcja 3: parametry ----------------------------------------------
    tk.Label(okno, text="3. Parametry:", font=("Segoe UI", 10, "bold"), fg=KOLOR,
             padx=15, pady=4).pack(anchor="w")
    ramka = tk.Frame(okno, padx=25)
    ramka.pack(anchor="w")

    def wiersz(nr, kolumna, etykieta, wartosc, ukryj=False, szer=14):
        """Pomocnik: jeden wiersz formularza (opis + pole do wpisania)."""
        tk.Label(ramka, text=etykieta, font=("Segoe UI", 9),
                 anchor="w").grid(row=nr, column=kolumna * 2, sticky="w",
                                  pady=2, padx=(0, 4))
        pole = tk.Entry(ramka, font=("Segoe UI", 9), width=szer,
                        show="*" if ukryj else "")  # hasło jako gwiazdki
        pole.insert(0, wartosc)
        pole.grid(row=nr, column=kolumna * 2 + 1, pady=2, padx=(0, 15))
        return pole

    p_od = wiersz(0, 0, "Data OD:", DOMYSLNA_DATA_OD)
    p_do = wiersz(0, 1, "Data DO:", DOMYSLNA_DATA_DO)
    p_chmury = wiersz(1, 0, "Maks. chmury (%):",
                      str(DOMYSLNE_ZACHMURZENIE))
    p_limit = wiersz(1, 1, "Maks. liczba zdjęć:", str(DOMYSLNY_LIMIT_SCEN))
    p_naraz = wiersz(2, 0, "Ile naraz (1-4):", str(DOMYSLNIE_ILE_NARAZ))
    # jeśli kiedyś zaznaczono "zapamiętaj" — pola wypełniają się same
    pamiec_login, pamiec_haslo = wczytaj_pamiec()
    p_login = wiersz(3, 0, "Login (e-mail):",
                     pamiec_login or DOMYSLNY_LOGIN, szer=32)
    p_haslo = wiersz(4, 0, "Hasło:", pamiec_haslo, ukryj=True, szer=32)
    czy_pamietac = tk.BooleanVar(value=bool(pamiec_haslo))
    tk.Checkbutton(ramka, text="Zapamiętaj login i hasło na tym "
                               "komputerze",
                   variable=czy_pamietac, font=("Segoe UI", 8)
                   ).grid(row=5, column=0, columnspan=4, sticky="w")

    # --- "Nie masz konta?" — rozwijana ściągawka ----------------------------
    opis_konta = tk.Label(
        okno,
        text=("Konto jest darmowe i zakłada się je w 2 minuty "
              "(kliknij ten tekst, a strona otworzy się sama):\n"
              "1. Wejdź na dataspace.copernicus.eu\n"
              "2. Kliknij Register w prawym górnym rogu\n"
              "3. Wypełnij formularz (imię, e-mail, hasło)\n"
              "4. Potwierdź konto linkiem z maila\n"
              "5. Wpisz e-mail i hasło powyżej — gotowe!"),
        font=("Segoe UI", 8), justify="left", padx=45, fg="gray25",
        cursor="hand2")
    opis_konta.bind(
        "<Button-1>",
        lambda _: webbrowser.open("https://dataspace.copernicus.eu"))

    def przelacz_konto():
        """Pokazuje/chowa ściągawkę o zakładaniu konta."""
        if opis_konta.winfo_ismapped():
            opis_konta.pack_forget()
            przycisk_konto.configure(
                text="Nie masz konta? Pokaż, jak założyć ▸")
        else:
            opis_konta.pack(anchor="w", after=przycisk_konto)
            przycisk_konto.configure(
                text="Nie masz konta? Zwiń podpowiedź ▾")

    przycisk_konto = tk.Button(
        okno, text="Nie masz konta? Pokaż, jak założyć ▸",
        command=przelacz_konto, relief="flat", bd=0, fg=KOLOR,
        activeforeground=KOLOR_HOVER, cursor="hand2",
        font=("Segoe UI", 9, "bold"))
    przycisk_konto.pack(anchor="w", padx=25)

    # ----- sekcja 4: folder docelowy ----------------------------------------
    tk.Label(okno, text="4. Folder na pobrane pliki:",
             font=("Segoe UI", 10, "bold"), fg=KOLOR, padx=15, pady=4).pack(anchor="w")
    ramka_folder = tk.Frame(okno, padx=25)
    ramka_folder.pack(anchor="w")
    pole_folder = tk.Entry(ramka_folder, font=("Segoe UI", 9), width=52)
    pole_folder.pack(side="left")

    def wybierz_folder():
        sciezka = filedialog.askdirectory(
            parent=okno, title="Folder na pobrane zdjęcia")
        if sciezka:
            pole_folder.delete(0, "end")
            pole_folder.insert(0, sciezka)

    tk.Button(ramka_folder, text="Przeglądaj...",
              command=wybierz_folder).pack(side="left", padx=6)

    # ----- sekcja 5: które zdjęcia brać -------------------------------------
    tk.Label(okno, text="5. Które zdjęcia pobrać?",
             font=("Segoe UI", 10, "bold"), fg=KOLOR, padx=15, pady=4).pack(anchor="w")
    tk.Label(okno, text="(po wyszukaniu i tak zobaczysz listę i będziesz "
                        "mógł zmienić zdanie)",
             font=("Segoe UI", 8), fg="gray", padx=25).pack(anchor="w")
    strategia = tk.StringVar(value="WSZYSTKIE")
    for opis, wartosc in STRATEGIE:
        tk.Radiobutton(okno, text=opis, variable=strategia, value=wartosc,
                       font=("Segoe UI", 9), padx=25).pack(anchor="w")

    # ----- sekcja 6: produkty (ptaszki w 2 kolumnach) -----------------------
    tk.Label(okno, text="6. Produkty z każdego zdjęcia (tylko Sentinel-2):",
             font=("Segoe UI", 10, "bold"), fg=KOLOR, padx=15, pady=4).pack(anchor="w")
    ramka_prod = tk.Frame(okno, padx=25)
    ramka_prod.pack(anchor="w")

    zmienne_produktow, ptaszki = {}, []
    for numer, (klucz, przepis) in enumerate(PRODUKTY.items()):
        zmienne_produktow[klucz] = tk.BooleanVar(
            value=DOMYSLNE_PRODUKTY.get(klucz, False))
        ptaszek = tk.Checkbutton(ramka_prod, text=przepis["opis"],
                                 variable=zmienne_produktow[klucz],
                                 font=("Segoe UI", 9))
        ptaszek.grid(row=numer // 2, column=numer % 2, sticky="w",
                     padx=(0, 12))
        ptaszki.append(ptaszek)

    if not JEST_RASTERIO:
        tk.Label(okno, text="(produkty niedostępne — doinstaluj: "
                            f"{KOMENDA_INSTALACJI})",
                 font=("Segoe UI", 8), fg="red", padx=25).pack(anchor="w")

    # ----- wyszarzanie pól, które nie mają sensu dla radaru -----------------
    def przelacz_satelite(*_):
        """Sentinel-1? Wyszarzamy chmury i produkty. Sentinel-2? Włączamy."""
        radar = satelita.get() == "S1-GRD"
        p_chmury.configure(state="disabled" if radar else "normal")
        for ptaszek in ptaszki:
            ptaszek.configure(
                state="disabled" if (radar or not JEST_RASTERIO)
                else "normal")

    satelita.trace_add("write", przelacz_satelite)
    przelacz_satelite()  # ustawiamy stan początkowy

    # ----- przycisk START + podpis ------------------------------------------
    wynik = {}

    def start():
        """Zbiera wszystko z formularza, sprawdza braki i zamyka okno."""
        if not pole_gpkg.get().strip():
            messagebox.showwarning("Brakuje pliku",
                                   "Wskaż plik .gpkg z obszarem (pkt 2).",
                                   parent=okno)
            return
        if not pole_folder.get().strip():
            messagebox.showwarning("Brakuje folderu",
                                   "Wskaż folder na pobrane pliki (pkt 4).",
                                   parent=okno)
            return
        wynik.update({
            "satelita": satelita.get(),
            "plik_gpkg": pole_gpkg.get().strip(),
            "folder": pole_folder.get().strip(),
            "data_od": p_od.get().strip(),
            "data_do": p_do.get().strip(),
            "chmury": p_chmury.get().strip() or "100",
            "limit": p_limit.get().strip() or "200",
            "naraz": max(1, min(4, int(p_naraz.get().strip() or "3"))),
            "login": p_login.get().strip(),
            "haslo": p_haslo.get(),
            "strategia": strategia.get(),
            "produkty": {klucz: zmienna.get() for klucz, zmienna
                         in zmienne_produktow.items()},
        })
        # zapamiętanie (lub wyczyszczenie) loginu i hasła
        if czy_pamietac.get():
            zapisz_pamiec(wynik["login"], wynik["haslo"])
        else:
            wyczysc_pamiec()
        okno.destroy()

    # przycisk startu w kolorze wiodącym; cursor="hand2" = rączka po
    # najechaniu, activebackground = ciemniejszy odcień przy kliknięciu —
    # żeby każdy widział, że to klikalny przycisk
    tk.Button(okno, text="URUCHOM POBIERANIE", command=start, width=24,
              font=("Segoe UI", 11, "bold"), bg=KOLOR, fg="white",
              activebackground=KOLOR_HOVER, activeforeground="white",
              cursor="hand2", relief="flat", pady=6).pack(pady=(12, 2))
    stopka(okno)

    okno.mainloop()
    if not wynik:
        sys.exit("Zamknięto okienko bez klikania START — kończę.")
    return wynik


# =============================================================================
#  OKIENKO WYBORU SCEN — lista z ptaszkami po wyszukaniu
# =============================================================================

def okienko_wyboru_scen(sceny, strategia_startowa):
    """
    Pokazuje znalezione zdjęcia na liście z ptaszkami i pozwala wybrać,
    które faktycznie ściągnąć. Do dyspozycji:
      - strategia: wszystkie / ręcznie / najlepsze 3 z miesiąca
        (przełączenie od razu przestawia ptaszki na liście)
      - filtr miesięcy: odznacz miesiąc, a jego zdjęcia wypadają z gry
      - ptaszek "Zaznacz wszystkie" i ptaszki przy każdym zdjęciu
      - licznik na dole: ile zaznaczonych i ile to GB
    Po kliknięciu "POBIERZ ZAZNACZONE" pyta jeszcze "Jesteś pewien?" —
    z podsumowaniem liczby zdjęć i rozmiaru.

    Zwraca listę wybranych scen (może być pusta, jak klikniesz Anuluj).
    """
    # przygotowujemy podręczne opisy każdej sceny
    opisy = []
    for scena in sceny:
        data = scena.get("ContentDate", {}).get("Start", "")[:10]
        chmury = odczytaj_zachmurzenie(scena)
        rozmiar_gb = scena.get("ContentLength", 0) / 1024**3
        opisy.append({"scena": scena, "data": data,
                      "miesiac": data[:7],  # np. "2024-06"
                      "chmury": chmury if chmury is not None else 101,
                      "gb": rozmiar_gb})

    miesiace = sorted({opis["miesiac"] for opis in opisy})

    okno = tk.Tk()
    okno.title("Wybór zdjęć do pobrania")
    okno.attributes("-topmost", True)

    laczny_gb = sum(opis["gb"] for opis in opisy)
    tk.Label(okno, text=f"Znalazłem {len(sceny)} zdjęć "
                        f"(razem ok. {laczny_gb:.1f} GB). Które ściągamy?",
             font=("Segoe UI", 11, "bold"), padx=15,
             pady=8).pack(anchor="w")

    # ----- strategia --------------------------------------------------------
    strategia = tk.StringVar(value=strategia_startowa)
    ramka_strategia = tk.Frame(okno, padx=15)
    ramka_strategia.pack(anchor="w")
    for opis_s, wartosc in STRATEGIE:
        tk.Radiobutton(ramka_strategia, text=opis_s, variable=strategia,
                       value=wartosc,
                       font=("Segoe UI", 9)).pack(anchor="w")

    # ----- filtr miesięcy ---------------------------------------------------
    tk.Label(okno, text="Miesiące (odznacz, żeby pominąć):",
             font=("Segoe UI", 9, "bold"), padx=15,
             pady=(6, 0)).pack(anchor="w")
    ramka_miesiace = tk.Frame(okno, padx=25)
    ramka_miesiace.pack(anchor="w")
    zmienne_miesiecy = {}
    for numer, miesiac in enumerate(miesiace):
        zmienne_miesiecy[miesiac] = tk.BooleanVar(value=True)
        rok, mies = miesiac.split("-")
        tk.Checkbutton(ramka_miesiace,
                       text=f"{NAZWY_MIESIECY[mies]} {rok}",
                       variable=zmienne_miesiecy[miesiac],
                       font=("Segoe UI", 8)).grid(
            row=numer // 4, column=numer % 4, sticky="w", padx=(0, 10))

    # ----- "zaznacz wszystkie" + przewijana lista scen ----------------------
    zaznacz_wszystkie = tk.BooleanVar(value=True)
    tk.Checkbutton(okno, text="Zaznacz wszystkie (w wybranych miesiącach)",
                   variable=zaznacz_wszystkie,
                   font=("Segoe UI", 9, "bold"),
                   padx=15).pack(anchor="w", pady=(6, 0))

    # lista bywa długa, więc pakujemy ją w ramkę z suwakiem:
    # Canvas (płótno) + Scrollbar (suwak) + Frame (właściwa lista) —
    # standardowy trik tkinter na przewijaną zawartość
    ramka_lista = tk.Frame(okno, padx=15)
    ramka_lista.pack(fill="both", expand=True)
    plotno = tk.Canvas(ramka_lista, height=240, width=600)
    suwak = tk.Scrollbar(ramka_lista, orient="vertical",
                         command=plotno.yview)
    wnetrze = tk.Frame(plotno)
    wnetrze.bind("<Configure>",
                 lambda _: plotno.configure(
                     scrollregion=plotno.bbox("all")))
    plotno.create_window((0, 0), window=wnetrze, anchor="nw")
    plotno.configure(yscrollcommand=suwak.set)
    plotno.pack(side="left", fill="both", expand=True)
    suwak.pack(side="right", fill="y")
    # kółko myszy też ma przewijać listę
    plotno.bind_all("<MouseWheel>",
                    lambda e: plotno.yview_scroll(-e.delta // 120,
                                                  "units"))

    zmienne_scen, ptaszki_scen = [], []
    for opis in opisy:
        zmienna = tk.BooleanVar(value=True)
        chmury_txt = (f"{opis['chmury']:5.1f}%"
                      if opis["chmury"] <= 100 else "  brak")
        nazwa_krotka = opis["scena"]["Name"][:48]
        ptaszek = tk.Checkbutton(
            wnetrze,
            text=f"{opis['data']}   chmury:{chmury_txt}   "
                 f"{opis['gb']:5.2f} GB   {nazwa_krotka}",
            variable=zmienna, font=("Consolas", 8))
        ptaszek.pack(anchor="w")
        zmienne_scen.append(zmienna)
        ptaszki_scen.append(ptaszek)

    # ----- licznik zaznaczonych ---------------------------------------------
    licznik = tk.Label(okno, text="", font=("Segoe UI", 9, "bold"),
                       padx=15, pady=4)
    licznik.pack(anchor="w")

    def przelicz_licznik(*_):
        """Aktualizuje napis: ile zaznaczonych zdjęć i ile ważą."""
        ile = sum(1 for zmienna in zmienne_scen if zmienna.get())
        gb = sum(opis["gb"] for opis, zmienna
                 in zip(opisy, zmienne_scen) if zmienna.get())
        licznik.configure(
            text=f"Zaznaczone: {ile} zdjęć, ok. {gb:.1f} GB")

    for zmienna in zmienne_scen:
        zmienna.trace_add("write", przelicz_licznik)

    # ----- automatyczne przestawianie ptaszków ------------------------------
    def zastosuj_strategie(*_):
        """
        Przestawia ptaszki według strategii i filtru miesięcy:
          - zdjęcia z odznaczonych miesięcy: odptaszkowane i wyszarzone
          - WSZYSTKIE: ptaszek przy każdym zdjęciu z wybranych miesięcy
          - TOP3: w każdym miesiącu ptaszek tylko przy 3 zdjęciach
            o najmniejszym zachmurzeniu
          - RECZNIE: nic nie ruszamy — klikasz sam
        """
        wybrane_miesiace = {miesiac for miesiac, zmienna
                            in zmienne_miesiecy.items() if zmienna.get()}

        # najpierw filtr miesięcy (zawsze obowiązuje)
        for opis, zmienna, ptaszek in zip(opisy, zmienne_scen,
                                          ptaszki_scen):
            if opis["miesiac"] not in wybrane_miesiace:
                zmienna.set(False)
                ptaszek.configure(state="disabled")
            else:
                ptaszek.configure(state="normal")

        tryb = strategia.get()
        if tryb == "WSZYSTKIE":
            for opis, zmienna in zip(opisy, zmienne_scen):
                zmienna.set(opis["miesiac"] in wybrane_miesiace)
        elif tryb == "TOP3":
            # w każdym miesiącu sortujemy po chmurach i bierzemy 3 pierwsze
            for miesiac in wybrane_miesiace:
                w_miesiacu = [(opis, zmienna) for opis, zmienna
                              in zip(opisy, zmienne_scen)
                              if opis["miesiac"] == miesiac]
                w_miesiacu.sort(key=lambda para: para[0]["chmury"])
                for numer, (opis, zmienna) in enumerate(w_miesiacu):
                    zmienna.set(numer < 3)
        # RECZNIE: zostawiamy ptaszki tak, jak są

    def przelacz_wszystkie(*_):
        """Reakcja na główny ptaszek: zaznacz/odznacz całą listę."""
        wybrane_miesiace = {miesiac for miesiac, zmienna
                            in zmienne_miesiecy.items() if zmienna.get()}
        for opis, zmienna in zip(opisy, zmienne_scen):
            zmienna.set(zaznacz_wszystkie.get()
                        and opis["miesiac"] in wybrane_miesiace)

    strategia.trace_add("write", zastosuj_strategie)
    for zmienna in zmienne_miesiecy.values():
        zmienna.trace_add("write", zastosuj_strategie)
    zaznacz_wszystkie.trace_add("write", przelacz_wszystkie)

    zastosuj_strategie()   # ustawiamy ptaszki według strategii z okienka 1
    przelicz_licznik()

    # ----- przyciski + potwierdzenie ----------------------------------------
    wynik = {"wybrane": None}

    def pobierz():
        """Zbiera zaznaczone sceny i pyta, czy na pewno ściągamy."""
        wybrane = [opis["scena"] for opis, zmienna
                   in zip(opisy, zmienne_scen) if zmienna.get()]
        if not wybrane:
            messagebox.showwarning("Nic nie zaznaczone",
                                   "Zaznacz chociaż jedno zdjęcie "
                                   "(albo kliknij Anuluj).", parent=okno)
            return
        gb = sum(opis["gb"] for opis, zmienna
                 in zip(opisy, zmienne_scen) if zmienna.get())
        # ostatnie "czy jesteś pewien?" — z konkretami
        if messagebox.askyesno(
                "Jesteś pewien?",
                f"Do pobrania: {len(wybrane)} zdjęć, ok. {gb:.1f} GB.\n"
                f"Upewnij się, że masz tyle miejsca na dysku.\n\n"
                f"Ruszamy?", parent=okno):
            wynik["wybrane"] = wybrane
            okno.destroy()

    ramka_przyciski = tk.Frame(okno, pady=8)
    ramka_przyciski.pack()
    tk.Button(ramka_przyciski, text="POBIERZ ZAZNACZONE",
              command=pobierz, width=22,
              font=("Segoe UI", 10, "bold"), bg=KOLOR, fg="white",
              activebackground=KOLOR_HOVER, activeforeground="white",
              cursor="hand2", relief="flat").pack(side="left", padx=5)
    tk.Button(ramka_przyciski, text="Anuluj",
              command=okno.destroy, width=12).pack(side="left", padx=5)
    stopka(okno)

    okno.mainloop()
    return wynik["wybrane"] or []


# =============================================================================
#  CZĘŚĆ ROBOCZA — obszar, logowanie, szukanie
# =============================================================================

def zapytaj_o_wszystko():
    """
    Pokazuje okienko główne z formularzem i porządkuje odpowiedzi.
    """
    gadaj("KROK 1/5: Otwieram okienko z ustawieniami — uzupełnij i "
          "kliknij START...")
    u = okienko_glowne()

    # sprzątanie po wyborach, które się wykluczają:
    if u["satelita"] == "S1-GRD":
        u["produkty"] = {klucz: False for klucz in PRODUKTY}
    if u["satelita"] == "S2-L1C" and u["produkty"].get("SCL"):
        gadaj("  Uwaga: mapa klas SCL istnieje tylko w L2A — pomijam ją "
              "dla L1C.")
        u["produkty"]["SCL"] = False
    if not JEST_RASTERIO:
        u["produkty"] = {klucz: False for klucz in PRODUKTY}

    wybrane = [nazwa for nazwa, tak in u["produkty"].items() if tak]
    gadaj(f"  Satelita: {u['satelita']}, daty {u['data_od']} — "
          f"{u['data_do']}, chmury do {u['chmury']}%, "
          f"limit {u['limit']} szt., {u['naraz']} naraz.")
    gadaj(f"  Obszar: {u['plik_gpkg']}")
    gadaj(f"  Folder: {u['folder']}")
    gadaj(f"  Strategia wyboru: {u['strategia']}")
    gadaj(f"  Produkty: {', '.join(wybrane) if wybrane else 'żadne'}")
    gadaj("KROK 1/5: Zrobione — mam komplet ustawień.")
    return u


def wczytaj_obszar(sciezka):
    """
    Otwiera plik .gpkg i zamienia Twój obszar na prosty tekstowy opis
    granic (tzw. WKT) — bo tylko taki format rozumie serwis Copernicus.
    Po drodze: przeliczenie współrzędnych na stopnie geograficzne,
    sklejenie kawałków w jeden kształt, wygładzenie granic.
    """
    gadaj("KROK 2/5: Zabieram się za Twój plik z obszarem...")
    gadaj(f"  Otwieram: {sciezka} (przy dużych plikach chwilę to trwa)...")

    if not os.path.exists(sciezka):
        sys.exit(f"OJ: nie widzę pliku: {sciezka}")
    dane = gpd.read_file(sciezka)
    if dane.empty:
        sys.exit("OJ: warstwa jest pusta — w tym pliku nie ma żadnego "
                 "obszaru.")
    gadaj(f"  W warstwie znalazłem {len(dane)} obiekt(ów).")

    gadaj(f"  Twój układ współrzędnych to: {dane.crs} — przeliczam na "
          f"zwykłe stopnie geograficzne (WGS84)...")
    dane = dane.to_crs(epsg=4326)

    gadaj("  Sklejam wszystkie obiekty w jeden obszar...")
    obszar = dane.union_all()
    if obszar.geom_type != "Polygon":
        gadaj("  Obszar ma kilka osobnych części — obrysowuję je jedną "
              "wspólną obwódką.")
        obszar = obszar.convex_hull
    obszar = obszar.simplify(0.001)

    zach, pd_, wsch, pn = obszar.bounds
    gadaj(f"  Obszar rozciąga się mniej więcej: {pd_:.3f}—{pn:.3f} szer., "
          f"{zach:.3f}—{wsch:.3f} dł. geogr.")
    gadaj("KROK 2/5: Zrobione — obszar gotowy.")
    return obszar.wkt


def pobierz_token(login, haslo, cicho=False):
    """
    Loguje się do Copernicus i odbiera token — jednorazową przepustkę
    ważną ok. 10 minut. Każde pobieranie bierze świeżą.
    "cicho=True" wyłącza meldunki, żeby nie spamować przy odświeżaniu.
    """
    if not cicho:
        gadaj("  Wysyłam login i hasło do serwisu Copernicus...")
    dane_logowania = {"client_id": "cdse-public", "grant_type": "password",
                      "username": login, "password": haslo}
    odpowiedz = requests.post(ADRES_LOGOWANIA, data=dane_logowania,
                              timeout=30)
    if odpowiedz.status_code != 200:
        sys.exit("OJ: logowanie nie wyszło — sprawdź login i hasło.\n"
                 f"Serwer odpowiedział: {odpowiedz.text[:300]}")
    if not cicho:
        gadaj("  Serwis przyjął dane i wydał przepustkę (token).")
    return odpowiedz.json()["access_token"]


def wyszukaj_sceny(u):
    """
    Pyta katalog Copernicus o zdjęcia pasujące do Twoich warunków.
    Sentinel-2: warunek na poziom (L2A/L1C) i chmury.
    Sentinel-1: produkty GRD, bez chmur (radar!).
    Wyniki przychodzą porcjami po 100 — kręcimy się, aż zbierzemy całość.
    """
    gadaj("KROK 3/5: Szukam zdjęć w katalogu Copernicus...")

    if u["satelita"].startswith("S2"):
        poziom = "L2A" if u["satelita"] == "S2-L2A" else "L1C"
        gadaj(f"  Warunki: Sentinel-2 {poziom}, {u['data_od']} — "
              f"{u['data_do']}, chmury do {u['chmury']}%.")
        filtr = (
            f"Collection/Name eq 'SENTINEL-2' "
            f"and contains(Name,'{poziom}') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;"
            f"{u['obszar_wkt']}') "
            f"and ContentDate/Start ge {u['data_od']}T00:00:00.000Z "
            f"and ContentDate/Start le {u['data_do']}T23:59:59.999Z "
            f"and Attributes/OData.CSC.DoubleAttribute/any("
            f"att:att/Name eq 'cloudCover' and "
            f"att/OData.CSC.DoubleAttribute/Value le {u['chmury']})")
    else:
        gadaj(f"  Warunki: Sentinel-1 GRD (radar), {u['data_od']} — "
              f"{u['data_do']} — chmury nas nie obchodzą :)")
        filtr = (
            f"Collection/Name eq 'SENTINEL-1' "
            f"and contains(Name,'GRD') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;"
            f"{u['obszar_wkt']}') "
            f"and ContentDate/Start ge {u['data_od']}T00:00:00.000Z "
            f"and ContentDate/Start le {u['data_do']}T23:59:59.999Z")

    adres = (f"{ADRES_KATALOGU}?$filter={filtr}"
             f"&$orderby=ContentDate/Start asc&$top=100&$expand=Attributes")

    sceny, porcja = [], 0
    while adres:
        porcja += 1
        gadaj(f"  Pytam serwis o porcję nr {porcja} (kilkanaście sekund, "
              f"spokojnie)...")
        odpowiedz = requests.get(adres, timeout=60)
        if odpowiedz.status_code != 200:
            sys.exit(f"OJ: wyszukiwanie nie wyszło: {odpowiedz.text[:300]}")
        wynik = odpowiedz.json()
        nowe = wynik.get("value", [])
        sceny.extend(nowe)
        gadaj(f"  Porcja nr {porcja}: dostałem {len(nowe)} zdjęć "
              f"(razem {len(sceny)}).")
        adres = wynik.get("@odata.nextLink")

    gadaj(f"KROK 3/5: Zrobione — znalazłem łącznie {len(sceny)} zdjęć.")
    return sceny


def odczytaj_zachmurzenie(scena):
    """Wyłuskuje z opisu zdjęcia, ile procent zajmują chmury."""
    for atrybut in scena.get("Attributes", []):
        if atrybut.get("Name") == "cloudCover":
            return atrybut.get("Value")
    return None


def wypisz_liste_scen(sceny):
    """Wypisuje listę znalezionych zdjęć w konsoli (dla porządku)."""
    gadaj("KROK 4/5: Oto co znalazłem (wybór zrobisz w okienku):")
    print("-" * 100, flush=True)
    for numer, scena in enumerate(sceny, start=1):
        data = scena.get("ContentDate", {}).get("Start", "")[:10]
        chmury = odczytaj_zachmurzenie(scena)
        chmury_txt = f"{chmury:5.1f}%" if chmury is not None else "  brak"
        rozmiar_gb = scena.get("ContentLength", 0) / 1024**3
        print(f"{numer:3d}. {data}  chmury:{chmury_txt}  "
              f"{rozmiar_gb:5.2f} GB  {scena['Name']}", flush=True)
    print("-" * 100, flush=True)


# =============================================================================
#  ŚCIĄGANIE — pojedyncza scena (odpalana przez kilku "pracowników" naraz)
# =============================================================================

def pobierz_scene(scena, numer, ile_wszystkich, folder, login, haslo):
    """
    Ściąga jedno zdjęcie (jako plik .zip). Ta funkcja jest odpalana przez
    kilku "pracowników" (wątków) równocześnie — dlatego każdy meldunek
    zaczyna się od [numer/ile], żebyś wiedział, które zdjęcie co robi.

    Po kolei: pomijamy jak już jest, bierzemy świeżą przepustkę, idziemy
    za przekierowaniami serwisu, zapisujemy po 8 MB z meldunkiem co
    ok. 200 MB. Jak pobieranie się wykrzaczy — próbujemy jeszcze 2 razy.
    W trakcie ściągania plik nazywa się .part — właściwą nazwę dostaje
    dopiero po udanym pobraniu, więc nie zostają zepsute "niby-gotowe"
    pliki. Zwraca (ścieżka_zipa_lub_None, opis_statusu, ile_sekund).
    """
    kto = f"[{numer}/{ile_wszystkich}]"
    nazwa_pliku = scena["Name"].replace(".SAFE", "") + ".zip"
    sciezka_pliku = os.path.join(folder, nazwa_pliku)
    sciezka_tymczasowa = sciezka_pliku + ".part"
    start = time.time()

    if os.path.exists(sciezka_pliku):
        gadaj(f"{kto} POMIJAM — już jest na dysku: {nazwa_pliku}")
        return sciezka_pliku, "pominięto (już było)", 0

    for proba in range(1, ILE_PROB + 1):
        try:
            if proba > 1:
                gadaj(f"{kto} Próba {proba}/{ILE_PROB} dla {nazwa_pliku}...")
            token = pobierz_token(login, haslo, cicho=True)
            sesja = requests.Session()
            sesja.headers.update({"Authorization": f"Bearer {token}"})

            adres = f"{ADRES_POBIERANIA}({scena['Id']})/$value"
            odpowiedz = sesja.get(adres, allow_redirects=False, stream=True,
                                  timeout=60)
            while odpowiedz.status_code in (301, 302, 303, 307):
                adres = odpowiedz.headers["Location"]
                odpowiedz = sesja.get(adres, allow_redirects=False,
                                      stream=True, timeout=60)

            if odpowiedz.status_code != 200:
                raise RuntimeError(f"kod {odpowiedz.status_code}: "
                                   f"{odpowiedz.text[:150]}")

            rozmiar_calkowity = int(odpowiedz.headers.get("Content-Length",
                                                          0))
            gadaj(f"{kto} Ściągam: {nazwa_pliku} "
                  f"({rozmiar_calkowity/1024**3:.2f} GB)")

            pobrano, ostatni_meldunek = 0, 0
            with open(sciezka_tymczasowa, "wb") as plik:
                for fragment in odpowiedz.iter_content(
                        chunk_size=8 * 1024 * 1024):
                    plik.write(fragment)
                    pobrano += len(fragment)
                    # meldunek co ok. 200 MB — przy kilku pobieraniach
                    # naraz częstsze meldunki zrobiłyby bałagan na ekranie
                    if pobrano - ostatni_meldunek >= 200 * 1024 * 1024:
                        ostatni_meldunek = pobrano
                        procent = (100 * pobrano / rozmiar_calkowity
                                   if rozmiar_calkowity else 0)
                        gadaj(f"{kto}   ...{pobrano/1024**2:.0f} MB "
                              f"({procent:.0f}%) — {nazwa_pliku[:35]}")

            os.rename(sciezka_tymczasowa, sciezka_pliku)
            ile_trwalo = time.time() - start
            predkosc = pobrano / 1024**2 / ile_trwalo if ile_trwalo else 0
            gadaj(f"{kto} Mam! {nazwa_pliku} — {ile_trwalo/60:.1f} min "
                  f"({predkosc:.1f} MB/s)")
            return sciezka_pliku, "OK", ile_trwalo

        except Exception as blad:
            gadaj(f"{kto} OJ: próba {proba} nie wyszła ({blad}). "
                  + ("Odczekuję 15 s i próbuję jeszcze raz..."
                     if proba < ILE_PROB else "Poddaję się z tą sceną."))
            if os.path.exists(sciezka_tymczasowa):
                os.remove(sciezka_tymczasowa)
            if proba < ILE_PROB:
                time.sleep(15)

    return None, f"błąd po {ILE_PROB} próbach", time.time() - start


# =============================================================================
#  PRODUKTY — kompozycje, wskaźniki i mapa SCL z pobranych zipów
# =============================================================================

def rozciagnij_do_8bit(pasmo):
    """
    Zamienia surowe wartości z satelity (liczby rzędu tysięcy) na zakres
    1-255, czyli taki, jaki rozumieją zwykłe obrazki. Trik z percentylami:
    odcinamy 2% skrajnych pikseli i rozciągamy resztę na całą skalę —
    obraz robi się kontrastowy, a nie ciemna plama.

    WAŻNE: kafelki Sentinel-2 to sztywne kwadraty 110x110 km, a satelita
    często pokrywa je tylko częściowo — reszta to zera ("brak danych").
    Dlatego dane rozciągamy na 1-255, a zerom zostawiamy 0 i oznaczamy
    je jako nodata — QGIS pokaże obrzeża jako PRZEZROCZYSTE, a nie
    jako wielkie czarne pole.
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


def znajdz_pasma_w_zipie(zip_, potrzebne):
    """
    Szuka w zipie plików z potrzebnymi pasmami.
    W L2A pliki nazywają się np. "..._B11_20m.jp2" (pasmo + rozdzielczość),
    w L1C po prostu "..._B11.jp2". Sprawdzamy obie konwencje.
    Dostaje listę par (pasmo, rozdzielczość), zwraca słownik
    {(pasmo, rozdz.): ścieżka_w_zipie albo None}.
    """
    znalezione = {para: None for para in potrzebne}
    for nazwa_w_zipie in zip_.namelist():
        for (pasmo, rozdz) in znalezione:
            if (nazwa_w_zipie.endswith(f"_{pasmo}_{rozdz}.jp2")
                    or nazwa_w_zipie.endswith(f"_{pasmo}.jp2")):
                znalezione[(pasmo, rozdz)] = nazwa_w_zipie
    return znalezione


def zrob_produkty(sciezka_zip, folder, produkty, kto=""):
    """
    Wyciąga z pobranego zipa potrzebne pasma i robi wybrane produkty.

    Jak to działa:
      1. Patrzymy, jakich pasm potrzebują wybrane produkty (tylko te
         wypakowujemy — po co ruszać więcej z 1-gigabajtowego zipa).
      2. Wypakowujemy je do folderu tymczasowego.
      3. Dla każdego produktu:
         - PIERWSZE pasmo z jego listy wyznacza siatkę pikseli; pozostałe
           pasma są do niej dopasowywane (przeliczane w locie, gdy mają
           inną rozdzielczość — np. 10 m vs 20 m)
         - kompozycja: 3 pasma sklejone w kolorowy GeoTIFF 8-bit
         - wskaźnik: wzór (A-B)/(A+B) piksel po pikselu, wynik od -1
           do 1 jako GeoTIFF (w QGIS nadasz paletę jednym kliknięciem);
           tam gdzie brak danych wpisujemy -9999
         - SCL: przepisujemy gotową mapę klas ESA do GeoTIFF-a
      4. Sprzątamy folder tymczasowy.

    Produkty już istniejące na dysku pomijamy (wznawialność!).
    """
    wybrane = [nazwa for nazwa, tak in produkty.items() if tak]
    if not wybrane:
        return
    nazwa = os.path.basename(sciezka_zip).replace(".zip", "")

    do_zrobienia = [
        produkt for produkt in wybrane
        if not os.path.exists(os.path.join(folder,
                                           f"{nazwa}_{produkt}.tif"))]
    if not do_zrobienia:
        gadaj(f"{kto} Produkty dla {nazwa[:35]} już są — pomijam.")
        return

    gadaj(f"{kto} Robię produkty ({', '.join(do_zrobienia)}) dla: "
          f"{nazwa[:35]}...")

    # 1. lista potrzebnych pasm (bez powtórek)
    potrzebne = []
    for produkt in do_zrobienia:
        for para in PRODUKTY[produkt]["pasma"]:
            if para not in potrzebne:
                potrzebne.append(para)

    # 2. szukamy w zipie i wypakowujemy
    with zipfile.ZipFile(sciezka_zip) as zip_:
        znalezione = znajdz_pasma_w_zipie(zip_, potrzebne)
        folder_tymczasowy = tempfile.mkdtemp()
        wypakowane = {}
        for para, gdzie_w_zipie in znalezione.items():
            if gdzie_w_zipie:
                wypakowane[para] = zip_.extract(gdzie_w_zipie,
                                                folder_tymczasowy)

    def wczytaj(para, ksztalt=None):
        """
        Czyta pasmo z dysku. Jak podasz "ksztalt" (wysokość, szerokość),
        pasmo zostanie w locie przeliczone do tej siatki pikseli — tak
        godzimy pasma 10-metrowe z 20-metrowymi w jednym produkcie.
        Zwraca (macierz_pikseli, metryczka_z_układem_współrzędnych).
        """
        with rasterio.open(wypakowane[para]) as zrodlo:
            if ksztalt and (zrodlo.height, zrodlo.width) != ksztalt:
                dane = zrodlo.read(1, out_shape=ksztalt,
                                   resampling=Resampling.bilinear)
            else:
                dane = zrodlo.read(1)
            return dane, zrodlo.profile

    def zapisz(dane_lista, profil, dopisek, dtype, nodata=None,
               fotometria=None):
        """Wspólna końcówka: zapis gotowego produktu jako GeoTIFF."""
        profil.update(driver="GTiff", dtype=dtype, count=len(dane_lista),
                      compress="deflate")
        if nodata is not None:
            profil.update(nodata=nodata)
        if fotometria:
            profil.update(photometric=fotometria)
        cel = os.path.join(folder, f"{nazwa}_{dopisek}.tif")
        with rasterio.open(cel, "w", **profil) as wyjscie:
            for numer, warstwa in enumerate(dane_lista, start=1):
                wyjscie.write(warstwa, numer)
        gadaj(f"{kto}   gotowe: {os.path.basename(cel)}")

    # 3. robimy produkty po kolei
    for produkt in do_zrobienia:
        przepis = PRODUKTY[produkt]
        pasma = przepis["pasma"]

        brakuje = [pasmo for pasmo in pasma if pasmo not in wypakowane]
        if brakuje:
            gadaj(f"{kto}   OJ: brak pasm {brakuje} w zipie — pomijam "
                  f"{produkt} (w L1C nie ma np. mapy SCL).")
            continue

        try:
            if przepis["typ"] == "kompozycja":
                # pierwsze pasmo = siatka odniesienia
                pierwsze, profil = wczytaj(pasma[0])
                ksztalt = pierwsze.shape
                warstwy = [rozciagnij_do_8bit(pierwsze)]
                for para in pasma[1:]:
                    dane, _ = wczytaj(para, ksztalt)
                    warstwy.append(rozciagnij_do_8bit(dane))
                # nodata=0 -> obrzeża sceny przezroczyste w QGIS
                zapisz(warstwy, profil, produkt, "uint8",
                       nodata=0, fotometria="RGB")

            elif przepis["typ"] == "wskaznik":
                a, profil = wczytaj(pasma[0])
                b, _ = wczytaj(pasma[1], a.shape)
                a = a.astype("float32")
                b = b.astype("float32")
                # znacznik "odwroc" zamienia wzór na (B-A)/(B+A) —
                # patrz komentarz przy definicji NDSI
                if przepis.get("odwroc"):
                    a, b = b, a
                suma = a + b
                wskaznik = np.where(
                    suma > 0,
                    (a - b) / np.where(suma > 0, suma, 1),
                    -9999).astype("float32")
                zapisz([wskaznik], profil, produkt, "float32",
                       nodata=-9999)
                # gotowa paleta kolorów z legendą (QGIS wczyta ją sam)
                zapisz_qml(os.path.join(folder, f"{nazwa}_{produkt}.tif"),
                           produkt)

            elif przepis["typ"] == "scl":
                # SCL to gotowa mapa klas od ESA — tylko przepisujemy ją
                # do wygodnego GeoTIFF-a. Znaczenie klas (wartości pikseli):
                #   0 brak danych, 1 piksel wadliwy, 2 ciemne/cienie,
                #   3 cień chmury, 4 roślinność, 5 goła gleba, 6 woda,
                #   7 niepewne, 8 chmura średnia, 9 chmura gęsta,
                #   10 chmura wysoka (cirrus), 11 śnieg/lód
                dane, profil = wczytaj(pasma[0])
                zapisz([dane.astype("uint8")], profil, produkt, "uint8",
                       nodata=0)
                # kolorowa legenda klas SCL (QGIS wczyta ją sam)
                zapisz_qml(os.path.join(folder, f"{nazwa}_{produkt}.tif"),
                           produkt)

        except Exception as blad:
            gadaj(f"{kto}   OJ: {produkt} nie wyszedł ({blad}) — lecę "
                  f"dalej z resztą.")

    # 4. sprzątamy po sobie
    shutil.rmtree(folder_tymczasowy, ignore_errors=True)


# =============================================================================
#  TU WSZYSTKO SIĘ SPINA — główny przebieg
# =============================================================================

def program_glowny():
    print("=" * 70, flush=True)
    print(" POBIERACZKA SENTINEL — wersja masowa (Copernicus Data Space)",
          flush=True)
    print(f" {AUTOR} ({EMAIL_AUTORA})", flush=True)
    print("=" * 70, flush=True)
    start_calosci = time.time()

    # krok 0: czy są wszystkie biblioteki? (jak nie — ładny komunikat)
    sprawdz_biblioteki()

    # krok 1: jedno okienko z całym formularzem
    u = zapytaj_o_wszystko()  # "u" jak "ustawienia"

    # krok 2: obszar z gpkg + próbne logowanie
    u["obszar_wkt"] = wczytaj_obszar(u["plik_gpkg"])
    gadaj("  Sprawdzam logowanie do Copernicus...")
    pobierz_token(u["login"], u["haslo"])
    gadaj("  Działa, jesteś zalogowany.")

    # krok 3: szukanie
    sceny = wyszukaj_sceny(u)
    if not sceny:
        gadaj("Nic nie znalazłem :( Spróbuj zwiększyć zachmurzenie albo "
              "poszerzyć daty.")
        return

    limit = int(u["limit"])
    if len(sceny) > limit:
        gadaj(f"Uwaga: znalazłem {len(sceny)} zdjęć, ale Twój limit to "
              f"{limit} — biorę tylko pierwsze {limit}.")
        sceny = sceny[:limit]

    # krok 4: lista w konsoli + okienko wyboru scen (ptaszki, miesiące,
    # top3, zaznacz wszystkie) + potwierdzenie "jesteś pewien?"
    wypisz_liste_scen(sceny)
    gadaj("  Otwieram okienko wyboru zdjęć...")
    sceny = okienko_wyboru_scen(sceny, u["strategia"])
    if not sceny:
        gadaj("OK, nic nie ściągam. Do zobaczenia!")
        return
    gadaj(f"  Wybrano do pobrania: {len(sceny)} zdjęć.")

    # krok 5: MASOWE ściąganie — kilku "pracowników" naraz.
    # Ekipa wątków ściąga sceny równolegle; główny wątek odbiera gotowe
    # zipy w kolejności KOŃCZENIA i od razu liczy z nich produkty —
    # pobieranie i liczenie idą jednocześnie, nic nie czeka bezczynnie.
    gadaj(f"KROK 5/5: Zaczynam masowe ściąganie "
          f"({u['naraz']} naraz, {len(sceny)} scen w kolejce)!")
    os.makedirs(u["folder"], exist_ok=True)

    raport = []
    udane, nieudane, skonczone = 0, 0, 0

    with ThreadPoolExecutor(max_workers=u["naraz"]) as ekipa:
        zlecenia = {
            ekipa.submit(pobierz_scene, scena, numer, len(sceny),
                         u["folder"], u["login"], u["haslo"]): scena
            for numer, scena in enumerate(sceny, start=1)}

        for zlecenie in as_completed(zlecenia):
            scena = zlecenia[zlecenie]
            sciezka_zip, status, ile_sek = zlecenie.result()
            skonczone += 1
            kto = f"[{skonczone}/{len(sceny)} gotowych]"

            if sciezka_zip:
                udane += 1
                if u["satelita"].startswith("S2") and JEST_RASTERIO:
                    try:
                        zrob_produkty(sciezka_zip, u["folder"],
                                      u["produkty"], kto)
                    except Exception as blad:
                        gadaj(f"{kto} OJ: produkty dla tej sceny nie "
                              f"wyszły ({blad}) — sam zip jest OK.")
            else:
                nieudane += 1

            raport.append({
                "scena": scena["Name"],
                "data": scena.get("ContentDate", {}).get("Start", "")[:10],
                "chmury_%": odczytaj_zachmurzenie(scena),
                "rozmiar_GB": round(
                    scena.get("ContentLength", 0) / 1024**3, 2),
                "status": status,
                "czas_min": round(ile_sek / 60, 1)})

    # raport CSV — czarno na białym, co się udało (otworzysz w Excelu)
    sciezka_raportu = os.path.join(
        u["folder"], f"raport_{time.strftime('%Y%m%d_%H%M')}.csv")
    with open(sciezka_raportu, "w", newline="",
              encoding="utf-8-sig") as plik:  # utf-8-sig = polskie znaki
        piszacy = csv.DictWriter(plik, fieldnames=raport[0].keys(),
                                 delimiter=";")  # średniki lubi Excel
        piszacy.writeheader()
        piszacy.writerows(raport)
    gadaj(f"Raport zapisany: {sciezka_raportu}")

    ile_trwalo_min = (time.time() - start_calosci) / 60
    print("\n" + "=" * 70, flush=True)
    gadaj(f"GOTOWE! Udało się: {udane}, nie wyszło: {nieudane}. "
          f"Całość zajęła {ile_trwalo_min:.1f} min.")
    gadaj(f"Wszystko leży w: {u['folder']}")
    print("=" * 70, flush=True)

    tk.Tk().withdraw()
    messagebox.showinfo("Gotowe!",
                        f"Skończone!\nUdane: {udane}, błędy: {nieudane}\n"
                        f"Pliki i raport CSV w: {u['folder']}\n\n{AUTOR}")


# start (tylko gdy odpalasz ten plik bezpośrednio)
if __name__ == "__main__":
    program_glowny()
