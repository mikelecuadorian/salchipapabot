#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vigía silencioso de las cédulas del operativo (2 etapas, estado POR TRÁMITE).

Caso: operativo de campo cuyos trámites CNEL debía crear en ACIIS. Miguel quiere
enterarse el día en que los creen y, además, cuando pasen a ejecutados (para no
volver a preguntar a mano).

ETAPAS VIGILADAS
  Etapa 1 — CREACIÓN  : el trámite ya aparece (en `recorrido_cuadrillas`
                        y/o en `gestion_tramites`).
  Etapa 2 — EJECUCIÓN : el trámite ya fue gestionado (`gestion_tramites`) o
                        figura con `accion_ejecutada = SI` en el recorrido.

QUÉ SE PUEDE VIGILAR (2026-09-30)
  - `CEDULAS`          : lista de cédulas (buscador por identificación).
  - `CUENTAS_CONTRATO` : dict cuenta -> RECORDATORIO. Vigila los trámites de
    esa cuenta y, cuando aparece uno NUEVO, el aviso sale con el recordatorio
    (p.ej. "planificar y asignar a la cuadrilla CARLOS MIRANDA"). Sirve para no
    perderse el paso siguiente de una cédula: en el operativo CNEL, después de
    la Factibilidad viene la INSTALACIÓN, que es un trámite DISTINTO y puede
    llegar semanas después.

ESTADO POR TRÁMITE (cambio del 2026-09-28)
  Antes el estado era por CÉDULA, y eso tenía un hueco real: una cédula con
  VARIOS trámites (p.ej. factibilidad ejecutada + instalación pendiente) quedaba
  marcada como "ya ejecutada" y el vigía **nunca avisaba** de los trámites
  siguientes. Ahora se guarda el número de trámite:
      "tramites_avisados"    -> trámites cuya CREACIÓN ya se anunció
      "tramites_ejecutados"  -> trámites cuya EJECUCIÓN ya se anunció
  Así cada trámite avisa UNA vez por etapa, aunque compartan cédula.

Patrón "watchdog silencioso":
  - Nada nuevo  -> no imprime NADA (stdout vacío = el cron no dice nada).
  - Novedad     -> imprime SOLO los trámites nuevos de esa etapa.
  - Si la BD no se puede leer -> imprime error y sale con código != 0
    (un vigía roto no puede quedarse callado).

Uso:
  python3 vigia_cedulas_operativo.py            # corrida normal (silencio)
  python3 vigia_cedulas_operativo.py --probar   # enseña el formato usando
                                                # cédulas que SÍ existen, sin
                                                # guardar estado ni alterar nada
  VIGIA_ESTADO=/tmp/x.json python3 vigia_cedulas_operativo.py   # otro estado

AUTO-ASIGNACIÓN (2026-10-15)
  Además de avisar, el vigía puede **planificar/asignar solo** los trámites nuevos
  en AFlow para las cuentas/cédulas que estén en el mapa `AUTO_ASIGNAR` del módulo
  `vigia_autoasignar.py`. Reglas:
    · solo actúa sobre lo que esté en ese mapa (derivado de los recordatorios:
      texto con "asignar"/"planificar"/"pasar" + cuadrilla válida);
    · sin cuadrilla → PLANIFICA; con OTRA cuadrilla → REASIGNA a la del vigía;
      con la cuadrilla correcta → no hace nada;
    · NUNCA toca un trámite ya ejecutado/liberado (no se falsea el historial);
    · verifica después y avisa del resultado (éxito o fallo; el fallo reintenta);
    · idempotente: guarda "tramites_autoasignados" en el estado;
    · en `--probar` NO ejecuta AFlow (dry-run).
  Para cambiar qué se auto-asigna: editar el RECORDATORIO del vigía.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

# Blindaje: el módulo de auto-asignación vive junto a este script. Aseguramos que
# su directorio esté en sys.path aunque el cron lo ejecute desde otro cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _cargar_env():
    """Carga las credenciales de ACIIS desde ~/.hermes/.env si no están en el entorno.

    Necesario porque este vigía corre como cron `no_agent`: el entorno del cron NO
    trae ACIIS_USER/ACIIS_PASS (los exporta `ejecutar_aciis_cron.sh`, que aquí no se
    ejecuta). Sin ellas, `tools/aflow_buscar_tramite.py` hace login con contraseña
    vacía, el portal se queda en la pantalla de login y el script muere con
    "iframe del portal no apareció tras 30s" (exit 5) — un error que apunta al
    síntoma y no a la causa. Los hijos heredan el entorno con env=dict(os.environ).
    """
    if os.environ.get("ACIIS_PASS"):
        return
    for ruta in (os.path.expanduser("~/.hermes/.env"), "/root/.hermes/.env"):
        if not os.path.exists(ruta):
            continue
        try:
            with open(ruta, encoding="utf-8", errors="ignore") as fh:
                for linea in fh:
                    linea = linea.strip()
                    if not linea or linea.startswith("#") or "=" not in linea:
                        continue
                    k, _, v = linea.partition("=")
                    k = k.strip()
                    if k in ("ACIIS_USER", "ACIIS_PASS") and v.strip():
                        os.environ.setdefault(k, v.strip().strip('"').strip("'"))
        except OSError:
            continue
        if os.environ.get("ACIIS_PASS"):
            break


