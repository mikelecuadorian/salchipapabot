#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cuadrillas del operativo y su cédula en AFlow.

Fuente ÚNICA del mapeo nombre → cédula. La usan:
  · asignar_por_zona.py    (emergencias por polígono + rotación)
  · asignar_desco_duran.py (DESCO por parroquia)
  · rotacion.py            (valida que el nombre exista antes de cargarlo)
  · el vigía (`vigia_autoasignar.py`), que importa de aquí.

⚠️ Los nombres deben coincidir EXACTAMENTE con la base de datos y con lo que
espera AFlow. "EDINSON QUIÑONEZ" con Ñ, no "Edison Quiñonez": AFlow rechaza la
asignación si el nombre no calza.
"""

CUADRILLA_CEDULA = {
    "CHRISTOPHER ORTIZ": "0921533568",
    "ORLYN CABRERA": "1207067438",
    "TOMMY SAMANIEGO": "0956072698",
    "ROBERTO BRIONES": "1203218654",
    "JOSE CARRION": "0958556813",
    "HAROLD VELOZ": "0924125057",
    "CARLOS GUARACA": "0920197241",
    "JULIO CUZCO": "0919327783",
    "DIEGO LUNA": "0923275630",
    "EDINSON QUIÑONEZ": "1206588558",
    "CARLOS PEREZ": "0926550781",
    "CARLOS MIRANDA MOLESTINA": "0928817774",
    "SERGIO CASTRO": "0705129153",
    "ACCEL CEVALLOS": "0942935149",
    "JIMMY VELIZ": "0923112445",
    "BRYAN ACOSTA": "0923465934",
    "CARLOS SAMUEL GUERRERO LAMOTA": "0916272438",
    "JORGE LOPEZ": "0924379910",
    "JOHNNY GARCIA": "0924954563",
    "JOEL ESPINAR": "0941296550",
    "CORREA ORTEGA ANA BELEN": "0926265059",
    "DARWIN INDACOCHEA": "0916893340",
    "VICENTE GONZALEZ": "0954123535",
}


def cedula(nombre: str) -> str | None:
    """Cédula de una cuadrilla, tolerante a mayúsculas y espacios."""
    if not nombre:
        return None
    n = " ".join(nombre.upper().split())
    if n in CUADRILLA_CEDULA:
        return CUADRILLA_CEDULA[n]
    for k, v in CUADRILLA_CEDULA.items():
        if k == n or (k in n) or (n in k):
            return v
    return None


if __name__ == "__main__":
    for k in sorted(CUADRILLA_CEDULA):
        print(f"  {k:34s} {CUADRILLA_CEDULA[k]}")
    print(f"\n{len(CUADRILLA_CEDULA)} cuadrillas")
