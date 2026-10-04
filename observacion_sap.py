#!/usr/bin/env python3
# ==========================================================================
# observacion_sap.py  —  Extrae la OBSERVACIÓN de una orden SAP (CNEL)
# --------------------------------------------------------------------------
# Uso:   /usr/bin/python3 observacion_sap.py <nro_orden>
# Salida: una línea con el marcador @@OBS@@ seguido de JSON.
#         Códigos: 0 = ok · 2 = no está en la bandeja · 1 = error.
#         Con OBS_TIMING=1 imprime marcas de tiempo a stderr.
#
# Mecánica VERIFICADA (2026-10-04) sobre el SAP de CNEL:
#   1. Login.
#   2. Clic real sobre el label VISIBLE 'No. Orden' -> aparece un input
#      '<algo>-menu-filter-tf' (el id VARÍA por pestaña: column0 / column12…).
#   3. Escribir el número + Enter -> la grilla queda con esa única orden.
#   4. Si no aparece -> se prueba la OTRA pestaña. Si tampoco -> no existe.
#   5. Clic en el icono 'ver' (role=img aria-label=display) -> VER ORDEN ->
#      leer los campos #VO*-inner.
#
# OPTIMIZACIONES:
#   A) PISTA desde la BD local (ordenes_sap.estado) para elegir la pestaña
#      primero. La BD se refresca ~cada 3 h: es SOLO pista, con fallback a la
#      otra pestaña. Nunca empeora.
#   B) ESPERAS POR CONDICIÓN en vez de sleeps fijos.
#   M) MODO RÁPIDO `--single-process`: baja el navegador de ~516 MB/4 procesos a
#      ~305 MB/1 proceso (y algo más rápido). Como puede ser inestable en apps
#      pesadas, si el MODO RÁPIDO falla se REINTENTA en modo normal. Medido
#      2026-10-04 (3/3 OK). Banderas extra para calibrar: OBS_EXTRA_ARGS="...".
#
# TRAMPAS (no romper):
#   - El encabezado visible es un CLON: IDs DUPLICADOS. Elegir SIEMPRE el
#     VISIBLE (offsetParent != null y width>0).
#   - El filtro se abre con page.mouse.click sobre la caja del label, NO con
#     .click() por JS.
#   - Los campos del detalle son <input>: leer .value.
# ==========================================================================
import sys, os, json, time, sqlite3, subprocess, signal
from datetime import datetime

SAP_URL = "https://sapgw.redenergia.gob.ec:8200/sap/bc/ui5_ui5/sap/zord/index.html?sap-language=ES"
USER = "0952050581"
PASS = "Joel7451595"
DB_PATH = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
BASE_ARGS = ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu',
             '--disable-dev-shm-usage', '--no-zygote', '--renderer-process-limit=1']
RAPIDO_ARGS = ['--single-process', '--blink-settings=imagesEnabled=false']
MARCA = "@@OBS@@"
POR = ("POR_ASIGNAR", "#SupNavAsig")
EN = ("EN_TRATAMIENTO", "#SupNavTrat")

_T0 = time.time()


def _tick(etq):
    if os.environ.get("OBS_TIMING"):
        print(f"[timing] {etq}: {time.time() - _T0:.1f}s", file=sys.stderr, flush=True)


