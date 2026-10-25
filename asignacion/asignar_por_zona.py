#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMERGENCIAS POR ZONA → rotación semanal.

PEDIDO DE MIGUEL (2026-10-23): asignar emergencias según la zona geográfica
donde caen las coordenadas del trámite, usando la rotación semanal cargada en
la tabla `rotacion_semanal`.

DIFERENCIA CON asignar_emergencias_duran.py
  · aquel usa UNA cuadrilla fija y filtra por parroquia (DURÁN/DIVINO NIÑO/EL RECREO)
  · este resuelve la cuadrilla por POINT-IN-POLYGON + rotación del día

FLUJO
  1. Lee emergencias sin cuadrilla en las 3 zonas
  2. Para cada trámite: (lat,lon) → zona (point-in-polygon) → cuadrilla del día
  3. Agrupa por cuadrilla y lanza UN lote por cuadrilla
  4. Verifica y marca anti-reintento

CADUCIDAD: sin rotación cargada para hoy, el script sale silencioso.
No hay fecha hardcodeada: la vigencia la manda la tabla `rotacion_semanal`.

USO
  asignar_por_zona.py --probar    # dry-run: muestra a quién iría cada trámite
  asignar_por_zona.py             # asigna y verifica
"""
import os, sys, subprocess, sqlite3, argparse, datetime, json
from collections import defaultdict

# Rutas relativas al repo: el script vive en <repo>/asignacion/.
# HERMES_SCRIPTS permite usarlo desde la instalación viva (~/.hermes/scripts)
# sin copiar; por defecto resuelve junto a este archivo.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HERMES_SCRIPTS", _HERE))
from zonas import Zonas, semana_iso

# ── Configuración ─────────────────────────────────────────────────────────────
DB = os.environ.get(
    "GESTION_DB",
    "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db")
LOTE = os.path.join(_HERE, "tools", "aflow_lote.py")
FRASE = "CONFIRMO LOTE"
TIMEOUT = 600

# Intérprete EXPLÍCITO: bajo cron `no_agent` sys.executable no tiene Playwright.
PY_LOTE = "/usr/bin/python3"

# ── Turnos (definidos por Miguel 2026-10-23) ─────────────────────────────────
# mañana: 06:00 a 16:00 · tarde: 16:00 a 23:59 · 00:00-05:59 sin turno.
TURNO_MANANA = "manana"
TURNO_TARDE = "tarde"
HORA_INICIO_MANANA = 6
HORA_INICIO_TARDE = 16

MOTIVO_EMERGENCIA = "ORDEN EMERGENCIAS"

# Cuadrilla → cédula. Fuente ÚNICA: vigia_autoasignar.CUADRILLA_CEDULA.
MARCA = os.environ.get(
    "ASIGNACION_STATE",
    "/root/.hermes/state/emergencias_zona_asignados.json")


def _cargar_env():
    """ACIIS_USER/ACIIS_PASS desde ~/.hermes/.env — el cron no_agent no los trae."""
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
        print("ERROR: ACIIS_PASS vacía tras cargar .env", file=sys.stderr)
        sys.exit(1)


def _mapa_cedulas():
    """Cuadrilla → cédula, importado del vigía (fuente única de verdad)."""
    try:
        from cuadrillas import CUADRILLA_CEDULA
        return CUADRILLA_CEDULA
    except Exception as e:
        print(f"ERROR: no pude importar CUADRILLA_CEDULA: {e}", file=sys.stderr)
        sys.exit(1)


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


def rotacion_hoy():
    """{zona: cuadrilla} vigente AHORA, según turno.

    TURNOS (definidos por Miguel 2026-10-23):
      mañana -> 06:00 a 16:00
      tarde  -> 16:00 a 23:59
      fuera de ese rango (00:00-05:59) no hay turno activo -> no se asigna.

    Una fila con turno='todo' aplica en cualquier hora.
    Las filas de turno específico solo aplican dentro de su ventana.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    try:
        ahora = datetime.datetime.now()
        hoy = ahora.date().isoformat()
        hora = ahora.hour
        if HORA_INICIO_MANANA <= hora < HORA_INICIO_TARDE:
            turno_actual = TURNO_MANANA
        elif hora >= HORA_INICIO_TARDE:
            turno_actual = TURNO_TARDE
        else:
            return {}
        filas = con.execute("""
            SELECT z.nombre, r.cuadrilla
              FROM rotacion_semanal r
              JOIN zonas_operativas z ON z.id = r.zona_id
             WHERE r.activa = 1 AND r.fecha_desde <= ? AND r.fecha_hasta >= ?
               AND (r.turno = 'todo' OR r.turno = ?)
             ORDER BY z.nombre,
                      CASE r.turno WHEN 'todo' THEN 2 ELSE 1 END,
                      r.prioridad
        """, (hoy, hoy, turno_actual)).fetchall()
    finally:
        con.close()
    out = {}
    for zona, cuad in filas:
        out.setdefault(zona, cuad)
    return out


