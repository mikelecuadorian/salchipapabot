#!/usr/bin/env python3
# ==========================================================================
# observacion_sap_daemon.py — Navegador SAP "TIBIO" para /observacion_sap
# --------------------------------------------------------------------------
# Mantiene Chromium + sesión SAP abiertos para responder consultas en ~6-9 s
# (en vez de ~20 s en frío). Se APAGA SOLO tras OBS_WARM_IDLE segundos (600 =
# 10 min) sin uso → la RAM (~300 MB) no queda fija.
#
# No es servicio runsv: lo arranca el bot bajo demanda (wrapper
# observacion_sap_warm.sh). Si Android lo mata o falla, el bot simplemente
# responde por el camino FRÍO (observacion_sap.py) y lo vuelve a arrancar.
#
# Reusa los helpers de observacion_sap.py (mismo directorio).
#
# HTTP en 127.0.0.1:  GET /ping         -> {"ok":true,"warm":true,idle:N}
#                      GET /obs?nro=NNN  -> mismo JSON que el script
# ==========================================================================
import os, sys, json, time, threading, signal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import observacion_sap as obs

PUERTO = int(os.environ.get("OBS_WARM_PORT", "8754"))
IDLE = int(os.environ.get("OBS_WARM_IDLE", "600"))
LOG = "/root/.hermes/logs/observacion_sap_daemon.log"

_ultimo = time.time()
_parar = threading.Event()
_bloqueo = threading.Lock()
_pagina = {"page": None}


def log(msg):
    linea = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(linea + "\n")
    except Exception:
        pass
    if os.environ.get("OBS_TIMING"):
        print(linea, file=sys.stderr, flush=True)


def calentar():
    """Abre Chromium, hace login y deja la grilla (POR ASIGNAR) lista."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True, args=obs.BASE_ARGS + obs.RAPIDO_ARGS)
    ctx = b.new_context(viewport={'width': 1920, 'height': 1080},
                        locale='es-EC', ignore_https_errors=True)
    page = ctx.new_page()
    page.goto(obs.SAP_URL, timeout=60000, wait_until='load')
    page.wait_for_selector('#usuario-inner', timeout=30000)
    page.fill('#usuario-inner', obs.USER)
    page.fill('#contrasena-inner', obs.PASS)
    page.click('button')
    if not obs.esperar(page, obs.JS_LOGIN_OK, timeout=35):
        raise RuntimeError("login no completó")
    page.query_selector('#SupNavAsig').click()
    obs.esperar(page, obs.JS_GRID_LISTA, timeout=25)
    return pw, b, page


def consultar(page, num):
    """Consulta una orden reusando la página tibia. Devuelve (datos|None, tab|None)."""
    pista = obs.pista_pestana(num)
    orden = [obs.EN, obs.POR] if pista == "EN_TRATAMIENTO" else [obs.POR, obs.EN]
    for nombre, tab in orden:
        # clic en la pestaña: además REGRESA desde la vista VER ORDEN a la grilla
        page.query_selector(tab).click()
        if not obs.esperar(page, obs.JS_GRID_LISTA, timeout=25):
            raise obs.SinUI(f"grilla no cargó en {nombre}")
        if not obs._clic(page, obs.JS_LABEL):
            continue
        if not obs.esperar(page, obs.JS_FILTRO, timeout=12):
            continue
        fb = page.evaluate(obs.JS_FILTRO)
        page.mouse.click(fb["x"], fb["y"])
        time.sleep(0.2)
        page.keyboard.press("Control+A")   # reemplaza el filtro anterior
        page.keyboard.type(str(num))
        page.keyboard.press("Enter")
        filas = obs.esperar_filtro(page, num)
        if not any(f.lstrip('0') == num for f in filas):
            continue
        if not obs._clic(page, obs.JS_VER):
            continue
        obs.esperar(page, obs.JS_HAY_VO, timeout=25)
        return page.evaluate(obs.JS_VO), nombre
    return None, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def do_GET(self):
        global _ultimo
        u = urlparse(self.path)
        if u.path == "/ping":
            return self._json({"ok": True, "warm": True, "idle": int(time.time() - _ultimo)})
        if u.path != "/obs":
            return self._json({"ok": False, "motivo": "ruta"}, 404)
        num = (parse_qs(u.query).get("nro") or [""])[0].strip().lstrip("0")
        if not num.isdigit():
            return self._json({"ok": False, "motivo": "sin_numero"}, 400)
        with _bloqueo:
            _ultimo = time.time()
            try:
                datos, tab = consultar(_pagina["page"], num)
            except Exception as e:
                log(f"ERROR consultando {num}: {str(e)[:150]}")
                _parar.set()               # morir: el bot caerá al camino frío
                return self._json({"ok": False, "motivo": "error", "detalle": str(e)[:150]})
            _ultimo = time.time()
        if not datos:
            return self._json({"ok": False, "motivo": "no_existe", "num": num})
        return self._json({"ok": True, "num": num, "tab": tab, **datos,
                           "capturado_en": time.strftime('%Y-%m-%d %H:%M:%S')})


def vigia_idle():
    while not _parar.wait(5):
        if time.time() - _ultimo > IDLE:
            log(f"apagado por inactividad ({IDLE}s)")
            _parar.set()
            return


def _sigterm(*_):
    _parar.set()


def main():
    global _ultimo
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    # 1) Tomar el PUERTO ANTES de calentar: si ya hay un daemon tibio, salir sin
    #    crear un navegador de más (HTTPServer, NO Threading: el handler debe correr
    #    en el hilo principal, único donde Playwright sync funciona).
    try:
        srv = HTTPServer(("127.0.0.1", PUERTO), Handler)
    except OSError as e:
        log(f"puerto {PUERTO} ocupado ({e}); ya hay un daemon tibio, salgo")
        return 0
    srv.timeout = 5

    # 2) Calentar (Chromium + login)
    obs.limpiar_huerfanos()
    try:
        pw, b, page = calentar()
    except Exception as e:
        log(f"no pude calentar: {str(e)[:150]}")
        try:
            srv.server_close()
        except Exception:
            pass
        return 1
    _pagina["page"] = page
    _ultimo = time.time()
    log("daemon tibio LISTO")
    threading.Thread(target=vigia_idle, daemon=True).start()
    try:
        while not _parar.is_set():
            srv.handle_request()
    finally:
        log("cerrando (browser + playwright)")
        for cerrar in (lambda: b.close(), lambda: pw.stop(), obs.limpiar_huerfanos):
            try:
                cerrar()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