def limpiar_huerfanos():
    """Mata solo navegadores huérfanos (ppid 1). Nunca toca uno con padre vivo."""
    try:
        ps = subprocess.run(['ps', '-eo', 'pid=,ppid=,args='], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return
    for linea in ps.splitlines():
        p = linea.split(None, 2)
        if len(p) < 3:
            continue
        pid, ppid, args = p
        if ppid == '1' and 'chromium_headless_shell' in args:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass


def pista_pestana(num):
    """Pista desde la BD local (~3 h). 'EN_TRATAMIENTO' si figura ahí; si no, None.
    NUNCA es la verdad: solo ordena cuál pestaña mirar primero."""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        r = con.execute(
            "SELECT estado FROM ordenes_sap WHERE numero_solicitud=? OR numero_orden_sap=? LIMIT 1",
            (num, num.zfill(12))).fetchone()
        con.close()
        return r[0] if r else None
    except Exception:
        return None


JS_LABEL = """() => {
  const c=[...document.querySelectorAll('label')].filter(e=>(e.textContent||'').trim()==='No. Orden');
  const v=c.find(e=>e.offsetParent!==null && e.getBoundingClientRect().width>0);
  if(!v) return null; const r=v.getBoundingClientRect();
  return {x:r.x+r.width/2, y:r.y+r.height/2};
}"""
JS_GRID_LISTA = """() => [...document.querySelectorAll('label')]
  .some(e => (e.textContent||'').trim()==='No. Orden'
        && e.offsetParent!==null && e.getBoundingClientRect().width>0)"""
JS_FILTRO = """() => {
  const c=[...document.querySelectorAll('input')].filter(e=>/-menu-filter-tf$/.test(e.id)
      && e.offsetParent!==null && e.getBoundingClientRect().width>0);
  if(!c.length) return null; const r=c[0].getBoundingClientRect();
  return {x:r.x+r.width/2, y:r.y+r.height/2};
}"""
JS_VER = """() => {
  const c=[...document.querySelectorAll('[role=img][aria-label=display]')]
      .filter(e=>e.offsetParent!==null && e.getBoundingClientRect().width>0);
  if(!c.length) return null; const r=c[0].getBoundingClientRect();
  return {x:r.x+r.width/2, y:r.y+r.height/2};
}"""
JS_FILAS = """() => { const o=[];
  for (const r of document.querySelectorAll('[role=row]')){
    const c=r.querySelectorAll('[role=gridcell]');
    if(c.length>=11){ const n=(c[1].textContent||'').trim(); if(/^\\d+$/.test(n)) o.push(n);} }
  return o; }"""
JS_VO = """() => {
  const g=id=>{const e=document.getElementById(id); return e?e.value:null;};
  return {orden:g('VOAufnr-inner'), cliente:g('VOSolic-inner'), cedula:g('VOCedula-inner'),
          dir:g('VOCalleNro-inner'), cel:g('VOTelfijoCel-inner'), obs:g('VOObservaciones-inner')};
}"""
JS_HAY_VO = "() => !!document.getElementById('VOObservaciones-inner')"
JS_LOGIN_OK = "() => !!document.querySelector('#SupNavAsig')"


class SinUI(Exception):
    """La interfaz del SAP no cargó (posible inestabilidad del modo rápido)."""


def _clic(page, js):
    b = page.evaluate(js)
    if not b:
        return False
    page.mouse.click(b["x"], b["y"])
    return True


def esperar(page, js_bool, timeout, intervalo=0.3):
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            if page.evaluate(js_bool):
                return True
        except Exception:
            pass
        time.sleep(intervalo)
    return False


def esperar_filtro(page, num, timeout=18):
    """Espera a que la grilla quede 'asentada' tras filtrar (todas la pedida, o
    cero, y estable 2 lecturas seguidas)."""
    fin = time.time() + timeout
    prev = None
    estable = 0
    while time.time() < fin:
        try:
            filas = page.evaluate(JS_FILAS)
        except Exception:
            filas = None
        if filas is not None and (len(filas) == 0 or all(f.lstrip('0') == num for f in filas)):
            if filas == prev:
                estable += 1
                if estable >= 2:
                    return filas
            else:
                estable = 0
            prev = filas
        else:
            prev = filas
            estable = 0
        time.sleep(0.4)
    try:
        return page.evaluate(JS_FILAS)
    except Exception:
        return []


def buscar_en_pestana(page, nombre, tab, num):
    page.query_selector(tab).click()
    if not esperar(page, JS_GRID_LISTA, timeout=25):
        raise SinUI(f"grilla no cargó en {nombre}")
    _tick(f"[{nombre}] grilla lista")
    if not _clic(page, JS_LABEL):
        return None
    if not esperar(page, JS_FILTRO, timeout=12):
        return None
    _tick(f"[{nombre}] filtro abierto")
    fb = page.evaluate(JS_FILTRO)
    page.mouse.click(fb["x"], fb["y"])
    time.sleep(0.3)
    page.keyboard.type(str(num))
    page.keyboard.press("Enter")
    filas = esperar_filtro(page, num)
    _tick(f"[{nombre}] filtrado -> {len(filas)} fila(s)")
    if not any(f.lstrip('0') == num for f in filas):
        return None
    if not _clic(page, JS_VER):
        return None
    esperar(page, JS_HAY_VO, timeout=25)
    datos = page.evaluate(JS_VO)
    _tick(f"[{nombre}] VER ORDEN leída")
    return datos


def _extraer(num, args):
    """Corre el flujo completo con `args`. Devuelve (datos|None, tab|None).
    Lanza SinUI si la interfaz no cargó (fallo de infraestructura, no 'ausente')."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=args)
        try:
            ctx = b.new_context(viewport={'width': 1920, 'height': 1080},
                                locale='es-EC', ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(SAP_URL, timeout=60000, wait_until='load')
            page.wait_for_selector('#usuario-inner', timeout=30000)
            _tick("página SAP cargada")
            page.fill('#usuario-inner', USER)
            page.fill('#contrasena-inner', PASS)
            page.click('button')
            if not esperar(page, JS_LOGIN_OK, timeout=35):
                raise SinUI("login no completó")
            _tick("login hecho")

            pista = pista_pestana(num)
            orden = [EN, POR] if pista == "EN_TRATAMIENTO" else [POR, EN]
            _tick(f"pista BD={pista or 'sin datos'} -> {[o[0] for o in orden]}")
            for nombre, t in orden:
                datos = buscar_en_pestana(page, nombre, t, num)
                if datos:
                    return datos, nombre
            return None, None
        finally:
            try:
                b.close()
            except Exception:
                pass


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(MARCA, json.dumps({"ok": False, "motivo": "sin_numero"}))
        return 1
    raw = sys.argv[1].strip()
    num = raw.lstrip('0') or raw

    extra = os.environ.get("OBS_EXTRA_ARGS")
    if extra:
        intentos = [("personalizado", BASE_ARGS + extra.split())]
    else:
        intentos = [("rápido", BASE_ARGS + RAPIDO_ARGS), ("normal", list(BASE_ARGS))]

    limpiar_huerfanos()
    datos = tab = None
    for etq, args in intentos:
        try:
            datos, tab = _extraer(num, args)
            if etq != "normal":
                _tick(f"modo {etq}: OK")
            break
        except Exception as e:
            _tick(f"modo {etq} falló: {str(e)[:120]}")
            datos = tab = None
            if etq == "normal" or len(intentos) == 1:
                limpiar_huerfanos()
                print(MARCA, json.dumps({"ok": False, "motivo": "error", "detalle": str(e)[:200]}))
                return 1
    limpiar_huerfanos()

    if not datos:
        _tick("fin (sin resultado)")
        print(MARCA, json.dumps({"ok": False, "motivo": "no_existe", "num": num}))
        return 2

    _tick("fin")
    print(MARCA, json.dumps({"ok": True, "num": num, "tab": tab, **datos,
                             "capturado_en": datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
                            ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
