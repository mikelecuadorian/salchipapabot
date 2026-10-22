#!/usr/bin/env python3
"""Vigía ACIIS para órdenes SAP puntuales de Miguel.

Avisa UNA SOLA VEZ por orden cuando esa orden SAP ya "refleja" en ACIIS,
es decir cuando aparece en la BD (gestion_tramites o recorrido_cuadrillas).

- Silencio (stdout vacío) mientras nada nuevo -> patrón watchdog, no molesta.
- Un aviso por orden, sin reavisos: el estado queda en .vigia_ordenes_aciis_estado.json
- Se puede probar sin efectos: VIGIA_ESTADO=/tmp/x.json python3 vigia_ordenes_aciis.py
"""

import json
import os
import sqlite3
import sys

DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
ESTADO = os.environ.get(
    "VIGIA_ESTADO", os.path.expanduser("~/.hermes/scripts/.vigia_ordenes_aciis_estado.json")
)

# Orden SAP -> descripción para el aviso
ORDENES = {
    "42848198": "000042848198 · Instalar medidor · Samborondón",
    "42847925": "000042847925 · Instalar medidor · Samborondón",
}

# Permite probar con otras órdenes sin tocar el archivo: VIGIA_ORDENES=38545078
_override = os.environ.get("VIGIA_ORDENES", "").strip()
if _override:
    ORDENES = {o.strip(): "(prueba)" for o in _override.split(",") if o.strip()}


def cargar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_estado(d):
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def consultar(conn, tabla, orden):
    """Devuelve la fila del trámite si la orden ya refleja en esa tabla."""
    cols = {
        "gestion_tramites": "numero_tramite, estado, cuadrilla, fecha_solicitud, fecha_ejecucion, cliente",
        "recorrido_cuadrillas": "numero_tramite, estado_insp, cuadrilla, fecha_solicitud, fecha_ejecucion, cliente",
    }[tabla]
    try:
        cur = conn.execute(
            f"SELECT {cols} FROM {tabla} WHERE numero_solicitud=?"
            " ORDER BY fecha_solicitud DESC LIMIT 1",
            (orden,),
        )
        return cur.fetchone()
    except Exception:
        return None


def main():
    if not os.path.exists(DB):
        print(f"⚠️ Vigía ACIIS: no encuentro la BD ({DB})", file=sys.stdout)
        return 1

    estado = cargar_estado()
    pendientes = [o for o in ORDENES if estado.get(o) not in ("refleja",)]
    if not pendientes:
        return 0  # nada que vigilar -> silencio

    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    except Exception as e:
        print(f"⚠️ Vigía ACIIS: no pude abrir la BD ({e})")
        return 1

    avisos = []
    try:
        for orden in pendientes:
            fila = None
            origen = ""
            for tabla in ("gestion_tramites", "recorrido_cuadrillas"):
                fila = consultar(conn, tabla, orden)
                if fila:
                    origen = tabla
                    break
            if not fila:
                continue
            numero_tramite, estado_tram, cuadrilla, f_sol, f_ejec, cliente = fila
            estado[orden] = "refleja"
            detalle = " · ".join(
                str(x) for x in [
                    f"trámite {numero_tramite}",
                    f"estado {estado_tram}",
                    f"cuadrilla {cuadrilla}" if cuadrilla else None,
                    f"ejec. {f_ejec}" if f_ejec else None,
                ] if x
            )
            avisos.append(f"- **{orden}** → {detalle}  _({origen})_")
    finally:
        conn.close()
        guardar_estado(estado)

    if not avisos:
        return 0  # silencio

    print("✅ **ACIIS — ya refleja**  " + f"({len(avisos)} orden(es))")
    print("\n".join(avisos))
    print("_Vigía de órdenes SAP → lo dejé revisando las demás._")
    return 0


if __name__ == "__main__":
    sys.exit(main())
