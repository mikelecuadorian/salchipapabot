#!/usr/bin/env python3
"""Vigía ACIIS — órdenes internas 80122148 / 80121602 → CARLOS MIRANDA MOLESTINA.

Caso real (2026-10-29): Miguel da números de ORDEN INTERNA sin cuenta de contrato.
Sin cuenta_contrato no se puede buscar por el camino normal (vigía de cédulas,
asignar_por_zona), así que se vigila por numero_solicitud en la BD.

Las órdenes internas SÍ tienen numero_tramite en la BD (ej. 80121582 -> 345456545),
por eso en cuanto AReports las cargue las detectamos y se pueden asignar por trámite.

- Silencio mientras no aparezcan -> patrón watchdog, no molesta.
- UN aviso por orden, sin reavisos (estado en .vigia_ordenes_internas_estado.json).
- Detección honesta: si a las 24h el numero_tramite sigue NULL, avisa igual
  marcándolo como "sin trámite" para que no quede mudo para siempre.
- Probar sin efectos: VIGIA_ESTADO=/tmp/x.json python3 vigia_ordenes_internas.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# Blindaje: el módulo de auto-asignación vive junto a este script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
ESTADO = os.environ.get(
    "VIGIA_ESTADO", os.path.expanduser("~/.hermes/scripts/.vigia_ordenes_internas_estado.json")
)

# numero_solicitud (ORDEN INTERNA) -> destino
ORDENES = {
    "80122148": "CARLOS MIRANDA MOLESTINA",
    "80121602": "CARLOS MIRANDA MOLESTINA",
    # 2026-11-06 · pedido de Miguel: si aparece esta orden interna, asignar a
    # la cuadrilla CARLOS PEREZ. Verificado al agregarla: 0 registros en
    # recorrido_cuadrillas y 0 en gestion_tramites → nada que sembrar en el
    # estado; el primer aviso será la orden nueva que cargue AReports.
    # Nombre EXACTO verificado en la BD: 'CARLOS PEREZ' (sin segundo apellido).
    "80122106": "CARLOS PEREZ",
}

# Órdenes que además se AUTO-ASIGNAN en AFlow al detectarlas (no solo avisan).
# Requiere que el destino sea una cuadrilla válida del directorio de
# vigia_autoasignar.py. Si la asignación falla, el módulo reintenta.
AUTO_ASIGNAR_ORDENES = {
    "80122106": "CARLOS PEREZ",
}

# Aviso de "sigue sin trámite" a las N horas de estar detectada
HORAS_ESPERA_TRAMITE = 24


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


def buscar(conn, orden):
    """Busca la orden interna en las dos tablas. Prefiere la fila con trámite."""
    filas = []
    for tabla, extra in (
        ("recorrido_cuadrillas", "motivo_solicitud, parroquia, accion_ejecutada, fecha_sincronizacion"),
        ("gestion_tramites", "tipo_solicitud, parroquia, estado, fecha_sincronizacion"),
    ):
        try:
            cur = conn.execute(
                f"SELECT numero_tramite, cuadrilla, {extra} FROM {tabla}"
                " WHERE numero_solicitud=? ORDER BY fecha_sincronizacion DESC LIMIT 1",
                (orden,),
            )
            r = cur.fetchone()
            if r:
                filas.append((tabla, r))
        except Exception:
            continue
    if not filas:
        return None
    # preferir la que ya tiene numero_tramite
    con_tram = [f for f in filas if f[1][0]]
    return (con_tram or filas)[0]


def main():
    if not os.path.exists(DB):
        print(f"⚠️ Vigía órdenes internas: no encuentro la BD ({DB})")
        return 1

    estado = cargar_estado()
    pendientes = [o for o in ORDENES if estado.get(o, {}).get("aviso") != "enviado"]
    if not pendientes:
        return 0  # nada que vigilar -> silencio

    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
    except Exception as e:
        print(f"⚠️ Vigía órdenes internas: no pude abrir la BD ({e})")
        return 1

    avisos, listas = [], []
    try:
        for orden in pendientes:
            destino = ORDENES[orden]
            hit = buscar(conn, orden)
            st = estado.setdefault(orden, {})
            st.setdefault("detectada_en", None)

            if not hit:
                continue  # todavía no baja a la BD -> silencio

            if not st["detectada_en"]:
                st["detectada_en"] = datetime.now().isoformat(timespec="seconds")

            tabla, fila = hit
            numero_tramite, cuadrilla = fila[0], fila[1]
            motivo, parroquia = fila[2], fila[3]

            if numero_tramite:
                # ── AUTO-ASIGNACIÓN en AFlow (solo órdenes de AUTO_ASIGNAR_ORDENES) ──
                # Si falla, NO se marca 'aviso=enviado' → se reintenta en la
                # próxima corrida (regla 1 del módulo).
                destino_auto = AUTO_ASIGNAR_ORDENES.get(orden)
                if destino_auto and not st.get("autoasignado"):
                    try:
                        import vigia_autoasignar as VA
                        cedula = VA.CUADRILLA_CEDULA.get(destino_auto.upper())
                        if not cedula:
                            st["autoasignado"] = "no-cuadrilla"
                            st["autoasignado_det"] = f"cuadrilla '{destino_auto}' no está en el directorio"
                        else:
                            ok, det = VA.asignar_en_aflow(numero_tramite, cedula)
                            if ok:
                                st["autoasignado"] = "ok"
                                st["autoasignado_det"] = det
                            else:
                                st["autoasignado"] = "fallo"
                                st["autoasignado_det"] = det
                    except Exception as e:  # noqa: BLE001
                        st["autoasignado"] = "fallo"
                        st["autoasignado_det"] = f"error interno: {e}"

                st["aviso"] = "enviado"
                st["numero_tramite"] = numero_tramite
                linea = (
                    f"- **{orden}** → trámite `{numero_tramite}`\n"
                    f"   parroquia: {parroquia} · motivo: {motivo}\n"
                    f"   cuadrilla actual: {cuadrilla or 'sin asignar'}\n"
                    f"   destino: **{destino}**"
                )
                auto = st.get("autoasignado")
                if auto == "ok":
                    linea += f"\n   ✅ **Asignada sola en AFlow** ({st.get('autoasignado_det','ok')})"
                elif auto == "fallo":
                    linea += ("\n   ⚠️ **Auto-asignación falló** "
                              f"({st.get('autoasignado_det','?')}) — reintento en la próxima corrida")
                    st["aviso"] = "pendiente"  # reintentar
                elif auto == "no-cuadrilla":
                    linea += f"\n   ⚠️ No pude asignar: {st.get('autoasignado_det','?')} — hazlo a mano en AFlow"
                elif auto is None:
                    linea += " (falta asignar en AFlow)"
                avisos.append(linea)
            else:
                # sin trámite todavía: avisar una sola vez pasado el plazo
                t0 = datetime.fromisoformat(st["detectada_en"])
                if datetime.now() - t0 >= timedelta(hours=HORAS_ESPERA_TRAMITE):
                    st["aviso"] = "enviado"
                    avisos.append(
                        f"- **{orden}** → apareció en BD pero **sin número de trámite** "
                        f"(+{HORAS_ESPERA_TRAMITE}h)\n"
                        f"   parroquia: {parroquia} · motivo: {motivo}\n"
                        f"   destino: **{destino}** — no se puede asignar por trámite todavía"
                    )
                else:
                    listas.append(orden)
    finally:
        conn.close()
        guardar_estado(estado)

    if not avisos:
        return 0  # silencio (o esperando el plazo de trámite)

    print(f"📋 **ACIIS — orden(es) interna(s) ya en BD** ({len(avisos)})")
    print("\n".join(avisos))
    if listas:
        print(f"\n_En BD sin trámite aún, reintento en la próxima corrida: {', '.join(listas)}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
