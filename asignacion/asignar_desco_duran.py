#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DESCO DURÁN → CARLOS MIRANDA: auto-asignación horaria (creado 2026-10-23).

PEDIDO DE MIGUEL: "asignar automáticamente trámites que sean DESC o
DESCONEXIONES DEFINITIVAS que sean de DURAN a la cuadrilla de CARLOS MIRANDA",
incluyendo además DIVINO NIÑO y EL RECREO (ampliado el mismo día).

QUÉ ASIGNA
  tipo_solicitud  = 'Desco. pedido del cliente' (34)  ó  'Desco. por mora' (36)
  parroquia       ∈ DURÁN (ELOY ALFARO) · DIVINO NIÑO · EL RECREO
  pendiente       = cuadrilla vacía/NULL  AND  accion_ejecutada ≠ 'SI'

DESTINO: CARLOS MIRANDA MOLESTINA — cédula 0928817774

POR QUÉ ESTAS COLUMNAS Y NO oficina_solicitud
  La oficina 110 cubre DURÁN **y también** DIVINO NIÑO y EL RECREO, pero
  'EL RECREO' y 'DIVINO NIÑO' son parroquias propias en la BD. Filtrar por
  oficina metería trámites de otras parroquias y dejaría fuera parte del
  objetivo. La parroquia es la unidad correcta (verificado 2026-10-23).

HORARIO
  El ACIIS descarga y carga reportes cada hora en punto (cron Hermes job
  7d1c34988efe, '0 0,6-23 * * *'). Este script debe correr a los :35 —
  después de la carga, antes de que el vigía de cédulas (:30) haga su ronda.
  Cron Hermes: '35 0,6-23 * * *'.

SALIDA: SILENCIO EN ÉXITO.
  - Sin pendientes      → no imprime nada, exit 0.
  - Pendientes asignados→ imprime un resumen de 1-3 líneas (Miguel quiere
                          saber qué se movió), exit 0.
  - Fallo real          → imprime el error, exit 1 (el cron avisa).

USO
  asignar_desco_duran.py --probar     # dry-run: lista, NO toca AFlow
  asignar_desco_duran.py              # asigna de verdad y verifica

⚠️ CREDENCIALES: el cron `no_agent` NO trae ACIIS_USER/ACIIS_PASS en el
entorno. Si no las carga este script, el planificador sale con exit 5 y el
mensaje apunta al iframe en vez de a la causa (bug ya visto el 2026-10-20).
Por eso `_cargar_env()` corre ANTES de cualquier otra cosa.
"""
import os, sys, subprocess, sqlite3, argparse, datetime, json

# ── Configuración ─────────────────────────────────────────────────────────────
DB = os.environ.get(
    "GESTION_DB",
    "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db")
LOTE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "aflow_lote.py")
FRASE = "CONFIRMO LOTE"
TIMEOUT = 600          # el lote hace login + N trámites; 80 s cada uno

# 🔴 Intérprete EXPLÍCITO para lanzar aflow_lote.py (2026-10-23).
# NO usar sys.executable: cuando el cron `no_agent` ejecuta este script, el
# proceso padre NO es /usr/bin/python3, y el intérprete resultante no tiene
# Playwright → `ModuleNotFoundError: No module named 'playwright'` y la
# asignación NO ocurre (pasó en la corrida de las 10:35, trámite 345197587).
# /usr/bin/python3 es el único que tiene playwright en esta tablet (verificado).
PY_LOTE = "/usr/bin/python3"

CUADRILLA = "CARLOS MIRANDA MOLESTINA"
CEDULA = "0928817774"

PARROQUIAS = ("DURÁN (ELOY FARO)",)  # placeholder, se redefine abajo
PARROQUIAS = ("DURÁN (ELOY ALFARO)", "DIVINO NIÑO", "EL RECREO")

# Códigos de tipo de orden que son desconexión
TIPOS_DESCO = ("Desco. pedido del cliente", "Desco. por mora")


def _cargar_env():
    """Carga ACIIS_USER / ACIIS_PASS desde ~/.hermes/.env sin pisar el entorno."""
    faltan = [k for k in ("ACIIS_USER", "ACIIS_PASS") if not os.environ.get(k)]
    if not faltan:
        return
    for p in ("/root/.hermes/.env", os.path.expanduser("~/.hermes/.env")):
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith("#") or "=" not in linea:
                        continue
                    k, v = linea.split("=", 1)
                    k = k.strip()
                    if k in faltan and not os.environ.get(k):
                        os.environ[k] = v.strip().strip('"').strip("'")
        except Exception as e:
            print(f"AVISO: no pude leer {p}: {e}", file=sys.stderr)
    if not os.environ.get("ACIIS_PASS"):
        print("ERROR: ACIIS_PASS vacía tras cargar .env — el planificador fallaría "
              "con exit 5 disfrazado de 'iframe no apareció'", file=sys.stderr)
        sys.exit(1)


def pendientes():
    """DESCO pendientes en las 3 parroquias. Devuelve [(tramite, tipo, parroquia)]."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    try:
        ph = ",".join("?" * len(PARROQUIAS))
        sql = f"""
            SELECT numero_tramite, tipo_solicitud, parroquia
              FROM recorrido_cuadrillas
             WHERE tipo_solicitud IN ({",".join("?" * len(TIPOS_DESCO))})
               AND parroquia IN ({ph})
               AND (cuadrilla IS NULL OR TRIM(cuadrilla) = '')
               AND UPPER(COALESCE(accion_ejecutada,'')) NOT IN ('SI','SÍ','1','TRUE')
             ORDER BY fecha_solicitud, numero_tramite
        """
        filas = con.execute(sql, (*TIPOS_DESCO, *PARROQUIAS)).fetchall()
    finally:
        con.close()
    # Guardarraíl extra: descartar lo que ya esté ejecutado/liberado en gestion_tramites
    out = []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    try:
        for tr, tipo, parr in filas:
            r = con.execute(
                "SELECT estado FROM gestion_tramites WHERE numero_tramite=? LIMIT 1",
                (str(tr),)).fetchone()
            est = (r[0] or "").upper() if r else ""
            if "LIBERADO" in est or "CERRADO" in est:
                continue
            out.append((str(tr), tipo, parr))
    finally:
        con.close()
    return out