_cargar_env()

DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
ESTADO = os.environ.get(
    "VIGIA_ESTADO", "/root/.hermes/state/vigia_cedulas_operativo.json"
)

# ── Cédulas a vigilar (editar aquí) ─────────────────────────────────────────
CEDULAS = [
    # Operativo CNEL (trámites creados 25 y 28-sep)
    "0907203749",
    "0910306711",
    "0926728676",
    "0908575889",
    # Caso aparte (2026-09-28): factibilidad YA ejecutada el 21-sep;
    # se vigila la INSTALACIÓN del medidor (trámite 343740383).
    "905467999",
    # 2026-10-05 · pedido de Miguel: si sale un trámite con esta cédula,
    # planificarlo y asignarlo a la cuadrilla JIMMY VELIZ.
    # (El texto del recordatorio está en RECORDATORIOS_CEDULA, más abajo.)
    "0904279882",
    # 2026-10-06 · pedido de Miguel: avisar cuando salgan trámites de estas 3
    # cédulas para planificar y asignar a la cuadrilla CARLOS MIRANDA MOLESTINA.
    # Verificado al agregarlas contra ACIIS (06-oct-2026):
    #   0922717913 → 0 trámites (limpia)
    #   0929952190 → 0 trámites (limpia)
    #   0917124844 → 1 trámite VIEJO ya EJECUTADO (317010651, 11-sep-2025,
    #                cuadrilla TOMMY SAMANIEGO) → se sembró en el estado para
    #                que NO salga aviso falso por ese trámite de 2025.
    "0922717913",
    "0929952190",
    "0917124844",
    # NOTA 2026-10-05: la cédula 0944035260 (XIOMARA TERESA CHAFLA PALMA) se pasó
    # a vigilar por sus 2 CUENTAS de contrato (201020602623 y 201020602615), no
    # por cédula. Ver CUENTAS_CONTRATO.
]
# Cédula que existe de verdad (con trámites ejecutados), para probar el formato
CEDULAS_PRUEBA = ["0100008887"]

