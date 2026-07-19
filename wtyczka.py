# -*- coding: utf-8 -*-
"""
Główna klasa wtyczki "Pobieranie Sentinel" — na pasku narzędzi stawia
własny przycisk: sygnet + napis "Pobieranie Sentinel". Po kliknięciu
otwiera okno główne. © Grzegorz Górniak
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QToolButton


class PobieranieSentinel:

    def __init__(self, iface):
        self.iface = iface        # "uchwyt" do QGIS
        self.akcja = None
        self.pasek = None
        self.dialog = None

    def initGui(self):
        """QGIS woła to przy starcie — dodajemy przycisk i menu."""
        # ikona = sygnet w SVG (ostry w każdym rozmiarze); PNG jako zapas
        folder = os.path.dirname(__file__)
        ikona = QIcon(os.path.join(folder, "icon.svg"))
        if ikona.isNull():
            ikona = QIcon(os.path.join(folder, "icon.png"))

        self.akcja = QAction(ikona, "Pobieranie Sentinel",
                             self.iface.mainWindow())
        self.akcja.setToolTip("Masowe pobieranie zdjęć Sentinel "
                              "z Copernicus Data Space")
        self.akcja.triggered.connect(self.uruchom)

        # własny pasek narzędzi z przyciskiem "ikona + napis":
        # zwykłe addToolBarIcon pokazuje samą ikonę, więc robimy
        # QToolButton i każemy mu pisać tekst OBOK ikony
        self.pasek = self.iface.addToolBar("Pobieranie Sentinel")
        self.pasek.setObjectName("PobieranieSentinelToolbar")
        przycisk = QToolButton()
        przycisk.setDefaultAction(self.akcja)
        przycisk.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pasek.addWidget(przycisk)

        self.iface.addPluginToMenu("&Pobieranie Sentinel", self.akcja)

    def unload(self):
        """QGIS woła to przy wyłączaniu wtyczki — sprzątamy po sobie."""
        self.iface.removePluginMenu("&Pobieranie Sentinel", self.akcja)
        if self.pasek is not None:
            self.pasek.deleteLater()   # zdejmujemy nasz pasek narzędzi
            self.pasek = None

    def uruchom(self):
        """Klik w przycisk = otwarcie okna głównego."""
        from .dialogi import DialogGlowny
        # okno trzymamy w zmiennej, żeby log i pobieranie przeżyły
        # zamknięcie i ponowne otwarcie
        if self.dialog is None:
            self.dialog = DialogGlowny(self.iface,
                                       self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
