#!/usr/bin/env python3
"""
Descarga los 4 reportes ACIIS y/o los carga en gestion_medidores.db.

Modos:
  Por defecto: descarga + carga (todo en uno)
  --download-only: solo descarga los Excel (bot puede seguir funcionando)
  --load-only: solo carga los Excel ya descargados (bot debe estar detenido)

Variables de entorno requeridas:
    ACIIS_USER, ACIIS_PASS

Variables opcionales:
    DIAS_VENTANA (default: 5) — días hacia atrás a descargar
    OUT_DIR (default: /tmp/aciis_reports)
"""
import requests
import json
import time
import os
import sys
import subprocess
from datetime import datetime, timedelta

# ── CONFIG ──────────────────────────────────────────────────────────
PORTAL_BASE = os.environ.get("ACIIS_PORTAL", "https://amobile.altura.systems")
USERNAME = os.environ.get("ACIIS_USER", "")
PASSWORD = os.environ.get("ACIIS_PASS", "")
DIAS_VENTANA = int(os.environ.get("DIAS_VENTANA", "5"))
OUT_DIR = os.environ.get("OUT_DIR", "/tmp/aciis_reports")
BASE_DIR = "/data/data/com.termux/files/home/salchipapabot"
DB_PATH = os.path.join(BASE_DIR, "gestion_medidores.db")

# Report definitions: (id, name, extra_params)
REPORTS = [
    ("915", "gestion", {}),
    ("911", "recorrido", {"PAR_POR": "gc.fecha_analisis"}),
    ("932", "retirado", {}),
    ("942", "fotos", {}),
]

# ── FECHA ───────────────────────────────────────────────────────────
now = datetime.now()
fecha_hasta = now.strftime("%d/%m/%Y")
fecha_desde = (now - timedelta(days=DIAS_VENTANA)).strftime("%d/%m/%Y")


# ── FUNCIONES ───────────────────────────────────────────────────────
def log(msg):
    ts = now.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def login():
    """Two-factor login: portal → AReports"""
    s = requests.Session()

    s.post(f"{PORTAL_BASE}/aportal/accesoUsuario", data={
        "accion": "login", "v": "1",
        "u": USERNAME, "c": PASSWORD,
        "action": "INICIAR SESIÓN"
    }, allow_redirects=True)

    r = s.get(f"{PORTAL_BASE}/areports/u/0/Wizard?idw=31172", allow_redirects=True)
    login_url = r.url[:r.url.index("/Login.jsp")]

    s.post(f"{login_url}/accesoUsuario", data={
        "i": USERNAME, "c": PASSWORD, "token": ""
    }, allow_redirects=True)

    return s, login_url


def download_excel(s, login_url, report_id, name, extra_params):
    """Genera reporte y descarga Excel completo."""
    s.post(f"{login_url}/htmlParametro", data={
        "id_parametro": "PAR_UNI", "id_reporte": report_id, "id": report_id
    })
    s.post(f"{login_url}/htmlParametro", data={
        "id_parametro": "PAR_CONTRA", "id_reporte": report_id,
        "id": report_id, "PAR_UNI": "09"
    })

    params = {
        "id_reporte": report_id,
        "PAR_UNI": "09",
        "PAR_CONTRA": "16697",
        "FEC_INI": fecha_desde,
        "FEC_FIN": fecha_hasta,
        "fecha": now.strftime("%d/%m/%Y"),
    }
    params.update(extra_params)

    r = s.post(f"{login_url}/jsGenerate", data=params)
    resp = json.loads(r.text)
    idc = resp["idConsulta"]
    log(f"  Reporte generado: idConsulta={idc}")

    for _ in range(20):
        r = s.post(f"{login_url}/jsRequest", data={"idParametro": f"c{idc}"})
        poll = json.loads(r.text)
        if poll.get("parametroStatus") == 200:
            break
        time.sleep(2)

    r = s.post(f"{login_url}/jsGenerate", data={
        "id_consulta": idc, "accion": "excel",
        "fecha": now.strftime("%d/%m/%Y"),
    })
    resp2 = json.loads(r.text)
    excel_idc = resp2["idConsulta"]

    for _ in range(30):
        r = s.post(f"{login_url}/jsRequest", data={"idParametro": f"e{excel_idc}"})
        poll = json.loads(r.text)
        st = poll.get("parametroStatus", poll.get("status"))
        if st == 200:
            break
        elif st == 500:
            raise Exception(f"Excel error: {poll.get('error')}")
        time.sleep(3)

    r = s.get(f"{login_url}/consultaExcel", params={
        "id_consulta": excel_idc, "n": name.capitalize()
    })

    fpath = os.path.join(OUT_DIR, f"{name}.xlsx")
    with open(fpath, "wb") as f:
        f.write(r.content)
    log(f"  Descargado: {fpath} ({len(r.content):,} bytes)")
    return fpath