# ── Cuentas de contrato a vigilar (editar aquí) ─────────────────────────────
# Mismo vigía, mismo horario, sin cron nuevo: además de cédulas, se puede
# vigilar una CUENTA de contrato. Cuando aparece un trámite NUEVO de esa cuenta
# (p.ej. la instalación que CNEL todavía no ha creado) sale el aviso con el
# RECORDATORIO que se escriba aquí al lado.
# Formato: {"<cuenta_contrato>": "<recordatorio para Miguel>"}
CUENTAS_CONTRATO = {
    # 2026-09-30 · CARLOS ALBERTO MORALES VERA (céd. 953022514)
    # Factibilidad `343501320` ya APROBADA y ejecutada el 22-sep por CARLOS
    # MIRANDA MOLESTINA ("todo OK, listo para instalar medidor bornera").
    # Se vigila la INSTALACIÓN para planificarla y dársela a esa cuadrilla.
    "201020517334": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-09-30 · pedido de Miguel: avisar cuando salga un trámite para pasarlas
    # a la unidad de CARLOS PEREZ. Al agregarlas NO tenían trámites en ACIIS
    # (nada que sembrar en el estado: el primer aviso será el trámite nuevo).
    "200045157308": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    "201020394890": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    # 2026-09-30 · pedido de Miguel: avisar cuando salga un trámite para pasarlo
    # a la cuadrilla CARLOS PEREZ. Al agregarla NO tenía trámites (todo limpio).
    "201020577734": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    # 2026-09-30 · pedido de Miguel: si sale un trámite, recordarle LLAMAR A
    # RODRIGUEZ para saber qué hacer. Al agregarla NO tenía trámites.
    "201016937611": "Llamar a RODRIGUEZ para saber qué hacer con este trámite",
    # 2026-09-30 · cuenta de FRANCISCA ALEXANDRA QUIONEZ GRANT (céd. 0910306711,
    # ya vigilada por cédula). Su factibilidad `343857485` se ejecutó el 28-sep y
    # falta la INSTALACIÓN → Miguel pidió recordarle planificarla a JULIO CUZCO.
    # OJO: al estar también la cédula en CEDULAS, esta cuenta haría aviso DOBLE;
    # el bucle de cuentas va primero y marca el trámite en `vistos_*` para que el
    # de cédulas lo salte (arreglo 2026-09-30).
    "201020615310": "Planificar la instalación a la cuadrilla JULIO CUZCO",
    # 2026-10-02 · pedido de Miguel: si sale un trámite en esta cuenta,
    # planificarlo para la cuadrilla ROBERTO BRIONES. Verificado al agregarla:
    # NO tenía trámites en ACIIS (0 en recorrido_cuadrillas y 0 en
    # gestion_tramites) → nada que sembrar en el estado; el primer aviso será
    # el trámite nuevo que cree CNEL.
    "201020655613": "Planificar y ASIGNAR a la cuadrilla ROBERTO BRIONES",
    # 2026-10-05 · pedido de Miguel: si sale un trámite en esta cuenta,
    # planificarlo para la cuadrilla CARLOS PEREZ. Verificado al agregarla:
    # NO tenía trámites en ACIIS (0 en recorrido_cuadrillas, 0 en
    # gestion_tramites y 0 en el barrido de TODAS las tablas) → nada que
    # sembrar en el estado; el primer aviso será el trámite nuevo que cree CNEL.
    "201020688986": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    # 2026-10-05 · pedido de Miguel: vigilar por CUENTA (no por cédula) las 2
    # cuentas de XIOMARA TERESA CHAFLA PALMA (céd. 0944035260) y recordar
    # planificar/asignar a JIMMY VELIZ. Ya tenían trámites → el estado se sembró
    # con los existentes (343857489, 343857498, 344141599) para no dar aviso falso.
    "201020602623": "Planificar y ASIGNAR a la cuadrilla JIMMY VELIZ",
    "201020602615": "Planificar y ASIGNAR a la cuadrilla JIMMY VELIZ",
    # 2026-10-05 · pedido de Miguel: si sale un trámite en esta cuenta, planificar
    # y asignar a la cuadrilla CARLOS MIRANDA MOLESTINA (nombre EXACTO en la BD;
    # Miguel lo pidió como "Carlos Miranda"). Verificado al agregarla: NO tenía
    # trámites en ACIIS (0 en recorrido_cuadrillas, 0 en gestion_tramites y 0 en
    # el barrido de TODA la BD) → nada que sembrar; el primer aviso será el nuevo.
    "201020550368": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-10-15 · pedido de Miguel: si sale un trámite NUEVO en esta cuenta,
    # recordarle planificarlo y asignarlo a la cuadrilla ROBERTO BRIONES.
    # Verificado al agregarla: YA tenía el trámite `344535763` (cliente MANUEL DE
    # JESÚS VIVANCO VALLEJO), ya EJECUTADO (accion_ejecutada=SI) y ya asignado a
    # ROBERTO BRIONES → se sembró en el estado (avisados + ejecutados) para que
    # NO dispare aviso falso; el próximo aviso será por un trámite nuevo.
    "200047690488": "Recordar: planificar y ASIGNAR a la cuadrilla ROBERTO BRIONES",
    # 2026-10-15 · pedido de Miguel: si sale un trámite en esta cuenta,
    # recordar planificar y asignar a la cuadrilla CARLOS MIRANDA MOLESTINA.
    # Verificado al agregarla: cuenta `200045568801` (cliente CHRISTIAN FREDDY
    # OCHOA FRANCO). YA tenía 2 trámites (268287920, 270044810), ambos EJECUTADOS
    # (8 - Liberados) → se sembraron en el estado para que NO dispare aviso falso;
    # el próximo aviso será por un trámite nuevo que cree CNEL.
    "200045568801": "Recordar: planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-10-15 · pedido de Miguel: mismo recordatorio que la cuenta anterior.
    # Verificado al agregarla: cuenta `201020732669` (12 díg.) NO tenía trámites en
    # ACIIS (0 en recorrido_cuadrillas y 0 en gestion_tramites) → nada que sembrar;
    # el primer aviso será el trámite nuevo que cree CNEL.
    "201020732669": "Recordar: planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-10-16 · pedido de Miguel: si sale un trámite en esta cuenta, planificar
    # y asignar a la cuadrilla CARLOS PEREZ (nombre EXACTO en la BD, verificado).
    # Verificado al agregarla: NO tenía trámites en ACIIS (0 en recorrido_cuadrillas
    # y 0 en gestion_tramites) → nada que sembrar; el primer aviso será el nuevo.
    "201020780676": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    # 2026-10-16 · pedido de Miguel: si sale un trámite en esta cuenta, planificar
    # y asignar a la cuadrilla CARLOS MIRANDA MOLESTINA (nombre EXACTO en la BD;
    # Miguel lo pidió como "Carlos Miranda"). Verificado al agregarla: NO tenía
    # trámites ni registros SAP (0 en recorrido_cuadrillas, gestion_tramites,
    # sap_medidores y sap_materiales) → nada que sembrar; el primer aviso será el nuevo.
    "201020498865": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-10-20 · pedido de Miguel: si sale un trámite en esta cuenta, planificar
    # y asignar a la cuadrilla CARLOS MIRANDA MOLESTINA (nombre EXACTO en la BD;
    # Miguel lo pidió como "Carlos Miranda"). Verificado al agregarla: NO tenía
    # trámites (0 en recorrido_cuadrillas y 0 en gestion_tramites) → nada que
    # sembrar; el primer aviso será el trámite nuevo que cree CNEL.
    "201020858415": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    # 2026-10-21 · pedido de Miguel: cuenta nueva → planificar y asignar a
    # CARLOS PEREZ (nombre EXACTO en la BD, verificado). Verificado al agregarla:
    # 0 registros en recorrido_cuadrillas y gestion_tramites → nada que sembrar;
    # el primer aviso será el trámite nuevo que cree CNEL.
    # (201020780676 ya estaba vigilada desde 2026-10-16 con el mismo recordatorio.)
    "201020840256": "Planificar y ASIGNAR a la cuadrilla CARLOS PEREZ",
    # 2026-10-22 · pedido de Miguel: cuenta nueva → planificar y asignar a la
    # cuadrilla ROBERTO BRIONES (nombre EXACTO en CUADRILLA_CEDULA, céd.
    # 1203218654). Verificado al agregarla: 0 registros en recorrido_cuadrillas
    # y 0 en gestion_tramites → nada que sembrar en el estado; el primer aviso
    # será el trámite nuevo que cree CNEL.
    "201020864413": "Planificar y ASIGNAR a la cuadrilla ROBERTO BRIONES",
    # 2026-10-22 · pedido de Miguel: dos cuentas nuevas → planificar y asignar a
    # la cuadrilla JIMMY VELIZ (nombre EXACTO en CUADRILLA_CEDULA, céd.
    # 0923112445).
    #   201020690834 (FLOR MARIA TORRES TORRES) YA tenía 2 trámites, ambos
    #     EJECUTADOS por TOMMY SAMANIEGO: `344213862` (factibilidad) y
    #     `344405345` (instalar medidor). Se sembraron en el estado (avisados +
    #     ejecutados) para que NO dispare aviso falso: el próximo aviso será
    #     por un trámite NUEVO.
    #   201020805853 → 0 en recorrido_cuadrillas y 0 en gestion_tramites.
    #     Nada que sembrar; el primer aviso será el trámite que cree CNEL.
    "201020690834": "Planificar y ASIGNAR a la cuadrilla JIMMY VELIZ",
    "201020805853": "Planificar y ASIGNAR a la cuadrilla JIMMY VELIZ",
    # 2026-10-22 · pedido de Miguel: si sale un trámite → planificar y asignar a
    # la cuadrilla BRYAN ACOSTA (nombre EXACTO en CUADRILLA_CEDULA, céd.
    # 0923465934).
    #   Ya tenía 1 trámite EJECUTADO por ORLYN CABRERA: `326952810`
    #   (46 - Reclamo consu. elevados, cliente ERICK DAVID VILLAO RUIZ,
    #   accion_ejecutada=SI) → se sembró en el estado (avisados + ejecutados)
    #   para que NO dispare aviso falso; el próximo aviso será por un trámite
    #   NUEVO. OJO: es un RECLAMO, no una instalación — Bryan Acosta no tiene
    #   nada que ver con ese historial.
    "201015467487": "Planificar y ASIGNAR a la cuadrilla BRYAN ACOSTA",
}

