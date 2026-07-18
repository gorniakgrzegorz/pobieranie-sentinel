# -*- coding: utf-8 -*-
"""
Punkt startowy wtyczki "Pobieranie Sentinel".
QGIS woła tę funkcję przy włączaniu wtyczki — oddajemy mu główną klasę.
© Grzegorz Górniak
"""


def classFactory(iface):
    from .wtyczka import PobieranieSentinel
    return PobieranieSentinel(iface)