def cargar_a_bd(script_name, excel_path):
    """Ejecuta el script de carga correspondiente."""
    script_path = os.path.join(BASE_DIR, script_name)
    if not os.path.exists(excel_path):
        log(f"  ⚠️  Archivo no encontrado: {excel_path}")
        return None, None
    # Usar subprocess.call en vez de run(capture_output) para evitar
    # que el pipe de subprocess se cuelgue en PRoot + Termux
    rc = subprocess.call(
        [sys.executable, script_path, excel_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=120
    )
    if rc != 0:
        log(f"  ❌ Error (código {rc}) en {script_name}")
        return None, None
    return None, None


def descargar_reportes():
    """Fase 1: solo descarga los Excel (bot funcionando)."""
    log("=" * 50)
    log("📥 FASE 1 — Descarga de Reportes")
    log(f"   Periodo: {fecha_desde} → {fecha_hasta} ({DIAS_VENTANA} días)")
    log("=" * 50)

    if not USERNAME or not PASSWORD:
        log("❌ Faltan ACIIS_USER / ACIIS_PASS")
        return False

    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        s, login_url = login()
        log(f"✅ Login OK")
    except Exception as e:
        log(f"❌ Error de login: {e}")
        return False

    ok = True
    for rid, name, extra in REPORTS:
        log(f"\n📊 {name.title()} ({rid})")
        try:
            download_excel(s, login_url, rid, name, extra)
        except Exception as e:
            log(f"  ❌ Falló descarga: {e}")
            ok = False

    return ok


def cargar_reportes():
    """Fase 2: solo carga los Excel a la BD (bot detenido)."""
    log("=" * 50)
    log("💾 FASE 2 — Carga a Base de Datos")
    log("=" * 50)

    if not os.path.exists(OUT_DIR):
        log(f"❌ Directorio de descargas no existe: {OUT_DIR}")
        return

    resultados = {}

    for _, name, _ in REPORTS:
        fpath = os.path.join(OUT_DIR, f"{name}.xlsx")
        log(f"\n📊 {name.title()}")
        try:
            nuevos, actualizados = cargar_a_bd(f"cargar_{name}_sqlite.py", fpath)
            resultados[name] = {"nuevos": nuevos, "actualizados": actualizados, "ok": True}
        except Exception as e:
            log(f"  ❌ Falló carga: {e}")
            resultados[name] = {"ok": False, "error": str(e)[:100]}

    # RESUMEN
    log("\n" + "=" * 50)
    log("📋 RESUMEN DE CARGA")
    log("=" * 50)
    for name, res in resultados.items():
        if res.get("ok"):
            n = res["nuevos"] if res["nuevos"] is not None else "?"
            a = res["actualizados"] if res["actualizados"] is not None else "?"
            log(f"   ✅ {name.title()}: +{n} nuevos, 🔄 {a} actualizados")
        else:
            log(f"   ❌ {name.title()}: Error - {res.get('error', 'desconocido')}")

    # Verificar BD
    import sqlite3
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        for tabla in ["gestion_tramites", "recorrido_cuadrillas", "materiales_tramite", "gestion_fotos"]:
            cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
            total = cursor.fetchone()[0]
            log(f"   📦 {tabla}: {total:,} registros")
        conn.close()
    except Exception as e:
        log(f"   ❌ No se pudo leer BD: {e}")

    log("=" * 50)
    log("✅ Carga completada")


# ── MAIN ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    modo_descarga = "--download-only" in sys.argv
    modo_carga = "--load-only" in sys.argv

    if modo_descarga and modo_carga:
        log("❌ Usa --download-only O --load-only, no ambos")
        sys.exit(1)
    elif modo_descarga:
        ok = descargar_reportes()
        sys.exit(0 if ok else 1)
    elif modo_carga:
        cargar_reportes()
        sys.exit(0)
    else:
        # Modo completo (original): descarga + carga
        ok = descargar_reportes()
        if ok:
            cargar_reportes()
        sys.exit(0 if ok else 1)