# RECORDATORIO por CÉDULA (mismo patrón que CUENTAS_CONTRATO). Cuando aparece un
# trámite NUEVO de esa cédula, el aviso sale con cabecera "CÉDULA VIGILADA" + el
# recordatorio. Formato: {"<cedula>": "<recordatorio para Miguel>"}
RECORDATORIOS_CEDULA = {
    # 2026-10-05 · pedido de Miguel: si sale un trámite con esta cédula,
    # planificarlo y asignarlo a la cuadrilla JIMMY VELIZ. Verificado al
    # agregarla: NO tenía trámites en ACIIS (0 en recorrido_cuadrillas y 0 en
    # gestion_tramites) → nada que sembrar en el estado; el primer aviso será
    # el trámite nuevo que cree CNEL.
    "0904279882": "Planificar y ASIGNAR a la cuadrilla JIMMY VELIZ",
    # 2026-10-06 · pedido de Miguel: si sale un trámite con estas 3 cédulas,
    # planificarlo y asignarlo a la cuadrilla CARLOS MIRANDA MOLESTINA (nombre
    # EXACTO en la BD; Miguel lo pidió como "Carlos Miranda").
    "0922717913": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    "0929952190": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
    "0917124844": "Planificar y ASIGNAR a la cuadrilla CARLOS MIRANDA MOLESTINA",
}

