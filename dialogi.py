# -*- coding: utf-8 -*-
"""
OKIENKA wtyczki "Pobieranie Sentinel" (Qt):
  - DialogGlowny: formularz z ustawieniami + log + pasek postępu
  - DialogWyboruScen: lista znalezionych zdjęć z ptaszkami, filtrem
    miesięcy i strategiami (wszystkie / ręcznie / top 3 z miesiąca)
  - WatekPobierania: pobieranie w tle, żeby QGIS się nie zawieszał

© Grzegorz Górniak
"""

import os

from qgis.PyQt.QtCore import (Qt, QDate, QThread, pyqtSignal, QSettings,
                              QSize)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QSpinBox, QDateEdit, QCheckBox, QRadioButton,
    QGroupBox, QFileDialog, QMessageBox, QTextEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QButtonGroup, QAbstractItemView)
from qgis.core import (QgsProject, QgsVectorLayer, QgsGeometry,
                       QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsMapLayerProxyModel)
from qgis.gui import QgsMapLayerComboBox

from concurrent.futures import ThreadPoolExecutor, as_completed

from . import silnik

AUTOR = "© Grzegorz Górniak"
EMAIL_AUTORA = "gorniakgrzegorz@gmail.com"
KOLOR = "#00bde7"                    # kolor wiodący wtyczki
FOLDER_WTYCZKI = os.path.dirname(__file__)


def ikona_wtyczki():
    """Sygnet wtyczki (SVG, z PNG jako zapasem)."""
    ikona = QIcon(os.path.join(FOLDER_WTYCZKI, "icon.svg"))
    if ikona.isNull():
        ikona = QIcon(os.path.join(FOLDER_WTYCZKI, "icon.png"))
    return ikona


def stopka():
    """
    Mały podpis autora: do prawej, drobny, czarny, bez podkreślenia,
    a mimo to klikalny — klik otwiera maila do autora.
    """
    napis = QLabel(f'<a href="mailto:{EMAIL_AUTORA}" '
                   f'style="color:black; text-decoration:none;">'
                   f'{AUTOR}</a>')
    napis.setOpenExternalLinks(True)
    napis.setAlignment(Qt.AlignmentFlag.AlignRight)
    napis.setStyleSheet("font-size: 8px; color: black;")
    return napis


def wkt_z_warstwy(warstwa):
    """
    Zamienia warstwę wektorową na tekst WKT w WGS84 — jedyny format,
    który rozumie serwis Copernicus. Skleja wszystkie obiekty w jeden
    kształt, wielokąty wieloczęściowe obrysowuje otoczką, upraszcza.
    """
    geometrie = [obiekt.geometry() for obiekt in warstwa.getFeatures()
                 if obiekt.hasGeometry()]
    if not geometrie:
        raise RuntimeError("Warstwa nie ma żadnych obiektów z geometrią.")
    geometria = QgsGeometry.unaryUnion(geometrie)

    # przeliczenie do zwykłych stopni geograficznych (WGS84)
    przeliczenie = QgsCoordinateTransform(
        warstwa.crs(), QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance())
    geometria.transform(przeliczenie)

    if geometria.isMultipart():
        geometria = geometria.convexHull()  # jedna wspólna obwódka
    geometria = geometria.simplify(0.001)   # wygładzenie (~100 m)

    # WAŻNA POPRAWKA: QGIS zapisuje typ geometrii jako "Polygon",
    # a serwis Copernicus wymaga DUŻYCH liter ("POLYGON") — inaczej
    # odpowiada błędem "Error during parsing at index 11".
    wkt = geometria.asWkt(5)  # 5 miejsc po przecinku w zupełności starcza
    nawias = wkt.find("(")
    return wkt[:nawias].strip().upper() + wkt[nawias:]


def wkt_z_pliku(sciezka):
    """Otwiera plik .gpkg jako warstwę QGIS i oddaje ją do wkt_z_warstwy."""
    warstwa = QgsVectorLayer(sciezka, "obszar", "ogr")
    if not warstwa.isValid():
        raise RuntimeError(f"Nie udało się otworzyć pliku: {sciezka}")
    return wkt_z_warstwy(warstwa)


# =============================================================================
#  WĄTEK POBIERANIA — cała robota w tle, QGIS zostaje żywy
# =============================================================================