def emergencias_sin_zona():
    """Emergencias del motivo, sin cuadrilla, con coordenada. [(tr, parroquia, lat, lon)]"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    try:
        filas = con.execute("""
            SELECT numero_tramite, parroquia, lat_cnel, lon_cnel
              FROM recorrido_cuadrillas
             WHERE motivo_solicitud = ?
               AND (cuadrilla IS NULL OR TRIM(cuadrilla) = '')
               AND UPPER(COALESCE(accion_ejecutada,'')) NOT IN ('SI','SÍ','1','TRUE')
             ORDER BY fecha_analisis, numero_tramite
        """, (MOTIVO_EMERGENCIA,)).fetchall()
    finally:
        con.close()

    # Guardarraíl: fuera lo liberado/cerrado, y sin coordenada válida
    out = []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    try:
        for tr, parr, la, lo in filas:
            if la is None or lo is None:
                continue
            r = con.execute("SELECT estado FROM gestion_tramites WHERE numero_tramite=? LIMIT 1",
                            (str(tr),)).fetchone()
            e = (r[0] or "").upper() if r else ""
            if "LIBERADO" in e or "CERRADO" in e:
                continue
            out.append((str(tr), parr, la, lo))
    finally:
        con.close()
    return out


def clasificar(pend, rot, zon):
    """[(tr, parr, zona, cuadrilla)] — solo los que caen en una zona con rotación hoy."""
    asignables, sin_zona, sin_rot = [], [], []
    for tr, parr, la, lo in pend:
        zona = zon.cual(la, lo)
        if not zona:
            sin_zona.append((tr, parr))
            continue
        cuad = rot.get(zona)
        if not cuad:
            sin_rot.append((tr, parr, zona))
            continue
        asignables.append((tr, parr, zona, cuad))
    return asignables, sin_zona, sin_rot


def asignar_lote(tramites, cedula):
    """Lanza aflow_lote para un grupo de trámites de la misma cuadrilla."""
    cmd = [PY_LOTE, LOTE]
    for tr in tramites:
        cmd += ["--item", f"{tr}:{cedula}"]
    cmd += ["--ejecutar", "--si", FRASE]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                       env=dict(os.environ))
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
                    help="dry-run: muestra a quién iría cada trámite, no toca AFlow")
    a = ap.parse_args()

    rot = rotacion_hoy()
    if not rot:
        if a.probar:
            print(f"[DRY-RUN] Sin rotación cargada para hoy ({datetime.date.today()}). "
                  "No hay a quién asignar.")
        return 0

    _cargar_env()
    cedulas = _mapa_cedulas()
    zon = Zonas()
    pend = emergencias_sin_zona()
    ahora = datetime.datetime.now().strftime("%d-%b %H:%M")

    marca = _leer_marca()
    ya = [t for t, _, _, _ in pend if t in marca]
    pend = [p for p in pend if p[0] not in marca]

    asignables, sin_zona, sin_rot = clasificar(pend, rot, zon)

    # Cuadrillas sin cédula conocida → no se pueden asignar
    sin_cedula = [x for x in asignables if not cedulas.get(x[3])]
    asignables = [x for x in asignables if cedulas.get(x[3])]

    if not asignables:
        if a.probar:
            print(f"[DRY-RUN] {ahora}: nada que asignar.")
            print(f"   · rotación hoy: {rot}")
            print(f"   · emergencias sin cuadrilla: {len(pend)}")
            print(f"   · fuera de zona: {len(sin_zona)}")
            print(f"   · en zona sin rotación: {len(sin_rot)}")
            if sin_cedula:
                print(f"   ⚠️ sin cédula conocida: {[x[0] for x in sin_cedula]}")
            if ya:
                print(f"   · ya asignadas por este script: {len(ya)}")
        return 0

    grupos = defaultdict(list)
    for tr, parr, zona, cuad in asignables:
        grupos[cuad].append((tr, parr, zona))

    if a.probar:
        print(f"[DRY-RUN] {ahora}: {len(asignables)} emergencia(s) asignarían")
        print(f"   rotación hoy:")
        for z, c in sorted(rot.items()):
            print(f"      {z:42s} → {c} ({cedulas.get(c, '¿SIN CÉDULA?')})")
        print()
        for cuad, items in grupos.items():
            print(f"   ▶ {cuad} — {len(items)} trámite(s)")
            for tr, parr, zona in items:
                print(f"       · {tr}  [{parr}]  {zona}")
        if sin_zona:
            print(f"\n   fuera de zona ({len(sin_zona)}): " +
                  ", ".join(f"{t}[{p}]" for t, p in sin_zona[:8]))
        if sin_rot:
            print(f"   en zona sin rotación hoy ({len(sin_rot)}): " +
                  ", ".join(f"{t}[{z}]" for t, _, z in sin_rot[:8]))
        if ya:
            print(f"   ya asignadas antes: {len(ya)}")
        print("\n[DRY-RUN] no se tocó AFlow.")
        return 0

    # ── ejecución real ──
    total_ok, total_fallos = 0, []
    for cuad, items in grupos.items():
        ced = cedulas[cuad]
        ok, fallos, salida = asignar_lote([x[0] for x in items], ced)
        if ok:
            total_ok += len(ok)
            print(f"🚨 EMERGENCIA→{cuad}: {len(ok)} asignada(s) — {ahora}")
            for t in ok:
                info = next((x for x in items if x[0] == t), None)
                extra = f"[{info[1]}] {info[2]}" if info else ""
                print(f"   ✅ {t} {extra}")
                marca[t] = datetime.datetime.now().isoformat(timespec="seconds")
            _guardar_marca(marca)
        if fallos:
            total_fallos.extend(fallos)
            print(f"⚠️ EMERGENCIA→{cuad}: {len(fallos)} con problema")
            for t, mot in fallos:
                print(f"   ⚠️ {t}: {mot[:160]}")
            if not ok:
                print(f"--- salida del lote (cola) ---\n{salida.strip()[-500:]}")
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main())