NORM = "TRIM(REPLACE(CAST(identificacion_cliente AS TEXT),'.0',''))"
VALORES_SI = {"SI", "SÍ", "S", "1", "TRUE", "YES"}


def es_si(v):
    return str(v or "").strip().upper() in VALORES_SI


def variantes(ced):
    """ACIIS guarda la identificación de 3 formas distintas:
       - cédula con cero inicial   → "0917124844"
       - cédula SIN cero inicial   → "917124844"   (la importa como número)
       - RUC de la persona         → "917124844001" (cédula + "001")
    Hay que probar las 3, si no un trámite se pierde en silencio.

    ⚠️ Pitfall real (2026-10-06): el trámite `344274832` (MARIA MERCEDES TORRES
    VILLANUEVA) NO se avisó porque ACIIS lo guardó como `917124844001` (RUC) y
    el vigía solo probaba `0917124844` / `917124844`. Miguel lo notó.
    """
    c = ced.strip()
    base = {c, c.lstrip("0")}
    if not c.startswith("0"):
        base.add("0" + c)
    # RUC: la misma cédula con sufijo "001" (con y sin cero inicial)
    con_ruc = base | {x + "001" for x in base}
    return sorted(x for x in con_ruc if x)


def conectar():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    return con


COLS_RC = """numero_tramite, numero_solicitud, cliente, identificacion_cliente AS ced,
            cuadrilla, tipo_solicitud, motivo_solicitud, estado_insp, fecha_analisis,
            fecha_planificacion, fecha_ejecucion, accion_ejecutada,
            cuenta_contrato, codigo_cliente, direccion, med_numero, se_posterga"""
COLS_GT = """numero_tramite, numero_solicitud, cliente, identificacion_cliente AS ced,
            cuadrilla, tipo_solicitud, fecha_ejecucion, cuenta_contrato,
            estado, se_posterga, motivo_no_ejecucion, detalle_no_ejecucion,
            observacion_gestion"""


def _buscar(con, where_rc, where_gt, params):
    """Devuelve (asignaciones, gestiones) aplicando los WHERE que le pasen."""
    cur = con.cursor()
    asign = cur.execute(
        f"SELECT {COLS_RC} FROM recorrido_cuadrillas WHERE {where_rc} "
        f"ORDER BY fecha_ejecucion", params).fetchall()

    ejecut = cur.execute(
        f"SELECT {COLS_GT} FROM gestion_tramites WHERE {where_gt} "
        f"ORDER BY fecha_ejecucion", params).fetchall()
    return asign, ejecut


def buscar(con, ced):
    """Devuelve (asignaciones, gestiones) para una cédula."""
    vs = variantes(ced)
    ph = ", ".join("?" * len(vs))
    return _buscar(con, f"{NORM} IN ({ph})", f"{NORM} IN ({ph})", vs)


def buscar_cuenta(con, cuenta):
    """Devuelve (asignaciones, gestiones) para una CUENTA de contrato.

    Se compara como texto y por coincidencia parcial: la cuenta puede venir con
    sufijo (`.0`) o con ceros/comillas raras según el reporte ACIIS.
    """
    pat = f"%{str(cuenta).strip()}%"
    where = "CAST(cuenta_contrato AS TEXT) LIKE ?"
    return _buscar(con, where, where, (pat,))


def novedades(asign, ejecut, tramites_avisados, tramites_ejecutados):
    """Recorta a lo NUEVO de cada etapa. Devuelve dict o None si no hay nada."""
    t_asign = {str(r["numero_tramite"]) for r in asign}
    t_ges = {str(r["numero_tramite"]) for r in ejecut}
    # trámites que ya se gestionaron en campo (accion_ejecutada = SI)
    t_ejec = set(t_ges) | {str(r["numero_tramite"]) for r in asign if es_si(r["accion_ejecutada"])}

    cre_asign = [r for r in asign if str(r["numero_tramite"]) not in tramites_avisados]
    cre_solo_ges = [r for r in ejecut
                    if str(r["numero_tramite"]) not in tramites_avisados
                    and str(r["numero_tramite"]) not in t_asign]

    eje_ges = [r for r in ejecut if str(r["numero_tramite"]) not in tramites_ejecutados]
    eje_solo_rc = [r for r in asign
                   if es_si(r["accion_ejecutada"])
                   and str(r["numero_tramite"]) not in tramites_ejecutados
                   and str(r["numero_tramite"]) not in t_ges]

    if not (cre_asign or cre_solo_ges or eje_ges or eje_solo_rc):
        return None

    return {
        "cre_asign": cre_asign,
        "cre_solo_ges": cre_solo_ges,
        "eje_ges": eje_ges,
        "eje_solo_rc": eje_solo_rc,
        "nuevos_creados": sorted(({str(r["numero_tramite"]) for r in cre_asign}
                                  | {str(r["numero_tramite"]) for r in cre_solo_ges})),
        "nuevos_ejecutados": sorted(({str(r["numero_tramite"]) for r in eje_ges}
                                     | {str(r["numero_tramite"]) for r in eje_solo_rc})),
        "todos_ejecutados": t_ejec,
    }