class WatekPobierania(QThread):
    """
    Pobiera sceny równolegle (ThreadPoolExecutor) i liczy produkty,
    meldując postęp sygnałami — dzięki temu okno QGIS nie zamarza.
    """
    zaloguj = pyqtSignal(str)          # komunikat do okienka logu
    postep = pyqtSignal(int, int)      # (ile gotowych, ile wszystkich)
    koniec = pyqtSignal(int, int, str, list)  # udane, nieudane,
    #                                           raport, produkty (tify)

    def __init__(self, sceny, ustawienia):
        super().__init__()
        self.sceny = sceny
        self.u = ustawienia

    def run(self):
        u = self.u
        raport, tify = [], []
        udane, nieudane, skonczone = 0, 0, 0
        os.makedirs(u["folder"], exist_ok=True)

        with ThreadPoolExecutor(max_workers=u["naraz"]) as ekipa:
            zlecenia = {
                ekipa.submit(silnik.pobierz_scene, scena, numer,
                             len(self.sceny), u["folder"], u["login"],
                             u["haslo"], self.zaloguj.emit): scena
                for numer, scena in enumerate(self.sceny, start=1)}

            for zlecenie in as_completed(zlecenia):
                scena = zlecenia[zlecenie]
                sciezka_zip, status, ile_sek = zlecenie.result()
                skonczone += 1
                self.postep.emit(skonczone, len(self.sceny))
                kto = f"[{skonczone}/{len(self.sceny)} gotowych]"

                if sciezka_zip:
                    udane += 1
                    if u["satelita"].startswith("S2"):
                        try:
                            tify += silnik.zrob_produkty(
                                sciezka_zip, u["folder"], u["produkty"],
                                self.zaloguj.emit, kto)
                        except Exception as blad:
                            self.zaloguj.emit(
                                f"{kto} OJ: produkty nie wyszły ({blad}) "
                                f"— sam zip jest OK.")
                else:
                    nieudane += 1

                raport.append({
                    "scena": scena["Name"],
                    "data": scena.get("ContentDate", {}).get("Start",
                                                             "")[:10],
                    "chmury_%": silnik.odczytaj_zachmurzenie(scena),
                    "rozmiar_GB": round(
                        scena.get("ContentLength", 0) / 1024**3, 2),
                    "status": status,
                    "czas_min": round(ile_sek / 60, 1)})

        sciezka_raportu = silnik.zapisz_raport(u["folder"], raport) \
            if raport else ""
        self.koniec.emit(udane, nieudane, sciezka_raportu, tify)


# =============================================================================
#  OKIENKO WYBORU SCEN
# =============================================================================