# ── Memoria local de trámites ya asignados por este script ────────────────────
#
# POR QUÉ EXISTE (2026-10-23): la BD es una foto de AReports (ventana móvil de
# 10 días), NO de AFlow. Hay trámites que salen de AReports pero siguen vivos y
# pendientes en AFlow; en la BD quedan con cuadrilla=NULL y accion_ejecutada='NO'
# para siempre. Sin esta marca, el script los volvería a asignar en CADA corrida
# (pasó con 345194563: se reasignó a las 09:36 y otra vez a las 09:50, gastando
# 80 s de sesión cada vez y con riesgo de pisar un cambio manual).
#
# Decisión de Miguel (2026-10-23): recolección SOLO por BD cada hora, sin
# barrido del portal. Esta marca es la compensación mínima de ese diseño.
#
# La marca es de UN SOLO SENTIDO: si la BD algún día muestra el trámite con
# otra cuadrilla distinta u otro estado, el trámite vuelve a fluir con normalidad
# (la marca solo evita REINTENTAR lo que este script ya asignó).
MARCA = os.environ.get(
    "DESCO_STATE",
    "/root/.hermes/state/desco_duran_asignados.json")


def _leer_marca():
    try:
        with open(MARCA, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _guardar_marca(d):
    try:
        os.makedirs(os.path.dirname(MARCA), exist_ok=True)
        tmp = MARCA + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, MARCA)
    except Exception as e:
        print(f"AVISO: no pude guardar la marca: {e}", file=sys.stderr)


def asignar(tramites):
    """Manda el lote a AFlow. Devuelve (ok_list, fallos_list)."""
    cmd = [PY_LOTE, LOTE]
    for tr in tramites:
        cmd += ["--item", f"{tr}:{CEDULA}"]
    cmd += ["--ejecutar", "--si", FRASE]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=TIMEOUT, env=dict(os.environ))
    salida = (p.stdout or "") + (p.stderr or "")
    ok, fallos = [], []
    for linea in (p.stdout or "").splitlines():
        linea = linea.strip()
        if linea.startswith("OK "):
            ok.append(linea.split()[1])
        elif linea.startswith(("PEND ", "SKIP ")):
            partes = linea.split(None, 2)
            fallos.append((partes[1], partes[2] if len(partes) > 2 else ""))
    if p.returncode != 0 and not fallos and not ok:
        fallos.append(("-", f"exit {p.returncode}: {salida.strip()[-300:]}"))
    return ok, fallos, salida


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar", action="store_true",
                    help="dry-run: lista lo que asignaría, no toca AFlow")
    a = ap.parse_args()

    _cargar_env()
    pend = pendientes()
    ahora = datetime.datetime.now().strftime("%d-%b %H:%M")

    # Filtrar los que este script ya asignó (la BD seguirá diciendo NULL porque
    # AReports dejó de traerlos, pero el portal ya los tiene con dueño).
    marca = _leer_marca()
    ya_asignados = [t for t, _, _ in pend if t in marca]
    pend = [p for p in pend if p[0] not in marca]

    if not pend:
        if a.probar:
            extra = f" ({len(ya_asignados)} ya asignado(s) por este script)" if ya_asignados else ""
            print(f"[DRY-RUN] {ahora}: sin DESCO pendientes nuevos en las 3 parroquias{extra}.")
        return 0

    if a.probar:
        print(f"[DRY-RUN] {ahora}: {len(pend)} DESCO pendiente(s) → {CUADRILLA} ({CEDULA})")
        for tr, tipo, parr in pend:
            print(f"   · {tr}  {tipo}  [{parr}]")
        if ya_asignados:
            print(f"[DRY-RUN] omitidos por ya asignados: {', '.join(ya_asignados)}")
        print("[DRY-RUN] no se tocó AFlow.")
        return 0

    ok, fallos, salida = asignar([t for t, _, _ in pend])
    if ok:
        print(f"🔌 DESCO→{CUADRILLA}: {len(ok)} asignado(s) — {ahora}")
        for t in ok:
            parr = next((p for n, _, p in pend if n == t), "")
            print(f"   ✅ {t} [{parr}]")
            marca[t] = datetime.datetime.now().isoformat(timespec="seconds")
        _guardar_marca(marca)
    if fallos:
        print(f"⚠️ DESCO→{CUADRILLA}: {len(fallos)} con problema — {ahora}")
        for t, mot in fallos:
            print(f"   ⚠️ {t}: {mot[:160]}")
        if not ok:
            print(f"--- salida del lote (cola) ---\n{salida.strip()[-500:]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