# ── Mensajes ────────────────────────────────────────────────────────────────

def encabezado_cuenta(cuenta, recordatorio):
    """Cabecera extra cuando el aviso sale por vigilar una CUENTA de contrato."""
    return (f"🔔 *CUENTA VIGILADA* — `{cuenta}`\n"
            f"⏰ *RECORDATORIO:* {recordatorio}")


def encabezado_cedula(ced, recordatorio):
    """Cabecera extra cuando el aviso sale por vigilar una CÉDULA con recordatorio."""
    return (f"🪪 *CÉDULA VIGILADA* — `{ced}`\n"
            f"⏰ *RECORDATORIO:* {recordatorio}")


def bloque_creacion(ref, nov):
    out = [f"🔔 *CNEL ya creó el trámite* — {ref}", ""]
    out.append(f"📋 *CREADO / EN RECORRIDO* ({len(nov['cre_asign']) + len(nov['cre_solo_ges'])} nuevo(s))")
    for r in nov["cre_asign"]:
        listo = es_si(r["accion_ejecutada"]) or str(r["numero_tramite"]) in nov["todos_ejecutados"]
        marca = "✅ gestionado" if listo else "⏳ pendiente de campo"
        out.append(
            f"  • Trámite `{r['numero_tramite']}` · solicitud `{r['numero_solicitud']}` — {marca}\n"
            f"    Cliente: {r['cliente']} (céd. guardada `{r['ced']}`)\n"
            f"    Tipo: {r['tipo_solicitud'] or '—'} · motivo: {r['motivo_solicitud'] or '—'}\n"
            f"    Cuadrilla: {r['cuadrilla'] or '—'}\n"
            f"    Analizado: {r['fecha_analisis'] or '—'} · planificado: {r['fecha_planificacion'] or '—'}\n"
            f"    Ejecutado: {r['fecha_ejecucion'] or '—'} · acción ejecutada: {r['accion_ejecutada'] or '—'}\n"
            f"    Cuenta: {r['cuenta_contrato'] or '—'} · medidor: {r['med_numero'] or '—'}\n"
            f"    Dirección: {r['direccion'] or '—'}")
    for r in nov["cre_solo_ges"]:
        out.append(
            f"  • Trámite `{r['numero_tramite']}` · solicitud `{r['numero_solicitud']}`\n"
            f"    Cliente: {r['cliente']} · tipo: {r['tipo_solicitud'] or '—'}\n"
            f"    Cuadrilla: {r['cuadrilla'] or '—'}")
    return "\n".join(out)


def bloque_ejecucion(ref, nov):
    out = [f"✅ *Trámite EJECUTADO* — {ref}", ""]
    if nov["eje_ges"]:
        out.append(f"🛠️ *GESTIÓN REGISTRADA* ({len(nov['eje_ges'])} nuevo(s))")
        for r in nov["eje_ges"]:
            post = es_si(r["se_posterga"])
            out.append(
                f"  • Trámite `{r['numero_tramite']}` · solicitud `{r['numero_solicitud']}`\n"
                f"    Cliente: {r['cliente']} · cuadrilla: {r['cuadrilla'] or '—'}\n"
                f"    Tipo: {r['tipo_solicitud'] or '—'} · fecha ejecución: {r['fecha_ejecucion'] or '—'}\n"
                f"    Cuenta: {r['cuenta_contrato'] or '—'} · se posterga: {'SI' if post else 'No'}"
                + (f"\n    ⚠️ Motivo de no ejecución: {r['motivo_no_ejecucion'] or '—'}"
                   f"\n    ⚠️ Detalle: {r['detalle_no_ejecucion'] or '—'}" if post else "")
                + (f"\n    Observación: {str(r['observacion_gestion'] or '—')[:200]}"
                   if r["observacion_gestion"] else ""))
    if nov["eje_solo_rc"]:
        out.append("")
        out.append(f"🚚 *MARCADO EJECUTADO EN CAMPO* ({len(nov['eje_solo_rc'])} nuevo(s))")
        for r in nov["eje_solo_rc"]:
            out.append(
                f"  • Trámite `{r['numero_tramite']}` · solicitud `{r['numero_solicitud']}`\n"
                f"    Cliente: {r['cliente']} · cuadrilla: {r['cuadrilla'] or '—'}\n"
                f"    Fecha ejecución: {r['fecha_ejecucion'] or '—'} · cuenta: {r['cuenta_contrato'] or '—'}")
    return "\n".join(out)