class DialogWyboruScen(QDialog):
    """
    Lista znalezionych zdjęć z ptaszkami + strategia + filtr miesięcy
    + "zaznacz wszystkie" + licznik + potwierdzenie "Jesteś pewien?".
    Po zamknięciu w self.wybrane siedzi lista zaznaczonych scen.
    """

    def __init__(self, sceny, strategia_startowa, rodzic=None):
        super().__init__(rodzic)
        self.setWindowTitle("Wybór zdjęć do pobrania")
        self.setWindowIcon(ikona_wtyczki())
        self.resize(760, 620)
        self.wybrane = []
        # brandowanie: tytuły grup w kolorze wiodącym
        self.setStyleSheet(
            f"QGroupBox {{ font-weight: bold; }} "
            f"QGroupBox::title {{ color: {KOLOR}; }}")

        # podręczne opisy scen
        self.opisy = []
        for scena in sceny:
            data = scena.get("ContentDate", {}).get("Start", "")[:10]
            chmury = silnik.odczytaj_zachmurzenie(scena)
            self.opisy.append({
                "scena": scena, "data": data, "miesiac": data[:7],
                "chmury": chmury if chmury is not None else 101,
                "gb": scena.get("ContentLength", 0) / 1024**3})
        miesiace = sorted({opis["miesiac"] for opis in self.opisy})

        uklad = QVBoxLayout(self)
        laczne_gb = sum(opis["gb"] for opis in self.opisy)
        naglowek = QLabel(f"<b>Znalazłem {len(sceny)} zdjęć "
                          f"(razem ok. {laczne_gb:.1f} GB). "
                          f"Które ściągamy?</b>")
        uklad.addWidget(naglowek)

        # --- strategia ---
        grupa_strategii = QGroupBox("Strategia wyboru")
        uklad_strategii = QVBoxLayout(grupa_strategii)
        self.radia = QButtonGroup(self)
        for opis, wartosc in silnik.STRATEGIE:
            radio = QRadioButton(opis)
            radio.setProperty("wartosc", wartosc)
            if wartosc == strategia_startowa:
                radio.setChecked(True)
            self.radia.addButton(radio)
            uklad_strategii.addWidget(radio)
        uklad.addWidget(grupa_strategii)

        # --- filtr miesięcy ---
        grupa_miesiecy = QGroupBox("Miesiące (odznacz, żeby pominąć)")
        siatka_miesiecy = QGridLayout(grupa_miesiecy)
        self.ptaszki_miesiecy = {}
        for numer, miesiac in enumerate(miesiace):
            rok, mies = miesiac.split("-")
            ptaszek = QCheckBox(f"{silnik.NAZWY_MIESIECY[mies]} {rok}")
            ptaszek.setChecked(True)
            siatka_miesiecy.addWidget(ptaszek, numer // 4, numer % 4)
            self.ptaszki_miesiecy[miesiac] = ptaszek
        uklad.addWidget(grupa_miesiecy)

        # --- zaznacz wszystkie + tabela scen ---
        self.zaznacz_wszystkie = QCheckBox(
            "Zaznacz wszystkie (w wybranych miesiącach)")
        self.zaznacz_wszystkie.setChecked(True)
        uklad.addWidget(self.zaznacz_wszystkie)

        self.tabela = QTableWidget(len(self.opisy), 4)
        self.tabela.setHorizontalHeaderLabels(
            ["Data", "Chmury", "GB", "Nazwa sceny"])
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        for wiersz, opis in enumerate(self.opisy):
            chmury_txt = (f"{opis['chmury']:.1f}%"
                          if opis["chmury"] <= 100 else "brak")
            # pierwsza kolumna dostaje ptaszek (checkbox w komórce)
            komorka = QTableWidgetItem(opis["data"])
            komorka.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            komorka.setCheckState(Qt.CheckState.Checked)
            self.tabela.setItem(wiersz, 0, komorka)
            self.tabela.setItem(wiersz, 1, QTableWidgetItem(chmury_txt))
            self.tabela.setItem(wiersz, 2,
                                QTableWidgetItem(f"{opis['gb']:.2f}"))
            self.tabela.setItem(wiersz, 3,
                                QTableWidgetItem(opis["scena"]["Name"]))
        self.tabela.resizeColumnsToContents()
        uklad.addWidget(self.tabela)

        # --- licznik + przyciski ---
        self.licznik = QLabel("")
        uklad.addWidget(self.licznik)

        rzad = QHBoxLayout()
        przycisk_ok = QPushButton("POBIERZ ZAZNACZONE")
        przycisk_ok.setCursor(Qt.CursorShape.PointingHandCursor)  # rączka = klikalne
        przycisk_ok.setStyleSheet(
            f"QPushButton {{ background-color: {KOLOR}; color: white; "
            f"font-weight: bold; padding: 6px; border-radius: 4px; }} "
            f"QPushButton:hover {{ background-color: #009ec2; }}")
        przycisk_ok.clicked.connect(self.pobierz)
        przycisk_nie = QPushButton("Anuluj")
        przycisk_nie.clicked.connect(self.reject)
        rzad.addWidget(przycisk_ok)
        rzad.addWidget(przycisk_nie)
        uklad.addLayout(rzad)
        uklad.addWidget(stopka())

        # --- podpinamy reakcje ---
        self.radia.buttonClicked.connect(self.zastosuj_strategie)
        for ptaszek in self.ptaszki_miesiecy.values():
            ptaszek.stateChanged.connect(self.zastosuj_strategie)
        self.zaznacz_wszystkie.stateChanged.connect(
            self.przelacz_wszystkie)
        self.tabela.itemChanged.connect(lambda _: self.przelicz_licznik())

        self.zastosuj_strategie()

    # ---- pomocnicze ---------------------------------------------------------
    def _wybrane_miesiace(self):
        return {miesiac for miesiac, ptaszek
                in self.ptaszki_miesiecy.items() if ptaszek.isChecked()}

    def _strategia(self):
        przycisk = self.radia.checkedButton()
        return przycisk.property("wartosc") if przycisk else "WSZYSTKIE"

    def zastosuj_strategie(self, *_):
        """
        Przestawia ptaszki w tabeli według strategii i filtru miesięcy:
        WSZYSTKIE — ptaszek przy każdym zdjęciu z wybranych miesięcy;
        TOP3 — w każdym miesiącu 3 najmniej zachmurzone; RECZNIE — nic
        nie ruszamy (poza wyłączeniem odznaczonych miesięcy).
        """
        self.tabela.blockSignals(True)  # nie odpalaj licznika 500 razy
        miesiace = self._wybrane_miesiace()
        tryb = self._strategia()

        # filtr miesięcy obowiązuje zawsze
        for wiersz, opis in enumerate(self.opisy):
            komorka = self.tabela.item(wiersz, 0)
            if opis["miesiac"] not in miesiace:
                komorka.setCheckState(Qt.CheckState.Unchecked)
                komorka.setFlags(Qt.ItemFlag.ItemIsUserCheckable)  # wyszarz
            else:
                komorka.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)

        if tryb == "WSZYSTKIE":
            for wiersz, opis in enumerate(self.opisy):
                self.tabela.item(wiersz, 0).setCheckState(
                    Qt.CheckState.Checked if opis["miesiac"] in miesiace
                    else Qt.CheckState.Unchecked)
        elif tryb == "TOP3":
            for miesiac in miesiace:
                w_miesiacu = [(wiersz, opis) for wiersz, opis
                              in enumerate(self.opisy)
                              if opis["miesiac"] == miesiac]
                w_miesiacu.sort(key=lambda para: para[1]["chmury"])
                for numer, (wiersz, _) in enumerate(w_miesiacu):
                    self.tabela.item(wiersz, 0).setCheckState(
                        Qt.CheckState.Checked if numer < 3 else Qt.CheckState.Unchecked)
        # RECZNIE: zostawiamy jak jest

        self.tabela.blockSignals(False)
        self.przelicz_licznik()

    def przelacz_wszystkie(self, *_):
        """Główny ptaszek: zaznacz/odznacz całą listę (wybrane miesiące)."""
        self.tabela.blockSignals(True)
        miesiace = self._wybrane_miesiace()
        stan = (Qt.CheckState.Checked if self.zaznacz_wszystkie.isChecked()
                else Qt.CheckState.Unchecked)
        for wiersz, opis in enumerate(self.opisy):
            self.tabela.item(wiersz, 0).setCheckState(
                stan if opis["miesiac"] in miesiace else Qt.CheckState.Unchecked)
        self.tabela.blockSignals(False)
        self.przelicz_licznik()

    def przelicz_licznik(self):
        ile, gb = 0, 0.0
        for wiersz, opis in enumerate(self.opisy):
            if self.tabela.item(wiersz, 0).checkState() == Qt.CheckState.Checked:
                ile += 1
                gb += opis["gb"]
        self.licznik.setText(f"<b>Zaznaczone: {ile} zdjęć, "
                             f"ok. {gb:.1f} GB</b>")

    def pobierz(self):
        """Zbiera zaznaczone sceny i pyta 'Jesteś pewien?' z konkretami."""
        wybrane, gb = [], 0.0
        for wiersz, opis in enumerate(self.opisy):
            if self.tabela.item(wiersz, 0).checkState() == Qt.CheckState.Checked:
                wybrane.append(opis["scena"])
                gb += opis["gb"]
        if not wybrane:
            QMessageBox.warning(self, "Nic nie zaznaczone",
                                "Zaznacz chociaż jedno zdjęcie "
                                "(albo kliknij Anuluj).")
            return
        if QMessageBox.question(
                self, "Jesteś pewien?",
                f"Do pobrania: {len(wybrane)} zdjęć, ok. {gb:.1f} GB.\n"
                f"Upewnij się, że masz tyle miejsca na dysku.\n\nRuszamy?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.wybrane = wybrane
            self.accept()


# =============================================================================
#  OKIENKO GŁÓWNE
# =============================================================================

class DialogGlowny(QDialog):
    """
    Główny formularz wtyczki — wszystko w jednym oknie:
    satelita, obszar (warstwa z projektu albo plik .gpkg), parametry,
    folder, strategia, produkty, przycisk startu, log i pasek postępu.
    """

    def __init__(self, iface, rodzic=None):
        super().__init__(rodzic)
        self.iface = iface
        self.watek = None
        self.setWindowTitle("Pobieranie Sentinel")
        self.setWindowIcon(ikona_wtyczki())
        self.resize(680, 760)
        # brandowanie: tytuły grup i pasek postępu w kolorze wiodącym
        self.setStyleSheet(
            f"QGroupBox {{ font-weight: bold; }} "
            f"QGroupBox::title {{ color: {KOLOR}; }} "
            f"QProgressBar::chunk {{ background-color: {KOLOR}; }}")
        ustawienia_qgis = QSettings()

        uklad = QVBoxLayout(self)

        # --- 1. satelita ---
        grupa1 = QGroupBox("1. Co ściągać?")
        uklad1 = QVBoxLayout(grupa1)
        self.combo_satelita = QComboBox()
        self.combo_satelita.addItem(
            "Sentinel-2 L2A — zdjęcia optyczne, poprawione (polecane)",
            "S2-L2A")
        self.combo_satelita.addItem(
            "Sentinel-2 L1C — zdjęcia optyczne, surowe", "S2-L1C")
        self.combo_satelita.addItem(
            "Sentinel-1 GRD — radar (widzi przez chmury i w nocy)",
            "S1-GRD")
        uklad1.addWidget(self.combo_satelita)
        uklad.addWidget(grupa1)

        # --- 2. obszar ---
        grupa2 = QGroupBox("2. Obszar zainteresowania")
        uklad2 = QGridLayout(grupa2)
        self.radio_warstwa = QRadioButton("Warstwa z projektu:")
        self.radio_warstwa.setChecked(True)
        self.combo_warstwa = QgsMapLayerComboBox()
        self.combo_warstwa.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.radio_plik = QRadioButton("Plik GeoPackage (.gpkg):")
        self.pole_gpkg = QLineEdit()
        przycisk_gpkg = QPushButton("Przeglądaj...")
        przycisk_gpkg.clicked.connect(self.wybierz_gpkg)
        uklad2.addWidget(self.radio_warstwa, 0, 0)
        uklad2.addWidget(self.combo_warstwa, 0, 1, 1, 2)
        uklad2.addWidget(self.radio_plik, 1, 0)
        uklad2.addWidget(self.pole_gpkg, 1, 1)
        uklad2.addWidget(przycisk_gpkg, 1, 2)
        uklad.addWidget(grupa2)

        # --- 3. parametry ---
        grupa3 = QGroupBox("3. Parametry")
        uklad3 = QGridLayout(grupa3)
        self.data_od = QDateEdit(QDate.currentDate().addYears(-1))
        self.data_od.setCalendarPopup(True)
        self.data_od.setDisplayFormat("yyyy-MM-dd")
        self.data_do = QDateEdit(QDate.currentDate())
        self.data_do.setCalendarPopup(True)
        self.data_do.setDisplayFormat("yyyy-MM-dd")
        self.spin_chmury = QSpinBox()
        self.spin_chmury.setRange(0, 100)
        self.spin_chmury.setValue(20)
        self.spin_chmury.setSuffix(" %")
        self.spin_limit = QSpinBox()
        self.spin_limit.setRange(1, 2000)
        self.spin_limit.setValue(200)
        self.spin_naraz = QSpinBox()
        self.spin_naraz.setRange(1, 4)   # serwis pozwala maks. na 4
        self.spin_naraz.setValue(3)
        self.pole_login = QLineEdit(
            ustawienia_qgis.value("pobieranie_sentinel/login", ""))
        self.pole_haslo = QLineEdit(
            ustawienia_qgis.value("pobieranie_sentinel/haslo", ""))
        self.pole_haslo.setEchoMode(QLineEdit.EchoMode.Password)
        # ptaszek "zapamiętaj" — dane trafiają do ustawień QGIS na tym
        # komputerze (hasło zapisane jawnie — nie używaj na wspólnym
        # koncie, jeśli Ci to przeszkadza!)
        self.ptaszek_pamietaj = QCheckBox(
            "Zapamiętaj login i hasło na tym komputerze")
        self.ptaszek_pamietaj.setChecked(bool(
            ustawienia_qgis.value("pobieranie_sentinel/haslo", "")))
        uklad3.addWidget(QLabel("Data OD:"), 0, 0)
        uklad3.addWidget(self.data_od, 0, 1)
        uklad3.addWidget(QLabel("Data DO:"), 0, 2)
        uklad3.addWidget(self.data_do, 0, 3)
        uklad3.addWidget(QLabel("Maks. chmury:"), 1, 0)
        uklad3.addWidget(self.spin_chmury, 1, 1)
        uklad3.addWidget(QLabel("Maks. liczba zdjęć:"), 1, 2)
        uklad3.addWidget(self.spin_limit, 1, 3)
        uklad3.addWidget(QLabel("Ile naraz (1-4):"), 2, 0)
        uklad3.addWidget(self.spin_naraz, 2, 1)
        uklad3.addWidget(QLabel("Login Copernicus:"), 3, 0)
        uklad3.addWidget(self.pole_login, 3, 1, 1, 3)
        uklad3.addWidget(QLabel("Hasło Copernicus:"), 4, 0)
        uklad3.addWidget(self.pole_haslo, 4, 1, 1, 3)
        uklad3.addWidget(self.ptaszek_pamietaj, 5, 0, 1, 4)

        # rozwijana ściągawka "Nie masz konta?" — klik pokazuje/chowa
        # instrukcję zakładania darmowego konta Copernicus
        self.przycisk_konto = QPushButton(
            "Nie masz konta? Pokaż, jak założyć ▸")
        self.przycisk_konto.setFlat(True)
        self.przycisk_konto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.przycisk_konto.setStyleSheet(
            f"color: {KOLOR}; border: none; text-align: left; "
            f"font-weight: bold;")
        self.opis_konta = QLabel(
            'Konto jest darmowe i zakłada się je w 2 minuty:<br>'
            '1. Wejdź na <a href="https://dataspace.copernicus.eu">'
            'dataspace.copernicus.eu</a> (kliknij link)<br>'
            '2. Kliknij <b>Register</b> w prawym górnym rogu<br>'
            '3. Wypełnij formularz (imię, e-mail, hasło)<br>'
            '4. Potwierdź konto linkiem z maila<br>'
            '5. Wpisz e-mail i hasło powyżej — gotowe!')
        self.opis_konta.setOpenExternalLinks(True)
        self.opis_konta.setVisible(False)
        self.przycisk_konto.clicked.connect(self.przelacz_opis_konta)
        uklad3.addWidget(self.przycisk_konto, 6, 0, 1, 4)
        uklad3.addWidget(self.opis_konta, 7, 0, 1, 4)
        uklad.addWidget(grupa3)

        # --- 4. folder ---
        grupa4 = QGroupBox("4. Folder na pobrane pliki")
        uklad4 = QHBoxLayout(grupa4)
        self.pole_folder = QLineEdit(
            ustawienia_qgis.value("pobieranie_sentinel/folder", ""))
        przycisk_folder = QPushButton("Przeglądaj...")
        przycisk_folder.clicked.connect(self.wybierz_folder)
        uklad4.addWidget(self.pole_folder)
        uklad4.addWidget(przycisk_folder)
        uklad.addWidget(grupa4)

        # --- 5. strategia ---
        grupa5 = QGroupBox("5. Które zdjęcia pobrać? (po wyszukaniu i tak "
                           "zobaczysz listę)")
        uklad5 = QVBoxLayout(grupa5)
        self.radia_strategii = QButtonGroup(self)
        for numer, (opis, wartosc) in enumerate(silnik.STRATEGIE):
            radio = QRadioButton(opis)
            radio.setProperty("wartosc", wartosc)
            if numer == 0:
                radio.setChecked(True)
            self.radia_strategii.addButton(radio)
            uklad5.addWidget(radio)
        uklad.addWidget(grupa5)

        # --- 6. produkty ---
        grupa6 = QGroupBox("6. Produkty z każdego zdjęcia "
                           "(tylko Sentinel-2)")
        uklad6 = QGridLayout(grupa6)
        self.ptaszki_produktow = {}
        for numer, (klucz, przepis) in enumerate(silnik.PRODUKTY.items()):
            ptaszek = QCheckBox(przepis["opis"])
            ptaszek.setChecked(klucz in ("RGB", "NDVI"))
            uklad6.addWidget(ptaszek, numer // 2, numer % 2)
            self.ptaszki_produktow[klucz] = ptaszek
        self.ptaszek_wczytaj = QCheckBox(
            "Po zakończeniu wczytaj gotowe produkty do QGIS")
        self.ptaszek_wczytaj.setChecked(True)  # domyślnie zaznaczony
        # lekki odstęp od ptaszków produktów, żeby napis nie ginął
        self.ptaszek_wczytaj.setStyleSheet(
            "margin-top: 12px; font-weight: bold;")
        uklad6.addWidget(self.ptaszek_wczytaj,
                         (len(silnik.PRODUKTY) + 1) // 2 + 1, 0, 1, 2)
        uklad.addWidget(grupa6)

        # radar? wyszarzamy chmury i produkty
        self.combo_satelita.currentIndexChanged.connect(
            self.przelacz_satelite)

        # --- start + postęp + log ---
        # rząd: [logo jako nieaktywny obrazek] [zwykły przycisk z tekstem]
        rzad_startu = QHBoxLayout()
        sciezka_logo = os.path.join(FOLDER_WTYCZKI, "przycisk.svg")
        if os.path.exists(sciezka_logo):
            logo = QLabel()  # QLabel = sam obrazek, nieklikalny
            logo.setPixmap(QIcon(sciezka_logo).pixmap(QSize(133, 48)))
            rzad_startu.addWidget(logo)
        self.przycisk_start = QPushButton("URUCHOM POBIERANIE")
        # kursor-rączka po najechaniu + ciemniejszy odcień (hover),
        # żeby było jasne, że to klikalny przycisk
        self.przycisk_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.przycisk_start.setStyleSheet(
            f"QPushButton {{ background-color: {KOLOR}; color: white; "
            f"font-weight: bold; padding: 10px; border-radius: 4px; }} "
            f"QPushButton:hover {{ background-color: #009ec2; }}")
        self.przycisk_start.clicked.connect(self.start)
        rzad_startu.addWidget(self.przycisk_start, 1)  # rozciągnij przycisk
        uklad.addLayout(rzad_startu)

        self.pasek = QProgressBar()
        self.pasek.setVisible(False)
        uklad.addWidget(self.pasek)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        self.log.setPlaceholderText(
            "Tu na bieżąco zobaczysz, co robi wtyczka...")
        uklad.addWidget(self.log)
        uklad.addWidget(stopka())

    # ---- drobne akcje -------------------------------------------------------
    def loguj(self, tekst):
        self.log.append(tekst)
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())

    def wybierz_gpkg(self):
        sciezka, _ = QFileDialog.getOpenFileName(
            self, "Wskaż plik GeoPackage", "",
            "GeoPackage (*.gpkg);;Wszystkie pliki (*)")
        if sciezka:
            self.pole_gpkg.setText(sciezka)
            self.radio_plik.setChecked(True)

    def wybierz_folder(self):
        sciezka = QFileDialog.getExistingDirectory(
            self, "Folder na pobrane zdjęcia")
        if sciezka:
            self.pole_folder.setText(sciezka)

    def przelacz_opis_konta(self):
        """Pokazuje/chowa instrukcję zakładania konta Copernicus."""
        widac = not self.opis_konta.isVisible()
        self.opis_konta.setVisible(widac)
        self.przycisk_konto.setText(
            "Nie masz konta? Zwiń podpowiedź ▾" if widac
            else "Nie masz konta? Pokaż, jak założyć ▸")

    def przelacz_satelite(self):
        """Sentinel-1 (radar)? Chmury i produkty nie mają sensu."""
        radar = self.combo_satelita.currentData() == "S1-GRD"
        self.spin_chmury.setDisabled(radar)
        for ptaszek in self.ptaszki_produktow.values():
            ptaszek.setDisabled(radar)

    # ---- główny przebieg ----------------------------------------------------
    def start(self):
        # zbieramy ustawienia z formularza
        if self.radio_plik.isChecked() and not self.pole_gpkg.text().strip():
            QMessageBox.warning(self, "Brakuje pliku",
                                "Wskaż plik .gpkg z obszarem (pkt 2).")
            return
        if not self.pole_folder.text().strip():
            QMessageBox.warning(self, "Brakuje folderu",
                                "Wskaż folder na pobrane pliki (pkt 4).")
            return
        if not self.pole_login.text().strip() or not self.pole_haslo.text():
            QMessageBox.warning(self, "Brakuje logowania",
                                "Wpisz login i hasło z konta "
                                "dataspace.copernicus.eu (pkt 3).")
            return

        satelita = self.combo_satelita.currentData()
        strategia = self.radia_strategii.checkedButton().property("wartosc")
        produkty = {klucz: ptaszek.isChecked()
                    for klucz, ptaszek in self.ptaszki_produktow.items()}
        if satelita == "S1-GRD":
            produkty = {klucz: False for klucz in produkty}
        if satelita == "S2-L1C":
            produkty["SCL"] = False  # SCL istnieje tylko w L2A

        ustawienia = {
            "satelita": satelita,
            "data_od": self.data_od.date().toString("yyyy-MM-dd"),
            "data_do": self.data_do.date().toString("yyyy-MM-dd"),
            "chmury": self.spin_chmury.value(),
            "limit": self.spin_limit.value(),
            "naraz": self.spin_naraz.value(),
            "login": self.pole_login.text().strip(),
            "haslo": self.pole_haslo.text(),
            "folder": self.pole_folder.text().strip(),
            "produkty": produkty,
        }

        # zapamiętywanie: folder zawsze; login i hasło tylko za zgodą
        # (odznaczenie ptaszka czyści zapamiętane dane)
        pamiec = QSettings()
        pamiec.setValue("pobieranie_sentinel/folder", ustawienia["folder"])
        if self.ptaszek_pamietaj.isChecked():
            pamiec.setValue("pobieranie_sentinel/login",
                            ustawienia["login"])
            pamiec.setValue("pobieranie_sentinel/haslo",
                            ustawienia["haslo"])
        else:
            pamiec.setValue("pobieranie_sentinel/login", "")
            pamiec.setValue("pobieranie_sentinel/haslo", "")

        try:
            # obszar -> WKT
            self.loguj("Przygotowuję obszar wyszukiwania...")
            if self.radio_warstwa.isChecked():
                warstwa = self.combo_warstwa.currentLayer()
                if warstwa is None:
                    QMessageBox.warning(self, "Brakuje warstwy",
                                        "Dodaj do projektu warstwę "
                                        "poligonową albo wybierz plik.")
                    return
                ustawienia["obszar_wkt"] = wkt_z_warstwy(warstwa)
            else:
                ustawienia["obszar_wkt"] = wkt_z_pliku(
                    self.pole_gpkg.text().strip())

            # próbne logowanie
            self.loguj("Sprawdzam logowanie do Copernicus...")
            silnik.pobierz_token(ustawienia["login"], ustawienia["haslo"])
            self.loguj("Zalogowano poprawnie.")

            # szukanie
            self.loguj("Szukam zdjęć w katalogu Copernicus...")
            sceny = silnik.wyszukaj_sceny(
                satelita, ustawienia["obszar_wkt"],
                ustawienia["data_od"], ustawienia["data_do"],
                ustawienia["chmury"], self.loguj)
        except Exception as blad:
            QMessageBox.critical(self, "OJ, coś nie wyszło", str(blad))
            return

        if not sceny:
            QMessageBox.information(
                self, "Nic nie znalazłem",
                "Brak zdjęć dla tych warunków. Zwiększ zachmurzenie "
                "albo poszerz daty.")
            return
        if len(sceny) > ustawienia["limit"]:
            self.loguj(f"Uwaga: znalazłem {len(sceny)} zdjęć, limit to "
                       f"{ustawienia['limit']} — biorę pierwsze "
                       f"{ustawienia['limit']}.")
            sceny = sceny[:ustawienia["limit"]]

        # okienko wyboru scen
        wybor = DialogWyboruScen(sceny, strategia, self)
        if (wybor.exec() != QDialog.DialogCode.Accepted
                or not wybor.wybrane):
            self.loguj("Anulowano wybór — nic nie pobieram.")
            return
        sceny = wybor.wybrane
        self.loguj(f"Wybrano do pobrania: {len(sceny)} zdjęć. Ruszamy!")

        # pobieranie w tle
        self.przycisk_start.setDisabled(True)
        self.pasek.setVisible(True)
        self.pasek.setMaximum(len(sceny))
        self.pasek.setValue(0)
        self.watek = WatekPobierania(sceny, ustawienia)
        self.watek.zaloguj.connect(self.loguj)
        self.watek.postep.connect(
            lambda gotowe, _: self.pasek.setValue(gotowe))
        self.watek.koniec.connect(self.po_zakonczeniu)
        self.watek.start()

    def po_zakonczeniu(self, udane, nieudane, raport, tify):
        """Sprzątanie po pobieraniu + opcjonalne wczytanie do QGIS."""
        self.przycisk_start.setDisabled(False)
        self.loguj(f"GOTOWE! Udane: {udane}, błędy: {nieudane}.")
        if raport:
            self.loguj(f"Raport CSV: {raport}")

        if self.ptaszek_wczytaj.isChecked() and tify:
            self.loguj(f"Wczytuję {len(tify)} produktów do QGIS...")
            for tif in tify:
                self.iface.addRasterLayer(
                    tif, os.path.basename(tif).replace(".tif", ""))

        QMessageBox.information(
            self, "Gotowe!",
            f"Skończone!\nUdane: {udane}, błędy: {nieudane}\n"
            f"Pliki i raport CSV w folderze docelowym.\n\n{AUTOR}")