SEPARADOR = "\n\n" + "─" * 34 + "\n\n"
PIE = "\n\n_(Detectado por el vigía del operativo — cédulas y cuentas de contrato. Avisa una sola vez por trámite y etapa.)_"


# ── Estado ──────────────────────────────────────────────────────────────────

def cargar_estado():
    try:
        with open(ESTADO) as fh:
            est = json.load(fh)
        if not isinstance(est, dict):
            raise ValueError("estado no es un objeto JSON")
    except Exception:
        est = {}
    est.setdefault("tramites_avisados", [])
    est.setdefault("tramites_ejecutados", [])
    # Aviso si aparece el formato viejo (por cédula): el estado debe migrarse,
    # si no los trámites ya vistos se anunciarían otra vez.
    if est.get("avisadas") or est.get("ejecutadas"):
        print("⚠️ Vigía de cédulas: el estado trae el formato VIEJO (por cédula). "
              "Hay que migrarlo a trámites o se repetirán avisos.",
              file=sys.stderr)
    return est


def guardar_estado(est):
    try:
        os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
        est["actualizado"] = datetime.now().isoformat(timespec="seconds")
        for k in ("avisadas", "ejecutadas"):     # formato viejo: se descarta
            est.pop(k, None)
        with open(ESTADO, "w") as fh:
            json.dump(est, fh, indent=2, ensure_ascii=False)
    except Exception as e:  # no romper el aviso por no poder guardar
        print(f"⚠️ (no se pudo guardar el estado: {e})", file=sys.stderr)


def main():
    probando = "--probar" in sys.argv
    cedulas = CEDULAS_PRUEBA if probando else CEDULAS
    if probando:
        print("[PRUEBA: uso cédulas que existen de verdad, no guardo estado]\n")

    # La BD tiene que estar: si no, avisar (un vigía roto no se queda callado)
    ultimo_error = None
    con = None
    for _ in range(3):
        try:
            if not os.path.exists(DB):
                raise FileNotFoundError(f"no existe la BD en {DB}")
            con = conectar()
            con.execute("SELECT COUNT(*) FROM recorrido_cuadrillas").fetchone()
            break
        except Exception as e:
            ultimo_error = e
            con = None
            time.sleep(5)
    if con is None:
        print(f"❌ Vigía de cédulas: no pude leer la BD ({ultimo_error}). "
              f"Revisar el archivo / el servicio. No es un aviso de trámites.")
        sys.exit(1)

    estado = cargar_estado()
    avisados = set(estado.get("tramites_avisados", []))
    ejecutados = set(estado.get("tramites_ejecutados", []))
    bloques, nuevos_cre, nuevos_eje = [], [], []
    # Trámites ya anunciados en ESTA corrida: evita el aviso DUPLICADO cuando una
    # cuenta vigilada pertenece a una cédula que también se vigila (2026-09-30:
    # la cuenta 201020615310 es de la cédula 0910306711, que ya estaba en CEDULAS).
    vistos_cre, vistos_eje = set(), set()
    # Trámites nuevos candidatos a auto-asignación: [{'numero_tramite','clave'}]
    # Se llenan en los bucles; se procesan al final con intentar_autoasignar().
    nuevos_auto = []
    # Trámites ya auto-asignados en corridas anteriores (idempotencia).
    autoasignados = set(estado.get("tramites_autoasignados", []))
    # Auto-asignaciones que FALLARON en corridas anteriores → reintentar (2026-10-16).
    auto_pendientes = estado.get("autoasignaciones_pendientes", []) or []

    # ── Cuentas de contrato PRIMERO ────────────────────────────────────────
    # Van antes que las cédulas a propósito: así, si un trámite cae en una cuenta
    # vigilada, el aviso sale CON el RECORDATORIO (y no con el formato genérico).
    for cuenta, recordatorio in CUENTAS_CONTRATO.items():
        try:
            asign, ejecut = buscar_cuenta(con, cuenta)
        except Exception as e:
            print(f"❌ Vigía: error consultando la cuenta {cuenta}: {e}")
            sys.exit(1)

        if not asign and not ejecut:
            continue                     # todavía no existe: silencio

        nov = novedades(asign, ejecut, avisados | vistos_cre, ejecutados | vistos_eje)
        if nov is None:
            continue                     # nada nuevo: silencio

        partes = []
        if nov["nuevos_creados"]:
            partes += [encabezado_cuenta(cuenta, recordatorio),
                       bloque_creacion(f"cuenta `{cuenta}`", nov)]
            nuevos_cre += nov["nuevos_creados"]
            vistos_cre |= set(nov["nuevos_creados"])
            # Auto-asignación (solo si esta cuenta está en AUTO_ASIGNAR)
            for tr in nov["nuevos_creados"]:
                nuevos_auto.append({"numero_tramite": tr, "clave": f"cuenta:{cuenta}"})
        if nov["nuevos_ejecutados"]:
            partes.append(bloque_ejecucion(f"cuenta `{cuenta}`", nov))
            nuevos_eje += nov["nuevos_ejecutados"]
            vistos_eje |= set(nov["nuevos_ejecutados"])
        bloques.append(SEPARADOR.join(partes))

    for ced in cedulas:
        try:
            asign, ejecut = buscar(con, ced)
        except Exception as e:
            print(f"❌ Vigía de cédulas: error consultando la cédula {ced}: {e}")
            sys.exit(1)

        if not asign and not ejecut:
            continue                     # todavía no existe: silencio

        nov = novedades(asign, ejecut, avisados | vistos_cre, ejecutados | vistos_eje)
        if nov is None:
            continue                     # nada nuevo: silencio

        partes = []
        if nov["nuevos_creados"]:
            rec = RECORDATORIOS_CEDULA.get(ced)
            if rec:
                partes.append(encabezado_cedula(ced, rec))
            partes.append(bloque_creacion(f"cédula `{ced}`", nov))
            nuevos_cre += nov["nuevos_creados"]
            vistos_cre |= set(nov["nuevos_creados"])
            # Auto-asignación (solo si esta cédula está en AUTO_ASIGNAR)
            for tr in nov["nuevos_creados"]:
                nuevos_auto.append({"numero_tramite": tr, "clave": f"cedula:{ced}"})
        if nov["nuevos_ejecutados"]:
            partes.append(bloque_ejecucion(f"cédula `{ced}`", nov))
            nuevos_eje += nov["nuevos_ejecutados"]
            vistos_eje |= set(nov["nuevos_ejecutados"])
        bloques.append(SEPARADOR.join(partes))

    # ── AUTO-ASIGNACIÓN ────────────────────────────────────────────────────
    # Cuando hay trámites nuevos en ítems del mapa AUTO_ASIGNAR, se asignan solos
    # en AFlow (si no tienen ya cuadrilla). Los avisos se anexan al mensaje.
    # Además se REINTENTAN las auto-asignaciones que fallaron en corridas
    # anteriores (2026-10-16): si no, un fallo transitorio de AFlow se perdía
    # para siempre (el trámite ya no vuelve a ser "nuevo").
    # En modo --probar NO se ejecuta nada real.
    lineas_auto, nuevos_asignados, errores_auto, auto_pend_restantes = [], set(), [], []
    if nuevos_auto or auto_pendientes:
        try:
            from vigia_autoasignar import intentar_autoasignar
            lineas_auto, nuevos_asignados, errores_auto, auto_pend_restantes = intentar_autoasignar(
                nuevos_auto, con, autoasignados, pendientes=auto_pendientes,
                dry_run=probando, log=lambda m: print(m, file=sys.stderr))
        except Exception as e:
            lineas_auto = [f"⚠️ *Auto-asignación falló* (error interno): {e}"]
            errores_auto = [str(e)]
            # no perder los pendientes si el módulo revienta
            auto_pend_restantes = list(auto_pendientes)

    if bloques or lineas_auto:
        cuerpo = SEPARADOR.join(bloques) if bloques else ""
        if lineas_auto:
            auto_txt = "\n".join(lineas_auto)
            cuerpo = (cuerpo + "\n\n" + auto_txt) if cuerpo else auto_txt
        print(cuerpo + PIE)
        if not probando:
            estado["tramites_avisados"] = sorted(avisados | set(nuevos_cre))
            estado["tramites_ejecutados"] = sorted(ejecutados | set(nuevos_eje))
            estado["tramites_autoasignados"] = sorted(autoasignados | nuevos_asignados)
            estado["autoasignaciones_pendientes"] = auto_pend_restantes
            guardar_estado(estado)
    elif auto_pendientes and not auto_pend_restantes and not probando:
        # No hay avisos pero sí se resolvieron pendientes sin generar texto:
        # persistir la limpieza para no reintentar en vano la próxima vez.
        estado["autoasignaciones_pendientes"] = []
        guardar_estado(estado)
    # Si no hay bloques: stdout vacío a propósito (silencio total)


if __name__ == "__main__":
    main()
